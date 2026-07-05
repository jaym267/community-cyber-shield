"""
region_layers.py — nationwide, region-on-demand heat/canopy/vulnerability.

Given any searched US zip, builds the choropleth dataset for that zip and
every ZCTA within a ~12-mile radius:

  boundaries — Census TIGERweb 2020 ZCTA polygons, fetched at runtime for
               the region's zip list (generalized ~30 m), cached 7 days
  heat       — NASA POWER T2M_MAX, bilinearly interpolated per zip centroid;
               each unique ~50 km POWER cell is fetched once and cached 24 h,
               so overlapping regions share cells
  canopy     — San Antonio zips use the committed NLCD 2021 zonal means
               (sa_canopy.json fast path); other regions get BEST-EFFORT
               runtime zonal means from MRLC's public WCS (guarded by a
               lock + timeout; failure → canopy missing, weights renormalize)
  acs        — Census ACS 5-yr demographics for the region's zips (needs
               CENSUS_API_KEY; labeled mock otherwise)

IMPORTANT SEMANTICS: vulnerability components are min-max normalized ACROSS
THE REGION, so scores mean "relative to nearby zips" — the right framing for
urban-heat-island comparisons (which neighborhoods of THIS metro run hottest),
not a national ranking. The frontend legend says so.

Regions are keyed by the searched zip's centroid quantized to 0.1°, so
searches in the same part of a metro share one cached dataset.
"""

import asyncio
import gzip
import json
import logging
import math
import time
from functools import lru_cache
from pathlib import Path

import httpx

from acs_api import generate_mock_acs, get_acs_zcta_data
from heat_api import _corner_cells, _fetch_power_cell, _window, generate_mock_heat

logger = logging.getLogger("ejmapper")

DATA_DIR = Path(__file__).resolve().parent / "data"
TIGERWEB_ZCTA = ("https://tigerweb.geo.census.gov/arcgis/rest/services/"
                 "TIGERweb/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/1/query")
MRLC_WCS = "https://www.mrlc.gov/geoserver/ows"
TCC_COVERAGE = "mrlc_download__nlcd_tcc_conus_2021_v2021-4"

REGION_RADIUS_MI = 12.0
_MILES_PER_DEG_LAT = 69.0

# Module-local TTL cache (main.py's cache isn't importable without a cycle).
_cache: dict[str, tuple[float, object]] = {}
_CACHE_MAX = 600


def _cget(key: str):
    entry = _cache.get(key)
    if entry is None:
        return None
    if time.time() > entry[0]:
        _cache.pop(key, None)
        return None
    return entry[1]


def _cset(key: str, value, ttl: float) -> None:
    if len(_cache) >= _CACHE_MAX and key not in _cache:
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]), None)
    _cache[key] = (time.time() + ttl, value)


@lru_cache(maxsize=1)
def load_us_centroids() -> dict[str, tuple[float, float]]:
    """All ~33.8k US ZCTA centroids (Census 2023 Gazetteer, gzipped JSON)."""
    with gzip.open(DATA_DIR / "zcta_centroids_us.json.gz", "rt", encoding="utf-8") as f:
        raw = json.load(f)
    return {z: (lat, lon) for z, (lat, lon) in raw["zctas"].items()}


def _haversine_mi(lat1, lon1, lat2, lon2) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def region_zips(lat: float, lon: float, radius_mi: float = REGION_RADIUS_MI) -> list[dict]:
    """ZCTAs whose centroid is within radius_mi of the point (coarse prefilter
    by bounding box, then true haversine)."""
    dlat = radius_mi / _MILES_PER_DEG_LAT
    dlon = radius_mi / (_MILES_PER_DEG_LAT * max(0.1, math.cos(math.radians(lat))))
    out = []
    for z, (zlat, zlon) in load_us_centroids().items():
        if abs(zlat - lat) > dlat or abs(zlon - lon) > dlon:
            continue
        if _haversine_mi(lat, lon, zlat, zlon) <= radius_mi:
            out.append({"zip": z, "lat": zlat, "lon": zlon})
    out.sort(key=lambda r: r["zip"])
    return out


def region_key(lat: float, lon: float) -> str:
    """Quantize to 0.1 deg so nearby searches share one cached region."""
    return f"{round(lat, 1)}:{round(lon, 1)}"


# ── Heat (per-cell cached, bilinear per zip) ─────────────────────────────────

async def heat_for_zips(zips: list[dict]) -> tuple[dict[str, float], str]:
    """{zip: temp_f}, source flag. Unique POWER cells cached 24h individually."""
    start, end = _window()
    corners = {z["zip"]: _corner_cells(z["lat"], z["lon"]) for z in zips}
    needed = sorted({cell for cs in corners.values() for cell, _w in cs})

    cell_vals: dict[tuple[float, float], float] = {}
    to_fetch = []
    for cell in needed:
        hit = _cget(f"power:{cell[0]}:{cell[1]}:{end}")
        if hit is not None:
            cell_vals[cell] = hit
        else:
            to_fetch.append(cell)

    if to_fetch:
        sem = asyncio.Semaphore(4)
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            results = await asyncio.gather(
                *[_fetch_power_cell(client, sem, glat, glon, start, end)
                  for (glat, glon) in to_fetch],
                return_exceptions=True,
            )
        for cell, res in zip(to_fetch, results):
            if isinstance(res, BaseException):
                logger.warning("POWER cell %s failed: %r", cell, res)
            elif res is not None:
                cell_vals[cell] = res
                _cset(f"power:{cell[0]}:{cell[1]}:{end}", res, 24 * 3600)

    mock = generate_mock_heat(tuple({"zip": z["zip"]} for z in zips))
    temps: dict[str, float] = {}
    live = 0
    for z in zips:
        avail = [(cell_vals[c], w) for c, w in corners[z["zip"]] if c in cell_vals and w > 0]
        wsum = sum(w for _v, w in avail)
        if wsum > 0:
            temps[z["zip"]] = round(sum(v * w for v, w in avail) / wsum, 1)
            live += 1
        else:
            temps[z["zip"]] = mock[z["zip"]]
    source = "live" if live == len(zips) else ("mock" if live == 0 else "mixed")
    return temps, source


