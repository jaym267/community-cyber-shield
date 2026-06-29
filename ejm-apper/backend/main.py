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
import time

import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from census_api import zip_to_latlon
from ejscreen_api import get_ejscreen_data
from map_layers import air_quality_geojson, facilities_geojson, green_space_geojson
from mock_data import generate_mock_ejscreen

logger = logging.getLogger("ejmapper")

load_dotenv()


# ── Rate limiting ─────────────────────────────────────────────────────────────
# Identify clients by their real IP. Render/Vercel sit behind a proxy, so
# request.client.host is the proxy — read the first hop of X-Forwarded-For first.
def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Use the RIGHTMOST hop — that's the IP appended by the trusted platform
        # proxy (Render/Vercel) and is the real client. The leftmost entries are
        # client-supplied and spoofable, so keying off them would let an attacker
        # reset the rate-limit bucket at will.
        return forwarded.split(",")[-1].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=client_ip)

app = FastAPI(title="EJMapper API", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# ── Security headers ──────────────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth response headers on every API response."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)

# CORS: allow the local dev servers plus any production origins supplied via the
# ALLOWED_ORIGINS env var (comma-separated). Vercel preview/prod domains are
# matched by an anchored, single-label regex (Starlette uses fullmatch). The API
# is public and read-only with no cookies/credentials, so CORS is defense-in-depth.
_DEFAULT_ORIGINS = ["http://localhost:5173", "http://localhost:3000"]
_ENV_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEFAULT_ORIGINS + _ENV_ORIGINS,
    allow_origin_regex=r"https://[a-z0-9-]+\.vercel\.app",
    allow_methods=["GET"],
    allow_headers=["*"],
)

anthropic = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# ── In-memory TTL cache (per-zip) ─────────────────────────────────────────────
# Caching a zip's response short-circuits the geocode, EJScreen fetch, and — most
# importantly — the paid Claude call, so repeat searches cost nothing. In-memory
# only: resets on restart and isn't shared across instances (fine for one Render
# free-tier instance). Size-capped so enumerating all ~40k US zips can't OOM us.
_CACHE_TTL_SECONDS = 24 * 60 * 60   # 24h — EJScreen data is effectively static
_CACHE_MAX_ENTRIES = 5000
_cache: dict[str, tuple[float, dict]] = {}


def cache_get(key: str):
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return value


def cache_set(key: str, value: dict) -> None:
    if len(_cache) >= _CACHE_MAX_ENTRIES and key not in _cache:
        # Evict the oldest entry (FIFO) to bound memory.
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[key] = (time.time(), value)


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "EJMapper API"}


# ── Main neighborhood route ───────────────────────────────────────────────────

@app.get("/api/neighborhood/{zip_code}")
@limiter.limit("20/minute")
async def neighborhood(
    request: Request,
    zip_code: str,
    radius: float = Query(1.0, ge=0.1, le=5.0),
):
    """
    Given a 5-digit US zip code, return:
      - environmental indicators (12 EPA EJScreen metrics)
      - percentile ranks vs. national averages
      - demographic breakdown
      - an AI-generated plain-language report card with a 0–100 score
    """
    if not zip_code.isdigit() or len(zip_code) != 5:
        raise HTTPException(status_code=400, detail="Zip code must be exactly 5 digits.")

    # Step 0: Serve from cache when possible. A hit skips the geocode, the EJScreen
    # fetch, AND the paid Claude call — so repeat searches are free.
    cache_key = f"neighborhood:{zip_code}:{radius}"
    cached = cache_get(cache_key)
    if cached is not None:
        logger.info("cache HIT %s", cache_key)
        return {**cached, "cached": True}
    logger.info("cache MISS %s", cache_key)

    # Step 1: Convert zip code to coordinates
    try:
        lat, lon = await zip_to_latlon(zip_code)
    except ValueError:
        raise HTTPException(status_code=404, detail="Zip code not found.")

    # Step 2: Fetch EJScreen data. If the EPA API is unreachable/timing out/erroring
    # (it goes down periodically), transparently fall back to mock data so the app
    # keeps working end-to-end. Real data resumes automatically once EPA recovers.
    data_source = "live"
    try:
        ejscreen_data = await get_ejscreen_data(lat, lon, radius_miles=radius)
    except ValueError:
        # Coordinates resolved but EJScreen genuinely has no coverage there.
        raise HTTPException(
            status_code=404, detail="No environmental data available for that location."
        )
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

    result = {
        "zip_code":   zip_code,
        "location":   ejscreen_data["location"],
        "environmental": ejscreen_data["environmental"],
        "percentiles":   ejscreen_data["percentiles"],
        "demographic":   ejscreen_data["demographic"],
        "report_card":   report_card,
        "data_source":   data_source,   # "live" = real EJScreen, "mock" = fallback
    }
    cache_set(cache_key, result)
    return {**result, "cached": False}


# ── Map layers route (heatmap + facilities + green space as GeoJSON) ──────────

@app.get("/api/map-layers/{zip_code}")
@limiter.limit("20/minute")
async def map_layers(
    request: Request,
    zip_code: str,
    intensity: float = Query(60.0, ge=0, le=100),
    radius: float = Query(1.5, ge=0.1, le=5.0),
):
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

    cache_key = f"map-layers:{zip_code}:{round(intensity)}:{radius}"
    cached = cache_get(cache_key)
    if cached is not None:
        logger.info("cache HIT %s", cache_key)
        return cached
    logger.info("cache MISS %s", cache_key)

    try:
        lat, lon = await zip_to_latlon(zip_code)
    except ValueError:
        raise HTTPException(status_code=404, detail="Zip code not found.")

    facilities, green_spaces = await asyncio.gather(
        facilities_geojson(lat, lon, radius_miles=radius),
        green_space_geojson(lat, lon, radius_miles=radius),
    )

    result = {
        "center": {"lat": lat, "lon": lon},
        "air_quality": air_quality_geojson(lat, lon, intensity=intensity),
        "facilities": facilities,
        "green_spaces": green_spaces,
    }
    cache_set(cache_key, result)
    return result


# ── EJScreen-only route (useful for map layer data) ───────────────────────────

@app.get("/api/ejscreen")
@limiter.limit("20/minute")
async def ejscreen_endpoint(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius: float = Query(1.0, ge=0.1, le=5.0),
):
    """Return raw EJScreen indicators for a lat/lon. Used by the map layer system."""
    try:
        data = await get_ejscreen_data(lat, lon, radius_miles=radius)
        data.pop("_raw", None)
        data["data_source"] = "live"
        return data
    except ValueError:
        raise HTTPException(
            status_code=404, detail="No environmental data available for that location."
        )
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
        # Claude returned non-JSON. Log the raw text server-side for debugging, but
        # never echo unbounded model output back to the client — return a safe,
        # generic fallback the frontend can render predictably.
        logger.error("Claude returned non-JSON for %s: %s", zip_code, raw_text[:300])
        return {
            "score": None,
            "grade": None,
            "summary": "We couldn't generate a report card for this area right now. Please try again.",
            "key_findings": [],
            "action_items": [],
            "comparison": "",
        }
