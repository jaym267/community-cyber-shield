"""
main.py — EJMapper FastAPI backend
─────────────────────────────────────────────────────────────────────────────
Runs at http://localhost:8000

Routes:
  GET /api/neighborhood/{zip_code}   → aggregates all data + AI report card
  GET /api/ejscreen                  → EPA EJScreen indicators only
  GET /api/health                    → health check

Start:
  uvicorn main:app --reload
"""

import asyncio
import logging
import os

import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from census_api import zip_to_latlon
from ejscreen_api import get_ejscreen_data
from map_layers import air_quality_geojson, facilities_geojson, green_space_geojson
from mock_data import generate_mock_ejscreen

logger = logging.getLogger("ejmapper")

load_dotenv()

app = FastAPI(title="EJMapper API", version="0.1.0")

# CORS: allow the local dev servers plus any production origins supplied via the
# ALLOWED_ORIGINS env var (comma-separated). Vercel preview/prod domains
# (*.vercel.app) are matched by regex so preview deploys work without config.
_DEFAULT_ORIGINS = ["http://localhost:5173", "http://localhost:3000"]
_ENV_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEFAULT_ORIGINS + _ENV_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)

anthropic = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "EJMapper API"}


# ── Main neighborhood route ───────────────────────────────────────────────────

@app.get("/api/neighborhood/{zip_code}")
async def neighborhood(zip_code: str, radius: float = 1.0):
    """
    Given a 5-digit US zip code, return:
      - environmental indicators (12 EPA EJScreen metrics)
      - percentile ranks vs. national averages
      - demographic breakdown
      - an AI-generated plain-language report card with a 0–100 score
    """
    if not zip_code.isdigit() or len(zip_code) != 5:
        raise HTTPException(status_code=400, detail="Zip code must be exactly 5 digits.")

    # Step 1: Convert zip code to coordinates
    try:
        lat, lon = await zip_to_latlon(zip_code)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Step 2: Fetch EJScreen data. If the EPA API is unreachable/timing out/erroring
    # (it goes down periodically), transparently fall back to mock data so the app
    # keeps working end-to-end. Real data resumes automatically once EPA recovers.
    data_source = "live"
    try:
        ejscreen_data = await get_ejscreen_data(lat, lon, radius_miles=radius)
    except ValueError as e:
        # Coordinates resolved but EJScreen genuinely has no coverage there.
        raise HTTPException(status_code=404, detail=str(e))
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as e:
        logger.warning("EJScreen unavailable (%s) — serving mock data for %s",
                       type(e).__name__, zip_code)
        ejscreen_data = generate_mock_ejscreen(lat, lon, zip_code, radius_miles=radius)
        data_source = "mock"

    # Step 3: Generate AI report card using Claude
    report_card = await generate_report_card(
        zip_code=zip_code,
        environmental=ejscreen_data["environmental"],
        percentiles=ejscreen_data["percentiles"],
        demographic=ejscreen_data["demographic"],
    )

    # Step 4: Return everything (strip _raw — it's too noisy for the frontend)
    ejscreen_data.pop("_raw", None)

    return {
        "zip_code":   zip_code,
        "location":   ejscreen_data["location"],
        "environmental": ejscreen_data["environmental"],
        "percentiles":   ejscreen_data["percentiles"],
        "demographic":   ejscreen_data["demographic"],
        "report_card":   report_card,
        "data_source":   data_source,   # "live" = real EJScreen, "mock" = fallback
    }


# ── Map layers route (heatmap + facilities + green space as GeoJSON) ──────────

