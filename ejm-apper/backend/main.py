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
import json
import logging
import math
import os
import time
from pathlib import Path

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

from census_api import zip_to_latlon, latlon_to_zip
from ejscreen_api import get_ejscreen_data
from map_layers import air_quality_geojson, facilities_geojson, green_space_geojson
from acs_api import generate_mock_acs, get_acs_zcta_data
from canopy_data import get_canopy_data
from heat_api import get_heat_data, load_sa_zips
from heat_vulnerability import WEIGHTS, compute_vulnerability
from region_layers import (
    REGION_RADIUS_MI,
    acs_for_zips,
    canopy_for_region,
    heat_for_zips,
    load_us_centroids,
    polygons_for_zips,
    region_key,
    region_zips,
)
from mock_data import generate_mock_ejscreen

logger = logging.getLogger("ejmapper")

load_dotenv(Path(__file__).resolve().parent / ".env")


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
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), browsing-topics=()"
        )
        # API responses are JSON and embed nothing — lock them down hard. Skip the
        # docs/openapi paths so Swagger UI (which needs CDN scripts) still renders.
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
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
# Mock/fallback results get a much shorter TTL: if EPA blips for one request we
# shouldn't pin that zip to labeled-estimated data for a whole day after the
# real service recovers.
_CACHE_TTL_MOCK_SECONDS = 10 * 60   # 10 min
_CACHE_MAX_ENTRIES = 5000
_cache: dict[str, tuple[float, object]] = {}   # key -> (expires_at, value)


def cache_get(key: str):
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.time() > expires_at:
        _cache.pop(key, None)
        return None
    return value


def cache_set(key: str, value, ttl: float = _CACHE_TTL_SECONDS) -> None:
    if len(_cache) >= _CACHE_MAX_ENTRIES and key not in _cache:
        # Evict the soonest-to-expire entry to bound memory.
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[key] = (time.time() + ttl, value)


def require_zip(zip_code: str) -> None:
    """400 unless zip_code is exactly 5 digits — shared by every zip route."""
    if not zip_code.isdigit() or len(zip_code) != 5:
        raise HTTPException(status_code=400, detail="Zip code must be exactly 5 digits.")


async def geocode_zip_cached(zip_code: str) -> tuple[float, float]:
    """
    Geocode a zip to its centroid (lat, lon), cached for 7 days — zip centroids
    don't move, and caching keeps us well inside Nominatim's 1 req/s policy.
    Raises ValueError if the zip can't be geocoded. Shared by the main report
    AND the nearby-zips scorer so both always score a zip at the same point.
    """
    key = f"geocode:{zip_code}"
    hit = cache_get(key)
    if hit is not None:
        return hit
    latlon = await zip_to_latlon(zip_code)
    cache_set(key, latlon, ttl=7 * 24 * 60 * 60)
    return latlon


async def resolve_zip(zip_code: str) -> tuple[float, float]:
    """Geocode a zip to (lat, lon), mapping failure to a clean 404."""
    try:
        return await geocode_zip_cached(zip_code)
    except ValueError:
        raise HTTPException(status_code=404, detail="Zip code not found.")


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "EJMapper API"}


# ── Nearby-zip comparison route ───────────────────────────────────────────────

