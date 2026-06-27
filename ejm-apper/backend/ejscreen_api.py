"""
ejscreen_api.py
──────────────────────────────────────────────────────────────────────────────
EPA EJScreen data fetcher for EJMapper.

The EJScreen REST broker returns 12 environmental indicators + 6 demographic
indicators for any US point location. No API key required.

Usage (async — inside FastAPI):
    data = await get_ejscreen_data(lat=29.4241, lon=-98.4936, radius_miles=1.0)

Usage (sync — scripts and testing):
    data = get_ejscreen_data_sync(lat=29.4241, lon=-98.4936)

CLI (first run — verify field names are correct for your EJScreen version):
    python ejscreen_api.py 29.4241 -98.4936 --diagnose

CLI (normal):
    python ejscreen_api.py 29.4241 -98.4936

Install deps:
    pip install httpx
"""

import asyncio
import json
import sys
from typing import Optional

import httpx

# ── API Endpoint ───────────────────────────────────────────────────────────────

EJSCREEN_URL = "https://ejscreen.epa.gov/mapper/ejscreenRESTbroker.aspx"

# ── 12 Environmental Indicators ───────────────────────────────────────────────
# Maps EJScreen raw field name → snake_case key used in your app.
#
# Field name reference: EJScreen Technical Documentation (2023)
# https://www.epa.gov/ejscreen/ejscreen-technical-documentation
#
# NOTE: On your first run, pass --diagnose to print all raw keys returned
# by the API. EJScreen occasionally renames fields between versions.

ENV_FIELDS: dict[str, str] = {
    # Air quality
    "AVGPM25": "pm25_avg_ugm3",           # Particulate matter 2.5, annual avg (μg/m³)
    "OZONE":   "ozone_ppb",               # Summer ozone average (ppb)
    "DSLPM":   "diesel_pm_ugm3",          # Diesel particulate matter (μg/m³)
    # Air toxics
    "CANCER":  "air_toxics_cancer_risk",  # Lifetime cancer risk per million people
    "RESP":    "air_toxics_resp_hazard",  # Noncancer respiratory hazard index
    # Traffic & built environment
    "PTRAF":   "traffic_proximity",       # Weighted daily vehicles/meter (AADT)
    "LDPNT":   "lead_paint_pct",          # % housing units built before 1960
    # Hazardous sites
    "PNPL":    "superfund_proximity",     # Superfund (NPL) sites / km²
    "PRMP":    "rmp_facility_proximity",  # RMP risk-management facilities / km²
    "PTSDF":   "hazwaste_proximity",      # Hazardous waste treatment sites / km²
    # Water & underground
    "UST":     "underground_storage_tanks", # UST + leaking UST count / km²
    "PWDIS":   "wastewater_discharge",    # Toxicity-weighted effluent flow
}

# National percentile ranks for each environmental indicator.
# Percentile 80 means the area scores worse than 80% of the US population.
# Field suffix _D2 = EJScreen's "supplemental index" (environmental only, no demo weighting).
ENV_PERCENTILE_FIELDS: dict[str, str] = {
    "PM25_D2":   "pm25_pctile_national",
    "OZONE_D2":  "ozone_pctile_national",
    "DSLPM_D2":  "diesel_pm_pctile_national",
    "CANCER_D2": "cancer_risk_pctile_national",
    "RESP_D2":   "resp_hazard_pctile_national",
    "PTRAF_D2":  "traffic_pctile_national",
    "LDPNT_D2":  "lead_paint_pctile_national",
    "PNPL_D2":   "superfund_pctile_national",
    "PRMP_D2":   "rmp_pctile_national",
    "PTSDF_D2":  "hazwaste_pctile_national",
    "UST_D2":    "ust_pctile_national",
    "PWDIS_D2":  "wastewater_pctile_national",
}

# ── 6 Demographic Indicators ──────────────────────────────────────────────────
DEMO_FIELDS: dict[str, str] = {
    "LOWINCPCT":  "pct_low_income",
    "MINORPCT":   "pct_minority",
    "LESSHSPCT":  "pct_no_hs_diploma",
    "LINGISOPCT": "pct_linguistically_isolated",
    "UNDER5PCT":  "pct_under_5",
    "OVER64PCT":  "pct_over_64",
}


# ── Core async function ────────────────────────────────────────────────────────

