"""
ejscreen_api.py — EPA EJScreen data fetcher for EJMapper.

The EJScreen REST broker returns 12 environmental indicators + 6 demographic
indicators for any US point location. No API key required.

    data = await get_ejscreen_data(lat=29.4241, lon=-98.4936, radius_miles=1.0)
"""

import json
import logging

import httpx

logger = logging.getLogger("ejmapper")

EJSCREEN_URL = "https://ejscreen.epa.gov/mapper/ejscreenRESTbroker.aspx"

# ── Field maps: EJScreen raw field name → snake_case key used in the app ─────
# Reference: EJScreen Technical Documentation (2023). Field names occasionally
# drift between EJScreen releases; _extract() tolerates missing fields.

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
    "UST":     "underground_storage_tanks",  # UST + leaking UST count / km²
    "PWDIS":   "wastewater_discharge",    # Toxicity-weighted effluent flow
}

# National percentile ranks (0–100; 80 = worse than 80% of the US population).
# Suffix _D2 = EJScreen's supplemental index (environmental only, no demo weighting).
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

DEMO_FIELDS: dict[str, str] = {
    "LOWINCPCT":  "pct_low_income",
    "MINORPCT":   "pct_minority",
    "LESSHSPCT":  "pct_no_hs_diploma",
    "LINGISOPCT": "pct_linguistically_isolated",
    "UNDER5PCT":  "pct_under_5",
    "OVER64PCT":  "pct_over_64",
}


async def get_ejscreen_data(
    lat: float,
    lon: float,
    radius_miles: float = 1.0,
) -> dict:
    """
    Fetch EPA EJScreen environmental-justice indicators for a point location.

    Returns a dict with "location", "environmental" (12 raw values),
    "percentiles" (12 national ranks, higher = more burdened), "demographic"
    (6 fractions 0–1), and "_raw" (full EJScreen row — strip before sending on).

    Raises:
        ValueError:             no rows returned (bad coordinates / outside US).
        httpx.HTTPStatusError:  EPA server returned a non-2xx response.
        httpx.TimeoutException: EPA server timed out.
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
        "f":        "pjson",
        "showgdb":  "true",
        "filetype": "0",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0)) as client:
        resp = await client.get(EJSCREEN_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()

    # EJScreen nests results under data.rows; older versions used a top-level key.
    rows = payload.get("data", {}).get("rows") or payload.get("rows") or []
    if not rows:
        # Log internals server-side; keep the client-facing message generic.
        logger.warning(
            "EJScreen returned no rows for (%s, %s). Payload keys: %s",
            lat, lon, list(payload.keys()),
        )
        raise ValueError("EJScreen returned no data for the requested location.")

    row = rows[0]
    return {
        "location": {"lat": lat, "lon": lon, "radius_miles": radius_miles},
        "environmental": _extract(row, ENV_FIELDS),
        "percentiles":   _extract(row, ENV_PERCENTILE_FIELDS),
        "demographic":   _extract(row, DEMO_FIELDS),
        "_raw":          row,
    }


def _extract(row: dict, field_map: dict[str, str]) -> dict:
    """Map raw EJScreen field names to app snake_case keys, tolerating missing fields."""
    return {snake: _coerce_float(row.get(raw)) for raw, snake in field_map.items()}


def _coerce_float(value) -> float | None:
    """Convert EJScreen values to float; None for nulls, sentinels, and non-numerics."""
    if value is None:
        return None
    try:
        f = float(value)
        return None if f in (-9999.0, -99999.0) else f  # -9999 = EJScreen null sentinel
    except (TypeError, ValueError):
        return None