@app.get("/api/nearby-zips/{zip_code}")
@limiter.limit("5/minute")
async def nearby_zips_endpoint(request: Request, zip_code: str):
    """
    Return up to 6 nearby zip codes with quick environmental scores for comparison.
    Samples 6 points ~3 miles out, reverse-geocodes each to a zip code (staggered
    1.1s apart to respect Nominatim's 1 req/s policy), scores each unique zip via
    EJScreen, and returns them sorted cleanest to most burdened.

    Upstream fan-out is bounded at 6 Nominatim + 6 EJScreen calls per uncached
    request: each sample point's own coordinates are reused for scoring, so found
    zips are never geocoded a second time.
    """
    require_zip(zip_code)

    cache_key = f"nearby:{zip_code}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    lat, lon = await resolve_zip(zip_code)

    RADIUS = 3.0
    dlat = RADIUS / 69.0
    dlon = RADIUS / (69.0 * max(0.1, math.cos(math.radians(lat))))

    sample_points = [
        (lat + dlat, lon),
        (lat, lon + dlon),
        (lat - dlat, lon),
        (lat, lon - dlon),
        (lat + dlat * 0.7, lon + dlon * 0.7),
        (lat - dlat * 0.7, lon - dlon * 0.7),
    ]

    async def get_zip_delayed(plat: float, plon: float, delay: float) -> str | None:
        await asyncio.sleep(delay)
        return await latlon_to_zip(plat, plon)

    raw_zips = await asyncio.gather(
        *[get_zip_delayed(plat, plon, i * 1.1) for i, (plat, plon) in enumerate(sample_points)],
        return_exceptions=True,
    )

    seen = {zip_code}
    candidates = []
    for z in raw_zips:
        if isinstance(z, str) and z.isdigit() and len(z) == 5 and z not in seen:
            seen.add(z)
            candidates.append(z)
    if not candidates:
        logger.warning(
            "nearby-zips: 0/%d sample points resolved to a new zip for %s",
            len(sample_points), zip_code,
        )

    # Score each zip at its OWN geocoded centroid with the same radius the main
    # report uses — not at the probe point that discovered it. This is what
    # guarantees the grade on a nearby card equals the grade on that zip's own
    # report page. Geocodes are cached 7 days and the uncached ones are
    # staggered 1.1s apart for Nominatim's 1 req/s policy.
    async def score_zip(z: str, delay: float):
        try:
            await asyncio.sleep(delay)
            zlat, zlon = await geocode_zip_cached(z)
            try:
                ej = await get_ejscreen_data(zlat, zlon, radius_miles=1.0)
                source = "live"
            except Exception:
                ej = generate_mock_ejscreen(zlat, zlon, z, radius_miles=1.0)
                source = "mock"
            score = _score_from_percentiles(ej.get("percentiles", {}))
            return {
                "zip": z,
                "score": score,
                "grade": _grade_from_score(score),
                "data_source": source,
            }
        except Exception:
            return None

    uncached = [z for z in candidates if cache_get(f"geocode:{z}") is None]
    delays = {z: i * 1.1 for i, z in enumerate(uncached)}
    scored = await asyncio.gather(*[score_zip(z, delays.get(z, 0.0)) for z in candidates])
    zips = sorted([r for r in scored if r], key=lambda x: x["score"])

    # Surface honesty about the data: "live", "mock", or "mixed" — the frontend
    # shows an "estimated data" note whenever any mock is present.
    sources = {r["data_source"] for r in zips}
    data_source = sources.pop() if len(sources) == 1 else ("mixed" if sources else "live")

    result = {"zips": zips, "center_zip": zip_code, "data_source": data_source}
    # Don't pin fallback data for a full day — recheck EPA every 10 minutes.
    ttl = _CACHE_TTL_SECONDS if data_source == "live" else _CACHE_TTL_MOCK_SECONDS
    cache_set(cache_key, result, ttl=ttl)
    return result


# ── Canonical score/grade formula ──────────────────────────────────────────────
# Single source of truth for turning EJScreen percentiles into a 0–100 burden
# score and an A–F grade. Every place in the app that shows a score or grade
# (the main report card, the nearby-zips comparison strip, anywhere else added
# later) MUST call these two functions rather than deriving its own number —
# that guarantee is what keeps the grade for a given zip identical everywhere
# it appears. Claude is deliberately never asked to invent the score/grade
# (see generate_report_card): LLM output is not deterministic, so letting it
# free-form a number here would let two views of the same zip disagree again.
def _score_from_percentiles(percentiles: dict) -> int:
    vals = [v for v in percentiles.values() if v is not None]
    return round(sum(vals) / len(vals)) if vals else 50


