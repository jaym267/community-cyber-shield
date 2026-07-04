"""
heat_api.py — NASA POWER air-temperature data for the San Antonio area.

Serves the /api/heat layer: a 30-day average of daily MAXIMUM AIR temperature
(NASA POWER parameter T2M_MAX) for every San Antonio / Bexar County zip.

Honesty notes baked into the design:
  - This is AIR temperature at 2 meters, NOT satellite land-surface temperature.
    The response's metric_note says so; the frontend must repeat it.
  - POWER's native grid is ~0.5° lat x 0.625° lon (~50 km). To avoid a blocky
    map, each zip's value is BILINEARLY INTERPOLATED from the four surrounding
    grid-cell centers — the standard way to read a point value out of gridded
    climate data (POWER itself is an interpolated reanalysis product). The
    smooth zip-to-zip variation is therefore real-data-derived, but the
    underlying measurement resolution is still ~50 km, and metric_note says so.

NASA POWER is free, keyless, and public (power.larc.nasa.gov). Daily data lags
real time by ~2-7 days, so the 30-day window ends 7 days before today.
"""

import asyncio
import json
import logging
import random
import time
from functools import lru_cache
from pathlib import Path

import httpx

logger = logging.getLogger("ejmapper")

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
_FILL_VALUE = -999.0          # POWER's missing-data sentinel
_GRID_LAT = 0.5               # POWER grid cell size, degrees
_GRID_LON = 0.625

DATA_DIR = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=1)
def load_sa_zips() -> tuple[dict, ...]:
    """San Antonio-area zip centroids from the committed Census Gazetteer subset."""
    raw = json.loads((DATA_DIR / "sa_zip_centroids.json").read_text(encoding="utf-8"))
    return tuple(raw["zips"])


@lru_cache(maxsize=1)
def load_zcta_boundaries() -> dict:
    """
    Bexar-area ZCTA polygons (Census TIGERweb 2020, generalized ~30 m),
    committed at backend/data/bexar_zcta_boundaries.geojson. Each feature's
    properties carry only {"zip"}; /api/heat-layers merges metric values in.
    """
    return json.loads(
        (DATA_DIR / "bexar_zcta_boundaries.geojson").read_text(encoding="utf-8"))


def _corner_cells(lat: float, lon: float) -> list[tuple[tuple[float, float], float]]:
    """
    The four POWER grid-cell centers surrounding a point, each paired with its
    bilinear interpolation weight (weights sum to 1). Cell centers sit on a
    lattice at k*0.5 + 0.25 (lat) and m*0.625 + 0.3125 (lon).
    """
    import math
    lat0 = math.floor((lat - _GRID_LAT / 2) / _GRID_LAT) * _GRID_LAT + _GRID_LAT / 2
    lon0 = math.floor((lon - _GRID_LON / 2) / _GRID_LON) * _GRID_LON + _GRID_LON / 2
    lat1, lon1 = lat0 + _GRID_LAT, lon0 + _GRID_LON
    ty = (lat - lat0) / _GRID_LAT
    tx = (lon - lon0) / _GRID_LON
    r3 = lambda v: round(v, 4)
    return [
        ((r3(lat0), r3(lon0)), (1 - ty) * (1 - tx)),
        ((r3(lat0), r3(lon1)), (1 - ty) * tx),
        ((r3(lat1), r3(lon0)), ty * (1 - tx)),
        ((r3(lat1), r3(lon1)), ty * tx),
    ]


def _window() -> tuple[str, str]:
    """30-day window ending 7 days ago (POWER lags real time)."""
    end = time.time() - 7 * 86400
    start = end - 30 * 86400
    fmt = lambda t: time.strftime("%Y%m%d", time.gmtime(t))
    return fmt(start), fmt(end)


async def _fetch_power_cell(
    client: httpx.AsyncClient, sem: asyncio.Semaphore,
    lat: float, lon: float, start: str, end: str,
) -> float | None:
    """Average T2M_MAX (°F) for one grid cell over the window; None on failure."""
    async with sem:
        resp = await client.get(POWER_URL, params={
            "parameters": "T2M_MAX",
            "community": "RE",
            "latitude": lat,
            "longitude": lon,
            "start": start,
            "end": end,
            "format": "JSON",
        })
        resp.raise_for_status()
        days = (
            resp.json()
            .get("properties", {})
            .get("parameter", {})
            .get("T2M_MAX", {})
        )
        vals = [v for v in days.values() if v is not None and v > _FILL_VALUE]
        if not vals:
            return None
        avg_c = sum(vals) / len(vals)
        return round(avg_c * 9 / 5 + 32, 1)


def generate_mock_heat(zips: tuple[dict, ...]) -> dict[str, float]:
    """Deterministic per-zip fallback (same seeding style as mock_data.py)."""
    out = {}
    for z in zips:
        rng = random.Random(int(z["zip"]))
        out[z["zip"]] = round(rng.uniform(94.0, 102.0), 1)
    return out


async def get_heat_data() -> dict:
    """
    Build the /api/heat payload. Collects the unique POWER grid cells that
    surround the SA zip centroids (each zip needs its 4 bilinear corners),
    fetches every unique cell once (bounded concurrency), then bilinearly
    interpolates each zip's temperature from whichever of its corners
    succeeded (weights renormalized over available corners). Zips with no
    live corner fall back to seeded mock values. `source` is "live", "mock",
    or "mixed".
    """
    zips = load_sa_zips()
    start, end = _window()

    # Every unique corner cell needed by any zip.
    corners_by_zip = {z["zip"]: _corner_cells(z["lat"], z["lon"]) for z in zips}
    unique_cells = sorted({cell for corners in corners_by_zip.values()
                           for cell, _w in corners})

    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        results = await asyncio.gather(
            *[_fetch_power_cell(client, sem, glat, glon, start, end)
              for (glat, glon) in unique_cells],
            return_exceptions=True,
        )

    cell_vals: dict[tuple[float, float], float] = {}
    for cell, res in zip(unique_cells, results):
        if isinstance(res, BaseException):
            logger.warning("POWER cell %s failed: %r", cell, res)
        elif res is not None:
            cell_vals[cell] = res

    mock_vals = generate_mock_heat(zips)
    zip_temps: dict[str, float] = {}
    live_zips = mock_zips = 0
    for z in zips:
        avail = [(cell_vals[cell], w) for cell, w in corners_by_zip[z["zip"]]
                 if cell in cell_vals and w > 0]
        wsum = sum(w for _v, w in avail)
        if wsum > 0:
            zip_temps[z["zip"]] = round(sum(v * w for v, w in avail) / wsum, 1)
            live_zips += 1
        else:
            zip_temps[z["zip"]] = mock_vals[z["zip"]]
            mock_zips += 1

    source = "live" if mock_zips == 0 else ("mock" if live_zips == 0 else "mixed")
    iso = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return {
        "region": "san_antonio",
        "metric": "avg_daily_max_air_temp_f",
        "metric_note": (
            "30-day average of daily maximum AIR temperature (NASA POWER "
            "T2M_MAX) — not land-surface temperature. Values are bilinearly "
            "interpolated between POWER's ~50 km grid cells; fine zip-to-zip "
            "differences are smoothed estimates, not station measurements."
        ),
        "period_start": iso(start),
        "period_end": iso(end),
        "zips": zip_temps,
        "source": source,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
