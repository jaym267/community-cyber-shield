"""
map_layers.py — GeoJSON map-layer data for EJMapper
──────────────────────────────────────────────────────────────────────────────
Builds the three map overlays the frontend renders on top of Mapbox:

  1. air_quality  — a heatmap point cloud (FeatureCollection of weighted points)
  2. facilities   — industrial/regulated facility markers (EPA ECHO)
  3. green_spaces — parks/green areas as polygons (OpenStreetMap Overpass)

Like the EJScreen path, each source attempts a real API and falls back to
deterministic, plausible synthetic data when the API is unreachable — so the
layers always work, even during government API outages. Outputs are standard
GeoJSON so the frontend can drop them straight into Mapbox Source/Layer.

All generators are seeded by coordinates, so the same location always produces
the same synthetic features.
"""

import math
import random

import httpx

ECHO_URL = "https://echodata.epa.gov/echo/echo_rest_services.get_facilities"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

_MILES_PER_DEG_LAT = 69.0


def _rng(lat: float, lon: float, salt: int = 0) -> random.Random:
    """Deterministic RNG seeded by coordinates (so a location is stable)."""
    return random.Random(int(abs(lat) * 1e4 + abs(lon) * 1e4) + salt)


def _miles_to_deg(lat: float, miles: float) -> tuple[float, float]:
    """Convert a mile offset to (lat_deg, lon_deg) at this latitude."""
    dlat = miles / _MILES_PER_DEG_LAT
    dlon = miles / (_MILES_PER_DEG_LAT * max(0.1, math.cos(math.radians(lat))))
    return dlat, dlon


# ── 1. Air-quality heatmap ────────────────────────────────────────────────────

def air_quality_geojson(
    lat: float,
    lon: float,
    intensity: float = 60.0,
    radius_miles: float = 1.2,
    n_points: int = 60,
) -> dict:
    """
    A heatmap point cloud around the location. `intensity` is the area's air-
    pollution percentile (0–100); higher = hotter. Weight peaks at center and
    decays outward, with noise so it reads like a real pollution gradient.

    NOTE: synthetic by design — EJScreen reports one value per point, not a
    spatial field, so we model a plausible gradient from that single value.
    """
    rng = _rng(lat, lon, salt=1)
    base = max(0.05, min(1.0, intensity / 100.0))
    features = []
    for _ in range(n_points):
        # Random point within the radius (sqrt for uniform area distribution).
        r = radius_miles * math.sqrt(rng.random())
        theta = rng.uniform(0, 2 * math.pi)
        dlat, dlon = _miles_to_deg(lat, r)
        plat = lat + dlat * math.sin(theta)
        plon = lon + dlon * math.cos(theta)
        # Weight: peak at center, decay with distance, plus jitter.
        decay = 1.0 - (r / radius_miles)
        weight = max(0.0, min(1.0, base * (0.4 + 0.6 * decay) + rng.uniform(-0.1, 0.1)))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [plon, plat]},
            "properties": {"weight": round(weight, 3)},
        })
    return {"type": "FeatureCollection", "features": features}


# ── 2. Industrial facility markers (EPA ECHO) ─────────────────────────────────

_FACILITY_TYPES = [
    "Metal finishing plant",
    "Auto body / paint shop",
    "Concrete batch plant",
    "Chemical storage facility",
    "Power generation station",
    "Waste transfer station",
    "Petroleum bulk storage",
    "Industrial laundry",
]