def _grade_from_score(score: int) -> str:
    if score < 30: return "A"
    if score < 50: return "B"
    if score < 65: return "C"
    if score < 80: return "D"
    return "F"


@app.get("/api/neighborhood/{zip_code}")
@limiter.limit("20/minute")
async def neighborhood(
    request: Request,
    zip_code: str,
    radius: float = Query(1.0, ge=0.1, le=5.0),
    profile: str = Query("general"),
):
    """
    Given a 5-digit US zip code, return:
      - environmental indicators (12 EPA EJScreen metrics)
      - percentile ranks vs. national averages
      - demographic breakdown
      - an AI-generated plain-language report card with a 0–100 score
    """
    require_zip(zip_code)

    # Step 0: Serve from cache when possible. A hit skips the geocode, the EJScreen
    # fetch, AND the paid Claude call — so repeat searches are free.
    if profile not in _PROFILE_CONTEXT:
        profile = "general"
    cache_key = f"neighborhood:{zip_code}:{radius}:{profile}"
    cached = cache_get(cache_key)
    if cached is not None:
        logger.info("cache HIT %s", cache_key)
        return {**cached, "cached": True}
    logger.info("cache MISS %s", cache_key)

    # Step 1: Convert zip code to coordinates
    lat, lon = await resolve_zip(zip_code)

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

    # Step 3: Compute the score and grade deterministically — see
    # _score_from_percentiles / _grade_from_score for why this must not be left
    # to the AI. Every zip's grade is now a pure function of its percentiles, so
    # it's identical here, in the nearby-zips comparison, and anywhere else it's
    # ever shown.
    score = _score_from_percentiles(ejscreen_data["percentiles"])
    grade = _grade_from_score(score)

    # Step 4: Generate the AI report card's narrative (summary/findings/actions)
    # using Claude, telling it the fixed score and grade so its prose matches.
    report_card = await generate_report_card(
        zip_code=zip_code,
        environmental=ejscreen_data["environmental"],
        percentiles=ejscreen_data["percentiles"],
        demographic=ejscreen_data["demographic"],
        profile=profile,
        score=score,
        grade=grade,
    )
    # Belt-and-suspenders: force the authoritative values even if Claude's JSON
    # somehow included its own score/grade fields, so a model slip-up can never
    # make this report disagree with the rest of the app again.
    report_card["score"] = score
    report_card["grade"] = grade

    # Step 5: Return everything (strip _raw — it's too noisy for the frontend)
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
    # Mock results expire fast so the app returns to live data soon after EPA
    # recovers, instead of pinning "estimated" data for 24h.
    cache_set(cache_key, result,
              ttl=_CACHE_TTL_SECONDS if data_source == "live" else _CACHE_TTL_MOCK_SECONDS)
    return {**result, "cached": False}


# ── Live conditions route (real-time, measured data — no API keys) ────────────
# Unlike EJScreen (a static survey snapshot), everything here is live measured
# or officially issued data, fetched per request from free public services:
#   air     — Open-Meteo Air Quality API (current US AQI + pollutants)
#   weather — NWS gridpoint forecast (3-day daytime highs + conditions)
#   alerts  — National Weather Service active alerts for the exact point
#   quakes  — USGS earthquakes (mag 2.5+, 200 km, past 30 days)
# Each source fails independently to None so one outage never hides the rest.

_OPEN_METEO_AQ = "https://air-quality-api.open-meteo.com/v1/air-quality"
_NWS_ALERTS = "https://api.weather.gov/alerts/active"
_USGS_QUAKES = "https://earthquake.usgs.gov/fdsnws/event/1/query"
# NWS requires an identifying User-Agent on every request.
_LIVE_HEADERS = {"User-Agent": "Sentinal/1.0 (github.com/jaym267/community-cyber-shield)"}


