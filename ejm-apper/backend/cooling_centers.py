"""
cooling_centers.py — City of San Antonio cooling-center locations.

Live source (verified 2026-07): the City of San Antonio GIS "Places to Stay
Cool" public Feature Service. Falls back to a committed snapshot of the same
feed (backend/data/sa_cooling_centers.json, source + retrieval date in-file)
when the live service is unreachable — labeled `source: "static_snapshot"`.

Cooling centers are a City of San Antonio program, so this layer is
SA-specific even though the heat choropleths are national.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

import httpx

logger = logging.getLogger("ejmapper")

DATA_DIR = Path(__file__).resolve().parent / "data"
COSA_COOLING_URL = (
    "https://services.arcgis.com/g1fRTDLeMgspWrYp/arcgis/rest/services/"
    "Cooling_Centers_Viewer/FeatureServer/0/query"
)


def _normalize(raw_features: list[dict]) -> list[dict]:
    feats = []
    for f in raw_features:
        p = f.get("properties", {})
        if (p.get("status") or "active").lower() != "active":
            continue
        geom = f.get("geometry")
        if not geom or geom.get("type") != "Point":
            continue
        feats.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "name": (p.get("NAME") or p.get("name") or "Cooling site").strip(),
                "address": (p.get("ADDRESS") or p.get("address") or "").strip(),
                "type": p.get("TYPE") or p.get("type") or "Cooling site",
                "phone": p.get("PHONE") or p.get("phone"),
                "zip": p.get("ZIP") or p.get("zip"),
            },
        })
    return feats


@lru_cache(maxsize=1)
def _load_static_centers() -> dict | None:
    try:
        raw = json.loads(
            (DATA_DIR / "sa_cooling_centers.json").read_text(encoding="utf-8"))
        return {"features": _normalize(raw["features"]),
                "retrieved": raw.get("_retrieved")}
    except Exception as e:
        logger.error("static cooling centers unavailable: %r", e)
        return None


async def get_cooling_centers() -> dict:
    """
    {centers: FeatureCollection, source: "live"|"static_snapshot"|"unavailable",
     retrieved: iso-date|None}. Never raises.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            r = await client.get(COSA_COOLING_URL, params={
                "where": "1=1",
                "outFields": "NAME,ADDRESS,TYPE,PHONE,ZIP,status",
                "f": "geojson",
            })
            r.raise_for_status()
            payload = r.json()
        feats = _normalize(payload.get("features", []))
        if feats:
            return {
                "centers": {"type": "FeatureCollection", "features": feats},
                "source": "live",
                "retrieved": None,
            }
        logger.warning("CoSA cooling feed returned 0 active centers — using snapshot")
    except Exception as e:
        logger.warning("CoSA cooling feed failed (%r) — using snapshot", e)

    static = _load_static_centers()
    if static:
        return {
            "centers": {"type": "FeatureCollection", "features": static["features"]},
            "source": "static_snapshot",
            "retrieved": static["retrieved"],
        }
    return {"centers": {"type": "FeatureCollection", "features": []},
            "source": "unavailable", "retrieved": None}