@app.get("/api/map-layers/{zip_code}")
async def map_layers(zip_code: str, intensity: float = 60.0, radius: float = 1.5):
    """
    Return the three Mapbox overlays for a zip code as GeoJSON:
      - air_quality:  weighted heatmap point cloud (intensity = air pollution
                      percentile 0–100; pass the value from /api/neighborhood)
      - facilities:   regulated facility markers (EPA ECHO, mock fallback)
      - green_spaces: park/green polygons (OSM Overpass, mock fallback)

    Facilities and green spaces are fetched in parallel.
    """
    if not zip_code.isdigit() or len(zip_code) != 5:
        raise HTTPException(status_code=400, detail="Zip code must be exactly 5 digits.")

    try:
        lat, lon = await zip_to_latlon(zip_code)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    facilities, green_spaces = await asyncio.gather(
        facilities_geojson(lat, lon, radius_miles=radius),
        green_space_geojson(lat, lon, radius_miles=radius),
    )

    return {
        "center": {"lat": lat, "lon": lon},
        "air_quality": air_quality_geojson(lat, lon, intensity=intensity),
        "facilities": facilities,
        "green_spaces": green_spaces,
    }


# ── EJScreen-only route (useful for map layer data) ───────────────────────────

@app.get("/api/ejscreen")
async def ejscreen_endpoint(lat: float, lon: float, radius: float = 1.0):
    """Return raw EJScreen indicators for a lat/lon. Used by the map layer system."""
    try:
        data = await get_ejscreen_data(lat, lon, radius_miles=radius)
        data.pop("_raw", None)
        data["data_source"] = "live"
        return data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as e:
        logger.warning("EJScreen unavailable (%s) — serving mock data for (%s, %s)",
                       type(e).__name__, lat, lon)
        data = generate_mock_ejscreen(lat, lon, zip_code=None, radius_miles=radius)
        data["data_source"] = "mock"
        return data


# ── Claude report card generator ─────────────────────────────────────────────

async def generate_report_card(
    zip_code: str,
    environmental: dict,
    percentiles: dict,
    demographic: dict,
) -> dict:
    """
    Send neighborhood data to Claude and get back a structured report card.

    Returns:
        {
            "score": int (0–100, higher = more burdened),
            "grade": str ("A" through "F"),
            "summary": str (2–3 sentence plain language overview),
            "key_findings": list[str] (3 specific findings),
            "action_items": list[str] (2 things residents can do),
            "comparison": str (how this zip compares to national avg)
        }
    """
    # Build a clean data summary to send to Claude
    # Only include non-null values so the prompt isn't cluttered with None
    env_clean = {k: v for k, v in environmental.items() if v is not None}
    pct_clean = {k: v for k, v in percentiles.items() if v is not None}
    demo_clean = {k: round(v * 100, 1) if v is not None else None
                  for k, v in demographic.items()}

    prompt = f"""You are an environmental health analyst writing a neighborhood report card for residents of zip code {zip_code}.

Here is the environmental data for this neighborhood (1-mile radius):

ENVIRONMENTAL INDICATORS (raw values):
{env_clean}

NATIONAL PERCENTILE RANKS (0-100, higher means more pollution than that % of the US):
{pct_clean}

DEMOGRAPHIC CONTEXT:
{demo_clean}

Write a report card in this exact JSON format — no markdown, no code blocks, just raw JSON:
{{
  "score": <integer 0-100 where 100 = most environmentally burdened>,
  "grade": <"A", "B", "C", "D", or "F" — A means clean, F means severely burdened>,
  "summary": "<2-3 sentences a resident with no science background can understand. Be direct and honest, not alarming.>",
  "key_findings": [
    "<finding 1 — be specific, cite a number>",
    "<finding 2 — be specific, cite a number>",
    "<finding 3 — be specific, cite a number>"
  ],
  "action_items": [
    "<action 1 — something a resident or community group can actually do>",
    "<action 2 — something a resident or community group can actually do>"
  ],
  "comparison": "<one sentence comparing this neighborhood to the national average>"
}}

Rules:
- Use plain language. Avoid jargon like "percentile" or "μg/m³" — explain what numbers mean.
- Be honest about problems without being alarmist.
- Action items should be realistic for regular people, not policy recommendations.
- Return ONLY the JSON object. No preamble, no explanation."""

    message = await anthropic.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = message.content[0].text.strip()

    # Parse the JSON Claude returns
    import json
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        # If Claude returned something unexpected, return a safe fallback
        return {
            "score": None,
            "grade": None,
            "summary": raw_text,
            "key_findings": [],
            "action_items": [],
            "comparison": "",
        }