async def _fetch_air(client: httpx.AsyncClient, lat: float, lon: float):
    r = await client.get(_OPEN_METEO_AQ, params={
        "latitude": lat, "longitude": lon,
        "current": "us_aqi,pm2_5,pm10,ozone,nitrogen_dioxide",
        "timezone": "auto",
    })
    r.raise_for_status()
    cur = r.json().get("current", {})
    return {
        "us_aqi": cur.get("us_aqi"),
        "pm2_5": cur.get("pm2_5"),
        "pm10": cur.get("pm10"),
        "ozone": cur.get("ozone"),
        "nitrogen_dioxide": cur.get("nitrogen_dioxide"),
        "time": cur.get("time"),
    }


async def _fetch_weather(client: httpx.AsyncClient, lat: float, lon: float):
    # NWS two-step (point metadata → gridpoint forecast). Chosen over Open-Meteo's
    # forecast host, whose TLS handshake resets intermittently from some networks;
    # NWS is already a dependency for alerts and needs no key.
    r = await client.get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
    r.raise_for_status()
    forecast_url = r.json().get("properties", {}).get("forecast")
    if not forecast_url:
        return None
    r2 = await client.get(forecast_url)
    r2.raise_for_status()
    days = []
    for p in r2.json().get("properties", {}).get("periods", []):
        if p.get("isDaytime"):
            days.append({
                "date": (p.get("startTime") or "")[:10],
                "name": p.get("name"),
                "high_f": p.get("temperature"),
                "short": p.get("shortForecast"),
            })
        if len(days) == 3:
            break
    return {"days": days} if days else None


async def _fetch_alerts(client: httpx.AsyncClient, lat: float, lon: float):
    r = await client.get(_NWS_ALERTS, params={"point": f"{lat},{lon}"})
    r.raise_for_status()
    out = []
    for f in r.json().get("features", [])[:5]:
        p = f.get("properties", {})
        out.append({
            "event": p.get("event"),
            "severity": p.get("severity"),
            "headline": p.get("headline"),
            "expires": p.get("expires"),
            "instruction": (p.get("instruction") or "")[:280] or None,
        })
    return out   # [] is meaningful: "no active alerts"


async def _fetch_quakes(client: httpx.AsyncClient, lat: float, lon: float):
    start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400))
    r = await client.get(_USGS_QUAKES, params={
        "format": "geojson", "latitude": lat, "longitude": lon,
        "maxradiuskm": 200, "starttime": start, "minmagnitude": 2.5,
        "orderby": "magnitude", "limit": 20,
    })
    r.raise_for_status()
    feats = r.json().get("features", [])
    strongest = None
    if feats:
        p = feats[0].get("properties", {})
        strongest = {"mag": p.get("mag"), "place": p.get("place"), "time_ms": p.get("time")}
    return {"count_30d": len(feats), "strongest": strongest}


@app.get("/api/live-conditions/{zip_code}")
@limiter.limit("20/minute")
async def live_conditions(request: Request, zip_code: str):
    """
    Real-time conditions for a zip: current air quality (measured), 3-day
    heat/UV forecast, active NWS hazard alerts, and recent seismic activity.
    Sources that fail return null — the frontend labels each section's source.
    """
    require_zip(zip_code)

    cache_key = f"live:{zip_code}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    lat, lon = await resolve_zip(zip_code)

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), headers=_LIVE_HEADERS) as client:
        air, weather, alerts, quakes = await asyncio.gather(
            _fetch_air(client, lat, lon),
            _fetch_weather(client, lat, lon),
            _fetch_alerts(client, lat, lon),
            _fetch_quakes(client, lat, lon),
            return_exceptions=True,
        )

    def ok(v):
        return None if isinstance(v, BaseException) else v
    for name, v in (("air", air), ("weather", weather), ("alerts", alerts), ("quakes", quakes)):
        if isinstance(v, BaseException):
            logger.warning("live-conditions %s failed for %s: %r", name, zip_code, v)

    result = {
        "zip_code": zip_code,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "air": ok(air),
        "weather": ok(weather),
        "alerts": ok(alerts),
        "quakes": ok(quakes),
    }
    # Live data goes stale fast — 15 minutes, matching Open-Meteo's update cadence.
    cache_set(cache_key, result, ttl=15 * 60)
    return result


