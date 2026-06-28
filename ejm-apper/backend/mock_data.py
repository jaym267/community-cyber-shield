"""
mock_data.py — EJScreen mock-data fallback for EJMapper
──────────────────────────────────────────────────────────────────────────────
The EPA EJScreen API (ejscreen.epa.gov) goes down periodically. When it does,
the main /api/neighborhood/{zip} route can fall back to this module so the app
keeps working end-to-end for any US zip code.

generate_mock_ejscreen() returns the EXACT same shape as
ejscreen_api.get_ejscreen_data() (minus the "_raw" key), so nothing downstream
— the Claude report card, the JSON response, the frontend — needs to change.

  - Zip 78207 (San Antonio west side, a heavily burdened neighborhood) gets
    hand-tuned realistic values.
  - Every other zip gets deterministic, plausible synthetic values seeded by the
    zip code, so the same zip always returns the same numbers.

This file can be deleted once EJScreen is reliably back, or kept as a permanent
resilience fallback (recommended — EPA outages recur).
"""

import random

# Pull the canonical snake_case key names straight from the real fetcher so the
# mock and live paths can never drift apart.
from ejscreen_api import DEMO_FIELDS, ENV_FIELDS, ENV_PERCENTILE_FIELDS

ENV_KEYS = list(ENV_FIELDS.values())              # 12 environmental indicator keys
PCT_KEYS = list(ENV_PERCENTILE_FIELDS.values())   # 12 national percentile keys
DEMO_KEYS = list(DEMO_FIELDS.values())            #  6 demographic fraction keys


# ── Hand-tuned realistic values for zip 78207 ─────────────────────────────────
# San Antonio's west side — one of the most environmentally burdened zips in the
# city: heavy traffic/diesel corridors, older housing stock (lead paint), high
# air-toxics cancer risk, and a low-income, majority-minority population.
MOCK_78207 = {
    "environmental": {
        "pm25_avg_ugm3":              9.8,     # μg/m³ — above the WHO 5 μg/m³ guideline
        "ozone_ppb":                  62.0,    # ppb — summer average
        "diesel_pm_ugm3":             0.71,    # μg/m³ — elevated near highways/rail
        "air_toxics_cancer_risk":     46.0,    # lifetime cancer risk per million
        "air_toxics_resp_hazard":     0.62,    # respiratory hazard index
        "traffic_proximity":          1900.0,  # AADT-weighted vehicles/meter
        "lead_paint_pct":             0.78,    # fraction of homes built pre-1960
        "superfund_proximity":        0.21,    # NPL sites / km²
        "rmp_facility_proximity":     1.4,     # RMP facilities / km²
        "hazwaste_proximity":         3.2,     # TSDF sites / km²
        "underground_storage_tanks":  8.6,     # UST + LUST count / km²
        "wastewater_discharge":       0.34,    # toxicity-weighted effluent
    },
    "percentiles": {
        "pm25_pctile_national":       72.0,
        "ozone_pctile_national":      68.0,
        "diesel_pm_pctile_national":  88.0,
        "cancer_risk_pctile_national": 84.0,
        "resp_hazard_pctile_national": 80.0,
        "traffic_pctile_national":    91.0,
        "lead_paint_pctile_national": 94.0,
        "superfund_pctile_national":  76.0,
        "rmp_pctile_national":        83.0,
        "hazwaste_pctile_national":   89.0,
        "ust_pctile_national":        85.0,
        "wastewater_pctile_national": 70.0,
    },
    "demographic": {
        "pct_low_income":              0.71,
        "pct_minority":                0.96,
        "pct_no_hs_diploma":           0.38,
        "pct_linguistically_isolated": 0.19,
        "pct_under_5":                 0.08,
        "pct_over_64":                 0.12,
    },
}

# Plausible value ranges for synthetic generation, keyed by the same snake_case
# names. Used for every zip other than 78207.
_ENV_RANGES = {
    "pm25_avg_ugm3":             (5.0, 12.0),
    "ozone_ppb":                 (45.0, 70.0),
    "diesel_pm_ugm3":            (0.10, 0.80),
    "air_toxics_cancer_risk":    (20.0, 50.0),
    "air_toxics_resp_hazard":    (0.20, 0.70),
    "traffic_proximity":         (200.0, 2200.0),
    "lead_paint_pct":            (0.05, 0.80),
    "superfund_proximity":       (0.0, 0.40),
    "rmp_facility_proximity":    (0.10, 1.6),
    "hazwaste_proximity":        (0.20, 3.5),
    "underground_storage_tanks": (1.0, 10.0),
    "wastewater_discharge":      (0.0, 0.50),
}

_DEMO_RANGES = {
    "pct_low_income":              (0.10, 0.70),
    "pct_minority":                (0.10, 0.95),
    "pct_no_hs_diploma":           (0.03, 0.35),
    "pct_linguistically_isolated": (0.0, 0.25),
    "pct_under_5":                 (0.04, 0.09),
    "pct_over_64":                 (0.08, 0.22),
}


def generate_mock_ejscreen(
    lat: float,
    lon: float,
    zip_code: str | None = None,
    radius_miles: float = 1.0,
) -> dict:
    """
    Return mock EJScreen data in the same shape as get_ejscreen_data().

    Args:
        lat, lon:     Coordinates to echo back in "location" (from zip_to_latlon).
        zip_code:     5-digit zip. "78207" returns hand-tuned realistic data;
                      any other value (or None) returns deterministic synthetic
                      data seeded by the zip (None → seeded by coordinates).
        radius_miles: Echoed back in "location".

    Returns:
        {"location": {...}, "environmental": {...}, "percentiles": {...},
         "demographic": {...}}  — no "_raw" key.
    """
    location = {"lat": lat, "lon": lon, "radius_miles": radius_miles}

    if zip_code == "78207":
        return {
            "location": location,
            "environmental": dict(MOCK_78207["environmental"]),
            "percentiles": dict(MOCK_78207["percentiles"]),
            "demographic": dict(MOCK_78207["demographic"]),
        }

    # Deterministic seed: same zip → same numbers. Fall back to coords if no zip.
    if zip_code and zip_code.isdigit():
        seed = int(zip_code)
    else:
        seed = int(abs(lat * 1000) + abs(lon * 1000))
    rng = random.Random(seed)

    environmental = {
        key: round(rng.uniform(*_ENV_RANGES[key]), 2) for key in ENV_KEYS
    }

    # Percentiles loosely track the raw value's position within its range, with a
    # little jitter, so a high pollutant value reads as a high national rank.
    percentiles = {}
    for env_key, pct_key in zip(ENV_KEYS, PCT_KEYS):
        lo, hi = _ENV_RANGES[env_key]
        frac = (environmental[env_key] - lo) / (hi - lo) if hi > lo else 0.5
        pct = frac * 100 + rng.uniform(-8, 8)
        percentiles[pct_key] = round(max(1.0, min(99.0, pct)), 1)

    demographic = {
        key: round(rng.uniform(*_DEMO_RANGES[key]), 3) for key in DEMO_KEYS
    }

    return {
        "location": location,
        "environmental": environmental,
        "percentiles": percentiles,
        "demographic": demographic,
    }