async def get_ejscreen_data(
    lat: float,
    lon: float,
    radius_miles: float = 1.0,
) -> dict:
    """
    Fetch EPA EJScreen environmental justice indicators for a point location.

    Args:
        lat:          Latitude in decimal degrees  (e.g., 29.4241 for San Antonio).
        lon:          Longitude in decimal degrees (e.g., -98.4936; negative for US).
        radius_miles: Buffer radius in miles. EJScreen recommends 1–3 mi for
                      neighborhood-level analysis. Default: 1.0.

    Returns:
        {
            "location": {
                "lat": float,
                "lon": float,
                "radius_miles": float,
            },
            "environmental": {                # 12 raw indicator values
                "pm25_avg_ugm3": float | None,
                "ozone_ppb": float | None,
                ...
            },
            "percentiles": {                  # national rank 0–100 (higher = more burdened)
                "pm25_pctile_national": float | None,
                ...
            },
            "demographic": {                  # 6 demographic fractions (0.0–1.0)
                "pct_low_income": float | None,
                ...
            },
            "_raw": dict,                     # full EJScreen row — strip before sending to Claude
        }

    Raises:
        ValueError:            EJScreen returned no rows (bad coordinates or outside US).
        httpx.HTTPStatusError: EPA server returned a non-2xx response.
        httpx.TimeoutException: EPA server timed out (retry with backoff).
    """
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError(f"Invalid coordinates: lat={lat}, lon={lon}")

    geometry = json.dumps(
        {"spatialReference": {"wkid": 4326}, "x": lon, "y": lat},
        separators=(",", ":"),
    )

    params = {
        "namestr":  "",
        "geometry": geometry,
        "distance": str(radius_miles),
        "unit":     "9035",       # 9035 = US statute miles
        "areatype": "circle",
        "areaid":   "",
        "f":        "pjson",      # pretty JSON response
        "showgdb":  "true",
        "filetype": "0",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0)) as client:
        resp = await client.get(EJSCREEN_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()

    # EJScreen nests results under data.rows; fall back to a top-level rows key
    # for older API versions, or raise clearly if neither exists.
    rows = (
        payload.get("data", {}).get("rows")
        or payload.get("rows")
        or []
    )
    if not rows:
        # Print raw payload to help debug unexpected structures
        raise ValueError(
            f"EJScreen returned no data for ({lat}, {lon}). "
            "Verify coordinates are within the contiguous US or Puerto Rico. "
            f"Raw payload keys: {list(payload.keys())}"
        )

    row = rows[0]

    return {
        "location": {
            "lat":          lat,
            "lon":          lon,
            "radius_miles": radius_miles,
        },
        "environmental": _extract(row, ENV_FIELDS),
        "percentiles":   _extract(row, ENV_PERCENTILE_FIELDS),
        "demographic":   _extract(row, DEMO_FIELDS),
        "_raw":          row,   # Keep for debugging; strip before passing to Claude
    }


def _extract(row: dict, field_map: dict[str, str]) -> dict:
    """Map raw EJScreen field names to app snake_case keys, tolerating missing fields."""
    return {
        snake: _coerce_float(row.get(raw_key))
        for raw_key, snake in field_map.items()
    }


def _coerce_float(value) -> Optional[float]:
    """Convert EJScreen values to float; return None for nulls and non-numeric strings."""
    if value is None:
        return None
    try:
        f = float(value)
        return None if f in (-9999.0, -99999.0) else f  # EJScreen uses -9999 as null sentinel
    except (TypeError, ValueError):
        return None


# ── Sync wrapper (testing and scripts) ────────────────────────────────────────

def get_ejscreen_data_sync(
    lat: float,
    lon: float,
    radius_miles: float = 1.0,
) -> dict:
    """Blocking wrapper around get_ejscreen_data for scripts and pytest."""
    return asyncio.run(get_ejscreen_data(lat, lon, radius_miles))


# ── Diagnostic helper ─────────────────────────────────────────────────────────

async def diagnose_ejscreen_response(lat: float, lon: float) -> None:
    """
    Print the full raw EJScreen response for a point location.
    Run this once to verify field names match what EJScreen actually returns
    on the current API version — names can drift between releases.

    Usage:
        python ejscreen_api.py 29.4241 -98.4936 --diagnose
    """
    geometry = json.dumps(
        {"spatialReference": {"wkid": 4326}, "x": lon, "y": lat},
        separators=(",", ":"),
    )
    params = {
        "namestr":  "",
        "geometry": geometry,
        "distance": "1",
        "unit":     "9035",
        "areatype": "circle",
        "areaid":   "",
        "f":        "pjson",
        "showgdb":  "true",
        "filetype": "0",
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.get(EJSCREEN_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()

    rows = (
        payload.get("data", {}).get("rows")
        or payload.get("rows")
        or []
    )
    if not rows:
        print("No rows returned. Full payload:")
        print(json.dumps(payload, indent=2))
        return

    row = rows[0]
    print(f"\nEJScreen raw response — {len(row)} fields returned\n")
    print("── All field names and values ──────────────────────────")
    for k, v in sorted(row.items()):
        print(f"  {k:30s}  {v}")

    print("\n── Which ENV_FIELDS matched ────────────────────────────")
    for raw_key, snake in ENV_FIELDS.items():
        val = row.get(raw_key)
        status = "✓" if raw_key in row else "✗ MISSING"
        print(f"  {status}  {raw_key:12s} → {snake}: {val}")

    print("\n── Which ENV_PERCENTILE_FIELDS matched ─────────────────")
    for raw_key, snake in ENV_PERCENTILE_FIELDS.items():
        status = "✓" if raw_key in row else "✗ MISSING"
        print(f"  {status}  {raw_key:12s} → {snake}: {row.get(raw_key)}")


# ── FastAPI route (copy into your main.py) ─────────────────────────────────────
#
# from fastapi import FastAPI, HTTPException
# from ejscreen_api import get_ejscreen_data
#
# app = FastAPI()
#
# @app.get("/api/ejscreen")
# async def ejscreen_endpoint(lat: float, lon: float, radius: float = 1.0):
#     try:
#         data = await get_ejscreen_data(lat, lon, radius)
#         data.pop("_raw", None)   # don't expose raw to the frontend
#         return data
#     except ValueError as e:
#         raise HTTPException(status_code=404, detail=str(e))
#     except httpx.HTTPStatusError as e:
#         raise HTTPException(status_code=502, detail="EPA EJScreen API error")
#     except httpx.TimeoutException:
#         raise HTTPException(status_code=504, detail="EPA EJScreen timed out — retry")
#
#
# ── In your main aggregation route, combine with other APIs using asyncio.gather:
#
# @app.get("/api/neighborhood/{zip_code}")
# async def neighborhood(zip_code: str):
#     from census_api import zip_to_latlon
#     from aqs_api import get_air_quality
#     from echo_api import get_facilities
#
#     lat, lon = await zip_to_latlon(zip_code)
#
#     ejscreen, air_quality, facilities = await asyncio.gather(
#         get_ejscreen_data(lat, lon),
#         get_air_quality(lat, lon),
#         get_facilities(lat, lon),
#     )
#
#     return {
#         "zip": zip_code,
#         "ejscreen": ejscreen,
#         "air_quality": air_quality,
#         "facilities": facilities,
#     }


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pprint

    args = sys.argv[1:]
    diagnose = "--diagnose" in args
    coords = [a for a in args if a != "--diagnose"]

    lat = float(coords[0]) if len(coords) > 0 else 29.4241   # Default: San Antonio
    lon = float(coords[1]) if len(coords) > 1 else -98.4936

    print(f"\nQuerying EJScreen for ({lat}, {lon}) — 1-mile radius")
    print(f"Endpoint: {EJSCREEN_URL}\n")

    if diagnose:
        asyncio.run(diagnose_ejscreen_response(lat, lon))
    else:
        try:
            data = get_ejscreen_data_sync(lat, lon)

            print("── Environmental indicators ────────────────────────────")
            for k, v in data["environmental"].items():
                print(f"  {k:40s}  {v}")

            print("\n── National percentile ranks (higher = more burdened) ──")
            for k, v in data["percentiles"].items():
                bar = "█" * int((v or 0) // 5) if v else ""
                print(f"  {k:40s}  {v:5.1f}  {bar}" if v else f"  {k:40s}  None")

            print("\n── Demographic indicators ──────────────────────────────")
            for k, v in data["demographic"].items():
                pct = f"{v*100:.1f}%" if v is not None else "None"
                print(f"  {k:40s}  {pct}")

        except ValueError as e:
            print(f"\nNo data returned: {e}")
            print("Try running with --diagnose to inspect the raw API response.")
        except httpx.ConnectError:
            print("\nConnection failed — check your internet connection.")
        except httpx.TimeoutException:
            print("\nEPA server timed out. Try again in a few seconds.")
        except Exception as e:
            print(f"\nUnexpected error: {type(e).__name__}: {e}")