# ── Heat layer route (NASA POWER air temperature, San Antonio region) ─────────
# Region-scoped (Bexar County, ~70 zips), not per-zip: one dataset computed
# once and cached under one key. See heat_api.py for the honesty notes about
# air-temp-vs-LST and POWER's ~50 km grid resolution.

@app.get("/api/heat")
@limiter.limit("20/minute")
async def heat_endpoint(request: Request):
    """30-day avg daily max air temperature per San Antonio-area zip."""
    cache_key = "heat:sa"
    cached = cache_get(cache_key)
    if cached is not None:
        logger.info("cache HIT %s", cache_key)
        return cached
    logger.info("cache MISS %s", cache_key)

    result = await get_heat_data()
    ttl = _CACHE_TTL_SECONDS if result["source"] == "live" else _CACHE_TTL_MOCK_SECONDS
    cache_set(cache_key, result, ttl=ttl)
    return result


async def _heat_cached() -> dict:
    """The /api/heat payload via the shared cache (same key/TTL as the route)."""
    cached = cache_get("heat:sa")
    if cached is not None:
        return cached
    result = await get_heat_data()
    ttl = _CACHE_TTL_SECONDS if result["source"] == "live" else _CACHE_TTL_MOCK_SECONDS
    cache_set("heat:sa", result, ttl=ttl)
    return result


async def _acs_cached() -> dict:
    """ACS demographics for the SA zip set, cached; labeled mock on failure."""
    cached = cache_get("acs:sa")
    if cached is not None:
        return cached
    zctas = [z["zip"] for z in load_sa_zips()]
    try:
        data = await get_acs_zcta_data(zctas)
        source = "live"
    except Exception as e:
        logger.warning("ACS unavailable (%r) — serving mock demographics", e)
        data = generate_mock_acs(zctas)
        source = "mock"
    result = {"zips": data, "source": source}
    ttl = _CACHE_TTL_SECONDS if source == "live" else _CACHE_TTL_MOCK_SECONDS
    cache_set("acs:sa", result, ttl=ttl)
    return result


# ── Heat-vulnerability route (composite score, San Antonio region) ────────────
# score = weighted sum of SA-set-normalized components; see
# heat_vulnerability.py for the documented formula, weights, and the
# missing-component renormalization rule.

@app.get("/api/heat-vulnerability")
@limiter.limit("20/minute")
async def heat_vulnerability_endpoint(request: Request):
    """Composite heat-vulnerability score per San Antonio-area zip."""
    cache_key = "heat-vuln:sa"
    cached = cache_get(cache_key)
    if cached is not None:
        logger.info("cache HIT %s", cache_key)
        return cached
    logger.info("cache MISS %s", cache_key)

    heat = await _heat_cached()
    canopy = get_canopy_data()
    acs = await _acs_cached()
    all_zips = [z["zip"] for z in load_sa_zips()]

    result = {
        "region": "san_antonio",
        "weights": WEIGHTS,
        "formula_note": (
            "Each component is min-max normalized 0-1 across the San Antonio "
            "zip set, inverted where needed so 1 = more vulnerable, then "
            "weighted and summed; weights renormalize over available "
            "components when data is missing."
        ),
        "zips": compute_vulnerability(heat["zips"], canopy["zips"], acs["zips"], all_zips),
        "sources": {
            "heat": heat["source"],
            "canopy": canopy["source"],
            "acs": acs["source"],
        },
    }
    all_good = (heat["source"] == "live" and acs["source"] == "live"
                and canopy["source"] == "static_estimate")
    cache_set(cache_key, result,
              ttl=_CACHE_TTL_SECONDS if all_good else _CACHE_TTL_MOCK_SECONDS)
    return result


