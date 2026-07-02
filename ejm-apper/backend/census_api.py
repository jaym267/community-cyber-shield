"""census_api.py — Nominatim geocoding helpers (zip ↔ coordinates)."""

import httpx

_NOMINATIM = "https://nominatim.openstreetmap.org"
# Nominatim's usage policy requires an identifying User-Agent on every request.
_HEADERS = {"User-Agent": "EJMapper/1.0 (github.com/jaym267/community-cyber-shield)"}


async def zip_to_latlon(zip_code: str) -> tuple[float, float]:
    """Resolve a US zip code to (lat, lon). Raises ValueError if not found."""
    params = {"postalcode": zip_code, "country": "US", "format": "json", "limit": 1}
    async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS) as client:
        resp = await client.get(f"{_NOMINATIM}/search", params=params)
        resp.raise_for_status()
        results = resp.json()

    if not results:
        # Keep the message generic — callers translate this to a clean 404, and
        # user input stays out of exception text that may reach logs/tracebacks.
        raise ValueError("Zip code could not be geocoded.")
    return float(results[0]["lat"]), float(results[0]["lon"])


async def latlon_to_zip(lat: float, lon: float) -> str | None:
    """Reverse-geocode a point to its zip code; returns None on any failure."""
    params = {"lat": lat, "lon": lon, "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=_HEADERS) as client:
            resp = await client.get(f"{_NOMINATIM}/reverse", params=params)
            resp.raise_for_status()
            return resp.json().get("address", {}).get("postcode")
    except Exception:
        return None
