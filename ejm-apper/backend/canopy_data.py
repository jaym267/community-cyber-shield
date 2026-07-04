"""
canopy_data.py — static tree-canopy estimates for the San Antonio area.

Serves /api/canopy from a committed dataset (backend/data/sa_canopy.json):
zonal mean of NLCD Tree Canopy Cover 2021 (v2021-4, USFS/MRLC) pixels within
each 2020 Census ZCTA polygon, computed offline via MRLC's public WCS. The
data file records its own provenance (_source/_method/_vintage).

These are STATIC ESTIMATES with a fixed vintage — the endpoint labels them
`source: "static_estimate"`, never "live". NLCD TCC reads urban canopy
conservatively compared to LiDAR studies (the City of San Antonio's own
assessment reports higher absolute percentages); relative differences
between zips are what the vulnerability score consumes.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("ejmapper")

DATA_DIR = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=1)
def get_canopy_data() -> dict:
    """
    The /api/canopy payload. On a missing/corrupt data file, returns an empty
    `zips` map with source "unavailable" (HTTP 200) so downstream consumers
    treat canopy as a missing component instead of failing the whole request.
    """
    try:
        raw = json.loads((DATA_DIR / "sa_canopy.json").read_text(encoding="utf-8"))
        zips = raw["zips"]
        # Cross-check against the centroid file so a half-updated data drop
        # gets noticed in logs rather than silently shrinking coverage.
        try:
            centroids = json.loads(
                (DATA_DIR / "sa_zip_centroids.json").read_text(encoding="utf-8"))
            expected = {z["zip"] for z in centroids["zips"]}
            missing = expected - set(zips)
            if missing:
                logger.warning("canopy data missing %d zips: %s",
                               len(missing), sorted(missing)[:8])
        except Exception:
            pass
        return {
            "region": "san_antonio",
            "metric": "tree_canopy_pct",
            "metric_note": (
                "Mean NLCD 2021 tree-canopy cover within each zip (ZCTA). "
                "Static estimate with a 2021 vintage — NLCD reads urban canopy "
                "conservatively vs. LiDAR surveys; compare zips relatively."
            ),
            "zips": zips,
            "source": "static_estimate",
            "vintage": raw.get("_vintage", "2021"),
        }
    except Exception as e:
        logger.error("canopy data unavailable: %r", e)
        return {
            "region": "san_antonio",
            "metric": "tree_canopy_pct",
            "zips": {},
            "source": "unavailable",
        }