# ── Heat-layers route (regional choropleth GeoJSON, anywhere in the US) ───────
# One FeatureCollection with temp/canopy/vulnerability as feature properties
# for every ZCTA within ~12 miles of the searched zip. Vulnerability scores
# are normalized ACROSS THE REGION — "relative to nearby zips", the honest
# framing for urban-heat-island comparisons. Regions quantized to 0.1° share
# a cache entry; POWER grid cells are cached individually inside
# region_layers so overlapping regions never refetch them.

@app.get("/api/heat-layers/{zip_code}")
@limiter.limit("20/minute")
async def heat_layers_endpoint(request: Request, zip_code: str):
    """Regional ZCTA polygons with temp_f / canopy_pct / vuln_score properties."""
    require_zip(zip_code)

    center = load_us_centroids().get(zip_code)
    if center is None:
        # Not a known ZCTA (PO-box-only zips etc.) — fall back to geocoding.
        lat, lon = await resolve_zip(zip_code)
    else:
        lat, lon = center

    rkey = region_key(lat, lon)
    cache_key = f"region-layers:{rkey}"
    cached = cache_get(cache_key)
    if cached is not None:
        logger.info("cache HIT %s", cache_key)
        return cached
    logger.info("cache MISS %s", cache_key)

    zips = region_zips(lat, lon)
    if not zips:
        return {"type": "FeatureCollection", "features": [], "stats": {},
                "sources": {}, "region_zips": [], "region": {"key": rkey}}
    zip_list = [z["zip"] for z in zips]

    # Boundaries + heat + demographics in parallel; canopy afterwards because
    # it needs the polygons. Each source degrades independently.
    polygons, (heat_zips, heat_src), (acs_zips, acs_src) = await asyncio.gather(
        polygons_for_zips(zip_list),
        heat_for_zips(zips),
        acs_for_zips(zip_list),
    )
    canopy_zips, canopy_src = await canopy_for_region(polygons, zip_list)
    vuln = compute_vulnerability(heat_zips, canopy_zips, acs_zips, zip_list)

    features = []
    stats: dict[str, dict] = {}

    def track(metric: str, value):
        if value is None:
            return
        s = stats.setdefault(metric, {"min": value, "max": value})
        s["min"] = min(s["min"], value)
        s["max"] = max(s["max"], value)

    for f in (polygons or {}).get("features", []):
        z = f.get("properties", {}).get("ZCTA5")
        if z not in set(zip_list):
            continue
        temp = heat_zips.get(z)
        can = canopy_zips.get(z)
        score = (vuln.get(z) or {}).get("score")
        track("temp_f", temp)
        track("canopy_pct", can)
        track("vuln_score", score)
        features.append({
            "type": "Feature",
            "geometry": f["geometry"],
            "properties": {
                "zip": z, "temp_f": temp, "canopy_pct": can, "vuln_score": score,
            },
        })

    result = {
        "type": "FeatureCollection",
        "features": features,
        "stats": stats,
        "sources": {
            "heat": heat_src,
            "canopy": canopy_src,
            "acs": acs_src,
            "boundaries": "live" if polygons else "unavailable",
        },
        "metric_notes": {
            "temp_f": (
                "30-day avg daily max AIR temperature (NASA POWER), bilinearly "
                "interpolated from a ~50 km grid — not land-surface temperature."
            ),
            "canopy_pct": (
                "Mean NLCD 2021 tree canopy per zip — estimates (static for "
                "San Antonio, computed on demand elsewhere)."
            ),
            "vuln_score": (
                "Composite of temperature, canopy, and Census demographics, "
                "normalized across this region — scores compare nearby zips, "
                "not the whole country."
            ),
        },
        "region_zips": zip_list,
        "region": {"key": rkey, "center": {"lat": lat, "lon": lon},
                   "radius_mi": REGION_RADIUS_MI},
    }
    all_good = bool(features) and heat_src == "live" and acs_src == "live"
    cache_set(cache_key, result,
              ttl=_CACHE_TTL_SECONDS if all_good else _CACHE_TTL_MOCK_SECONDS)
    return result


