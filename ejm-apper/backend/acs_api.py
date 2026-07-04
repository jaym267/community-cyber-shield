"""
acs_api.py — US Census Bureau ACS 5-year demographics for ZCTAs.

(Named acs_api, not census_api — census_api.py is the Nominatim geocoding
module and predates this one.)

Fetches the three demographic vulnerability proxies used by the heat-
vulnerability score, per zip (ZCTA):
  - pct_65plus         share of population aged 65+  (B01001 sex-by-age cells)
  - median_income      median household income, $    (B19013_001E)
  - median_year_built  median year housing built     (B25035_001E)

The Census API is free; a key (CENSUS_API_KEY) is OPTIONAL — keyless works
at low volume and responses are cached upstream. Never hardcode the key.

ACS publishes sentinel values for suppressed/unavailable estimates (e.g.
-666666666); _clean() maps those to None so they can never leak into math.
ZCTAs approximate USPS zips but are not identical — documented in README.
"""

import logging
import os
import random

import httpx

logger = logging.getLogger("ejmapper")

_ACS_URL = "https://api.census.gov/data/2023/acs/acs5"

# B01001: sex by age. 020-025 = male 65+, 044-049 = female 65+.
_MALE_65 = [f"B01001_{i:03d}E" for i in range(20, 26)]
_FEMALE_65 = [f"B01001_{i:03d}E" for i in range(44, 50)]
_VARS = ["B01001_001E", *_MALE_65, *_FEMALE_65, "B19013_001E", "B25035_001E"]


def _clean(value) -> float | None:
    """ACS value → float, or None for sentinels/garbage (-666666666 etc.)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f <= -666666 or f < 0 else f


async def get_acs_zcta_data(zctas: list[str]) -> dict[str, dict]:
    """
    One batched ACS request for every requested ZCTA.

    Returns {zip: {"pct_65plus": 0.14|None, "median_income": 48750|None,
    "median_year_built": 1968|None}}. ZCTAs absent from ACS (tiny/military
    areas) are returned with all-None values. Raises on transport/HTTP
    errors — the caller decides whether to fall back to mock data.
    """
    params = {
        "get": ",".join(_VARS),
        "for": f"zip code tabulation area:{','.join(zctas)}",
    }
    key = os.getenv("CENSUS_API_KEY")
    if key:
        params["key"] = key

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.get(_ACS_URL, params=params)
        resp.raise_for_status()
        rows = resp.json()

    header = rows[0]
    idx = {name: header.index(name) for name in _VARS}
    zcta_col = header.index("zip code tabulation area")

    out: dict[str, dict] = {z: {"pct_65plus": None, "median_income": None,
                                "median_year_built": None} for z in zctas}
    for row in rows[1:]:
        z = row[zcta_col]
        if z not in out:
            continue
        total = _clean(row[idx["B01001_001E"]])
        seniors = [_clean(row[idx[v]]) for v in (*_MALE_65, *_FEMALE_65)]
        pct_65plus = None
        if total and total > 0 and all(s is not None for s in seniors):
            pct_65plus = round(sum(seniors) / total, 4)
        out[z] = {
            "pct_65plus": pct_65plus,
            "median_income": _clean(row[idx["B19013_001E"]]),
            "median_year_built": _clean(row[idx["B25035_001E"]]),
        }
    return out


def generate_mock_acs(zctas: list[str]) -> dict[str, dict]:
    """Deterministic seeded fallback, same style as the other mock generators."""
    out = {}
    for z in zctas:
        rng = random.Random(int(z) * 7)
        out[z] = {
            "pct_65plus": round(rng.uniform(0.07, 0.22), 4),
            "median_income": round(rng.uniform(32000, 110000), 0),
            "median_year_built": float(rng.randint(1950, 2005)),
        }
    return out
