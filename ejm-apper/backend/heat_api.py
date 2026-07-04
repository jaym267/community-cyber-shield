"""
heat_api.py — NASA POWER air-temperature data for the San Antonio area.

Serves the /api/heat layer: a 30-day average of daily MAXIMUM AIR temperature
(NASA POWER parameter T2M_MAX) for every San Antonio / Bexar County zip.

Honesty notes baked into the design:
  - This is AIR temperature at 2 meters, NOT satellite land-surface temperature.
    The response's metric_note says so; the frontend must repeat it.
  - POWER's native grid is ~0.5° lat x 0.625° lon (~50 km). All of Bexar County
    spans only a handful of grid cells, so adjacent zips legitimately share the
    same value. We therefore fetch each UNIQUE grid cell once and assign the
    cell's value to every zip inside it — identical numbers across neighboring
    zips are the data's real resolution, not a bug.

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


def _grid_key(lat: float, lon: float) -> tuple[float, float]:
    """Snap a point to the center of its POWER grid cell."""
    glat = round((lat // _GRID_LAT) * _GRID_LAT + _GRID_LAT / 2, 3)
    glon = round((lon // _GRID_LON) * _GRID_LON + _GRID_LON / 2, 3)
    return (glat, glon)


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
    Build the /api/heat payload. Fetches each unique POWER grid cell covering
    the SA zip set (bounded concurrency), assigns cell averages to member zips,
    and falls back to seeded mock values per failed cell — or entirely, if
    every cell fails. `source` is "live", "mock", or "mixed".
    """
    zips = load_sa_zips()
    start, end = _window()

    cells: dict[tuple[float, float], list[str]] = {}
    for z in zips:
        cells.setdefault(_grid_key(z["lat"], z["lon"]), []).append(z["zip"])

    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        results = await asyncio.gather(
            *[_fetch_power_cell(client, sem, glat, glon, start, end)
              for (glat, glon) in cells],
            return_exceptions=True,
        )

    mock_vals = generate_mock_heat(zips)
    zip_temps: dict[str, float] = {}
    live_cells = failed_cells = 0
    for (cell, members), res in zip(cells.items(), results):
        if isinstance(res, BaseException) or res is None:
            failed_cells += 1
            if isinstance(res, BaseException):
                logger.warning("POWER cell %s failed: %r", cell, res)
            for zc in members:
                zip_temps[zc] = mock_vals[zc]
        else:
            live_cells += 1
            for zc in members:
                zip_temps[zc] = res

    source = "live" if failed_cells == 0 else ("mock" if live_cells == 0 else "mixed")
    iso = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return {
        "region": "san_antonio",
        "metric": "avg_daily_max_air_temp_f",
        "metric_note": (
            "30-day average of daily maximum AIR temperature (NASA POWER "
            "T2M_MAX, ~50 km grid) — not land-surface temperature. Adjacent "
            "zips share values at this grid resolution."
        ),
        "period_start": iso(start),
        "period_end": iso(end),
        "zips": zip_temps,
        "source": source,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