# ── Canopy layer route (static NLCD-derived estimates, San Antonio region) ────

@app.get("/api/canopy")
@limiter.limit("20/minute")
async def canopy_endpoint(request: Request):
    """Tree canopy % per San Antonio-area zip (NLCD TCC 2021 zonal means)."""
    # Static committed data — lru_cache inside get_canopy_data, no TTL needed.
    return get_canopy_data()


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
    require_zip(zip_code)

    cache_key = f"map-layers:{zip_code}:{round(intensity)}:{radius}"
    cached = cache_get(cache_key)
    if cached is not None:
        logger.info("cache HIT %s", cache_key)
        return cached
    logger.info("cache MISS %s", cache_key)

    lat, lon = await resolve_zip(zip_code)

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

_PROFILE_CONTEXT = {
    "general": {
        "audience": "a resident with no scientific background",
        "emphasis": "Give a balanced assessment of all major pollution sources.",
    },
    "children": {
        "audience": "a parent concerned about their young children's health (ages 0–12)",
        "emphasis": (
            "Prioritize lead paint exposure, traffic-related air pollution, and air toxics "
            "cancer risk — these most directly affect child development. Mention specific "
            "risks for kids and what parents can watch for."
        ),
    },
    "elderly": {
        "audience": "an elderly resident or their caregiver",
        "emphasis": (
            "Prioritize fine particles (PM2.5), ozone, and respiratory hazard index — "
            "these most directly affect older adults. Mention cardiovascular and "
            "respiratory risks and how they compound with age."
        ),
    },
    "respiratory": {
        "audience": "someone managing a respiratory condition such as asthma or COPD",
        "emphasis": (
            "Heavily prioritize PM2.5, ozone, diesel exhaust, and the respiratory hazard "
            "index. Be specific about which pollutants are most likely to trigger symptoms "
            "and give practical day-to-day guidance."
        ),
    },
}

async def generate_report_card(
    zip_code: str,
    environmental: dict,
    percentiles: dict,
    demographic: dict,
    score: int,
    grade: str,
    profile: str = "general",
) -> dict:
    """
    Send neighborhood data to Claude and get back the narrative portion of the
    report card. The score and grade are NOT generated here — they're computed
    deterministically by _score_from_percentiles/_grade_from_score in the caller
    and simply handed to Claude as fixed facts to write around. An LLM asked to
    invent a score free-form will not reliably reproduce the same number twice,
    which previously let the same zip show different grades in different parts
    of the app (e.g. a "D" on its own report card but a "C" in the nearby-zips
    comparison). Do not change this back to letting Claude choose the score/grade.

    Returns:
        {
            "score": int (0–100, higher = more burdened) — echoes the input,
            "grade": str ("A" through "F") — echoes the input,
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

    ctx = _PROFILE_CONTEXT.get(profile, _PROFILE_CONTEXT["general"])

    prompt = f"""You are an environmental health analyst writing a neighborhood report card for residents of zip code {zip_code}.

You are writing for {ctx["audience"]}. {ctx["emphasis"]}

This neighborhood has ALREADY been scored: {score} out of 100 (higher = more environmentally burdened) and assigned grade {grade} (A = clean, F = severely burdened). These are fixed — your job is only to explain and justify them in plain language, not to second-guess or restate a different number.

Here is the environmental data for this neighborhood (the census block group at the heart of this zip code, from EPA's EJScreen v2.32 dataset):

ENVIRONMENTAL INDICATORS (raw values):
{env_clean}

NATIONAL PERCENTILE RANKS (0-100, higher means more pollution than that % of the US):
{pct_clean}

DEMOGRAPHIC CONTEXT:
{demo_clean}

Write the narrative portion of the report card in this exact JSON format — no markdown, no code blocks, just raw JSON, and no "score" or "grade" fields (those are already fixed above):
{{
  "summary": "<2-3 sentences a resident with no science background can understand, consistent with the {score}/100 score and {grade} grade. Be direct and honest, not alarming.>",
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
