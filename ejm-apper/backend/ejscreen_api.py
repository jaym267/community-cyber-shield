"""
ejscreen_api.py — EJScreen data fetcher for Sentinal.

HISTORY: EPA removed EJScreen from ejscreen.epa.gov on Feb 5, 2025 (the domain
no longer resolves). This module now queries the community rehost of EJScreen
v2.32 maintained by the Public Environmental Data Partners (PEDP) — the
original EPA dataset served as a public ArcGIS Online FeatureServer, no API
key required. See https://screening-tools.com/epa-ejscreen.

Semantics: this is a point-in-polygon lookup returning the census BLOCK GROUP
containing the point — not the old broker's ring-buffer aggregation. The
`radius_miles` parameter is kept for signature compatibility and echoed back,
but results describe the containing block group.

    data = await get_ejscreen_data(lat=29.4241, lon=-98.4936)
"""

import logging

import httpx

logger = logging.getLogger("ejmapper")

# PEDP rehost of the EPA EJScreen v2.32 block-group percentile layer.
EJSCREEN_URL = (
    "https://services2.arcgis.com/w4yiQqB14ZaAGzJq/arcgis/rest/services/"
    "EJScreen_US_Percentiles_Block_Group_gdb_V_2.32_(Parent)_view/"
    "FeatureServer/0/query"
)

# ── Field maps: EJScreen v2.32 field name → snake_case key used in the app ───
# Reference: EJScreen 2.32 schema as served by the PEDP FeatureServer (verified
# live 2026-07). Raw values pair with P_-prefixed national percentiles.
# NOTE: the old AirToxScreen fields (CANCER/RESP) were removed in v2.32 and
# replaced by RSEI_AIR (toxicity-weighted releases to air). NO2 and DWATER
# (drinking water non-compliance) are new in this release.

ENV_FIELDS: dict[str, str] = {
    # Air quality
    "PM25":       "pm25_avg_ugm3",           # Particulate matter 2.5, annual avg (μg/m³)
    "OZONE":      "ozone_ppb",               # Summer ozone average (ppb)
    "NO2":        "no2_ppb",                 # Nitrogen dioxide, annual avg (ppb)
    "DSLPM":      "diesel_pm_ugm3",          # Diesel particulate matter (μg/m³)
    "RSEI_AIR":   "toxic_releases_air",      # Toxicity-weighted industrial air releases (index)
    # Traffic & built environment
    "PTRAF":      "traffic_proximity",       # Distance-weighted daily traffic count (index)
    "PRE1960PCT": "lead_paint_pct",          # Fraction of housing built before 1960 (0–1)
    # Hazardous sites
    "PNPL":       "superfund_proximity",     # Superfund (NPL) sites / km²
    "PRMP":       "rmp_facility_proximity",  # RMP risk-management facilities / km²
    "PTSDF":      "hazwaste_proximity",      # Hazardous waste treatment sites / km²
    # Water & underground
    "UST":        "underground_storage_tanks",  # UST + leaking UST count / km²
    "PWDIS":      "wastewater_discharge",    # Toxicity-weighted effluent flow (index)
    "DWATER":     "drinking_water_noncompliance",  # Drinking water non-compliance (index)
}

# National percentile ranks (0–100; 80 = worse than 80% of the US population).
# P_<FIELD> = percentile of the raw environmental indicator itself — NOT the
# demographically weighted EJ index (those are the D2_/P_D2_ fields, which we
# deliberately do not use for the per-indicator "worse than X% of the US" claim).
ENV_PERCENTILE_FIELDS: dict[str, str] = {
    "P_PM25":     "pm25_pctile_national",
    "P_OZONE":    "ozone_pctile_national",
    "P_NO2":      "no2_pctile_national",
    "P_DSLPM":    "diesel_pm_pctile_national",
    "P_RSEI_AIR": "toxic_releases_pctile_national",
    "P_PTRAF":    "traffic_pctile_national",
    "P_LDPNT":    "lead_paint_pctile_national",
    "P_PNPL":     "superfund_pctile_national",
    "P_PRMP":     "rmp_pctile_national",
    "P_PTSDF":    "hazwaste_pctile_national",
    "P_UST":      "ust_pctile_national",
    "P_PWDIS":    "wastewater_pctile_national",
    "P_DWATER":   "drinking_water_pctile_national",
}

DEMO_FIELDS: dict[str, str] = {
    "LOWINCPCT":     "pct_low_income",
    "PEOPCOLORPCT":  "pct_minority",          # renamed from MINORPCT in v2.32
    "LESSHSPCT":     "pct_no_hs_diploma",
    "LINGISOPCT":    "pct_linguistically_isolated",
    "UNDER5PCT":     "pct_under_5",
    "OVER64PCT":     "pct_over_64",
}

_OUT_FIELDS = ",".join(
    ["ID", "ST_ABBREV", "CNTY_NAME"]
    + list(ENV_FIELDS) + list(ENV_PERCENTILE_FIELDS) + list(DEMO_FIELDS)
)


async def get_ejscreen_data(
    lat: float,
    lon: float,
    radius_miles: float = 1.0,
) -> dict:
    """
    Fetch EJScreen v2.32 indicators for the census block group containing the
    point (via the PEDP community rehost — EPA's own service was shut down).

    Returns a dict with "location", "environmental" (13 raw values),
    "percentiles" (13 national ranks, higher = more burdened), "demographic"
    (6 fractions 0–1), and "_raw" (full attribute row — strip before sending on).

    Raises:
        ValueError:             no block group found (outside US coverage).
        httpx.HTTPStatusError:  server returned a non-2xx response.
        httpx.TimeoutException: server timed out.
        httpx.ConnectError:     server unreachable.
    """
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError(f"Invalid coordinates: lat={lat}, lon={lon}")

    params = {
        "geometry":     f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR":         "4326",
        "spatialRel":   "esriSpatialRelIntersects",
        "returnGeometry": "false",
        "outFields":    _OUT_FIELDS,
        "f":            "json",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.get(EJSCREEN_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()

    # ArcGIS reports errors inside a 200 payload — normalize those to HTTP-ish
    # failures so main.py's fallback logic treats them like an outage.
    if "error" in payload:
        logger.warning("EJScreen mirror error payload: %s", payload["error"])
        raise httpx.HTTPStatusError(
            "EJScreen mirror returned an error payload",
            request=resp.request, response=resp,
        )

    features = payload.get("features") or []
    if not features:
        logger.warning("EJScreen mirror returned no block group for (%s, %s)", lat, lon)
        raise ValueError("EJScreen returned no data for the requested location.")

    row = features[0].get("attributes", {})
    return {
        "location": {
            "lat": lat,
            "lon": lon,
            "radius_miles": radius_miles,          # kept for compatibility
            "granularity": "block_group",          # point-in-polygon, not a ring buffer
            "block_group_id": row.get("ID"),
            "county": row.get("CNTY_NAME"),
            "state": row.get("ST_ABBREV"),
        },
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