async def facilities_geojson(lat: float, lon: float, radius_miles: float = 1.5) -> dict:
    """
    Regulated facilities near the location as point markers. Tries EPA ECHO,
    falls back to deterministic synthetic facilities if ECHO is unreachable.
    """
    try:
        params = {
            "output": "JSON",
            "p_lat": lat,
            "p_long": lon,
            "p_radius": radius_miles,
            "responseset": "5",
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
            resp = await client.get(ECHO_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
        results = payload.get("Results", {})
        rows = results.get("Facilities", []) or []
        features = []
        for f in rows:
            flat, flon = f.get("FacLat"), f.get("FacLong")
            if flat is None or flon is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(flon), float(flat)]},
                "properties": {
                    "name": f.get("FacName", "Regulated facility"),
                    "type": f.get("FacComplianceStatus", "Facility"),
                    "source": "live",
                },
            })
        if features:
            return {"type": "FeatureCollection", "features": features, "source": "live"}
        # No rows → fall through to synthetic.
    except Exception:
        pass

    return _mock_facilities(lat, lon, radius_miles)


def _mock_facilities(lat: float, lon: float, radius_miles: float) -> dict:
    rng = _rng(lat, lon, salt=2)
    n = rng.randint(5, 9)
    features = []
    for i in range(n):
        r = radius_miles * math.sqrt(rng.random())
        theta = rng.uniform(0, 2 * math.pi)
        dlat, dlon = _miles_to_deg(lat, r)
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon + dlon * math.cos(theta), lat + dlat * math.sin(theta)],
            },
            "properties": {
                "name": f"{rng.choice(_FACILITY_TYPES)} #{i + 1}",
                "type": rng.choice(["Active", "Active", "Recent violation"]),
                "source": "mock",
            },
        })
    return {"type": "FeatureCollection", "features": features, "source": "mock"}


# ── 3. Green-space polygons (OpenStreetMap Overpass) ──────────────────────────

async def green_space_geojson(lat: float, lon: float, radius_miles: float = 1.5) -> dict:
    """
    Parks and green areas as polygons. Tries OSM Overpass, falls back to
    deterministic synthetic park polygons if Overpass is unreachable.
    """
    radius_m = int(radius_miles * 1609)
    query = f"""
    [out:json][timeout:10];
    (
      way["leisure"="park"](around:{radius_m},{lat},{lon});
      way["leisure"="garden"](around:{radius_m},{lat},{lon});
      way["landuse"="recreation_ground"](around:{radius_m},{lat},{lon});
    );
    out geom;
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0)) as client:
            resp = await client.post(OVERPASS_URL, data={"data": query})
            resp.raise_for_status()
            payload = resp.json()
        features = []
        for el in payload.get("elements", []):
            geom = el.get("geometry")
            if not geom or len(geom) < 3:
                continue
            ring = [[pt["lon"], pt["lat"]] for pt in geom]
            if ring[0] != ring[-1]:
                ring.append(ring[0])  # close the polygon
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {
                    "name": el.get("tags", {}).get("name", "Green space"),
                    "source": "live",
                },
            })
        if features:
            return {"type": "FeatureCollection", "features": features, "source": "live"}
    except Exception:
        pass

    return _mock_green_spaces(lat, lon, radius_miles)


def _mock_green_spaces(lat: float, lon: float, radius_miles: float) -> dict:
    rng = _rng(lat, lon, salt=3)
    n = rng.randint(2, 4)
    features = []
    for i in range(n):
        r = radius_miles * 0.7 * math.sqrt(rng.random())
        theta = rng.uniform(0, 2 * math.pi)
        dlat, dlon = _miles_to_deg(lat, r)
        clat = lat + dlat * math.sin(theta)
        clon = lon + dlon * math.cos(theta)
        # Build an irregular blob polygon around (clat, clon).
        size = rng.uniform(0.06, 0.18)  # miles
        sdlat, sdlon = _miles_to_deg(clat, size)
        ring = []
        for k in range(8):
            a = (k / 8) * 2 * math.pi
            jitter = rng.uniform(0.6, 1.2)
            ring.append([
                clon + sdlon * math.cos(a) * jitter,
                clat + sdlat * math.sin(a) * jitter,
            ])
        ring.append(ring[0])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"name": f"Park #{i + 1}", "source": "mock"},
        })
    return {"type": "FeatureCollection", "features": features, "source": "mock"}