# ── Boundaries (runtime TIGERweb) ────────────────────────────────────────────

async def polygons_for_zips(zips: list[str]) -> dict | None:
    """GeoJSON FeatureCollection of ZCTA polygons; None on failure."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            r = await client.post(TIGERWEB_ZCTA, data={
                "where": "ZCTA5 IN ({})".format(",".join(f"'{z}'" for z in zips)),
                "outFields": "ZCTA5",
                "f": "geojson",
                "returnGeometry": "true",
                "maxAllowableOffset": "0.0003",
                "geometryPrecision": "5",
                "outSR": "4326",
            })
            r.raise_for_status()
            gj = r.json()
        if "features" not in gj:
            logger.warning("TIGERweb unexpected payload keys: %s", list(gj))
            return None
        return gj
    except Exception as e:
        logger.warning("TIGERweb polygons failed: %r", e)
        return None


# ── Canopy (SA static fast path + best-effort runtime zonal stats) ──────────

@lru_cache(maxsize=1)
def _sa_canopy() -> dict[str, float]:
    try:
        return json.loads((DATA_DIR / "sa_canopy.json").read_text(encoding="utf-8"))["zips"]
    except Exception:
        return {}


_canopy_lock = asyncio.Lock()


async def canopy_for_region(polygons: dict | None, zips: list[str]) -> tuple[dict[str, float], str]:
    """
    {zip: canopy_pct}. SA zips come from the committed NLCD zonal means.
    Everything else is computed at request time from an MRLC WCS raster
    subset — best-effort: any failure/timeout just yields missing canopy
    (the vulnerability formula renormalizes). Never raises.
    """
    out = {z: _sa_canopy()[z] for z in zips if z in _sa_canopy()}
    remaining = [z for z in zips if z not in out]
    if not remaining or polygons is None:
        return out, ("static_estimate" if out else "unavailable")

    feats = {f["properties"]["ZCTA5"]: f for f in polygons.get("features", [])
             if f.get("properties", {}).get("ZCTA5") in remaining and f.get("geometry")}
    if not feats:
        return out, ("static_estimate" if out else "unavailable")

    try:
        # Serialize raster work: one WCS fetch + zonal pass at a time, with a
        # hard time budget, so this can never pile up on a small server.
        async with asyncio.timeout(45):
            async with _canopy_lock:
                computed = await asyncio.to_thread(_zonal_canopy_sync, feats)
        out.update(computed)
        return out, "computed_estimate"
    except Exception as e:
        logger.warning("runtime canopy failed (%r) — serving without canopy", e)
        return out, ("static_estimate" if out else "unavailable")


def _zonal_canopy_sync(feats: dict[str, dict]) -> dict[str, float]:
    """Blocking: fetch NLCD TCC subset for the features' bbox, zonal-mean each."""
    import tempfile

    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from shapely.geometry import shape

    lons, lats = [], []
    for f in feats.values():
        g = shape(f["geometry"])
        b = g.bounds
        lons += [b[0], b[2]]
        lats += [b[1], b[3]]
    pad = 0.01
    lon0, lon1 = min(lons) - pad, max(lons) + pad
    lat0, lat1 = min(lats) - pad, max(lats) + pad

    params = {
        "service": "WCS", "version": "2.0.1", "request": "GetCoverage",
        "coverageId": TCC_COVERAGE,
        "subset": [f"Long({lon0},{lon1})", f"Lat({lat0},{lat1})"],
        "subsettingcrs": "http://www.opengis.net/def/crs/EPSG/0/4326",
        "outputCrs": "http://www.opengis.net/def/crs/EPSG/0/4326",
        "format": "image/geotiff",
    }
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        with httpx.stream("GET", MRLC_WCS, params=params, timeout=35.0) as r:
            r.raise_for_status()
            with path.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        result: dict[str, float] = {}
        with rasterio.open(path) as src:
            band = src.read(1)
            valid = band <= 100
            for z, f in feats.items():
                mask = geometry_mask([shape(f["geometry"])], out_shape=band.shape,
                                     transform=src.transform, invert=True)
                sel = band[mask & valid]
                if sel.size:
                    result[z] = round(float(np.mean(sel)), 1)
        return result
    finally:
        path.unlink(missing_ok=True)


# ── ACS (region-batched) ─────────────────────────────────────────────────────

async def acs_for_zips(zips: list[str]) -> tuple[dict[str, dict], str]:
    try:
        return await get_acs_zcta_data(zips), "live"
    except Exception as e:
        logger.warning("ACS unavailable (%r) — serving mock demographics", e)
        return generate_mock_acs(zips), "mock"
