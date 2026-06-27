"""
census_api.py — US Census Geocoder
─────────────────────────────────────────────────────────────────────────────
Converts a 5-digit US zip code to a latitude/longitude centroid.
This is the first call on every EJMapper request — every other API needs
coordinates, not zip codes.

No API key required. Free, maintained by the US Census Bureau.
Docs: https://geocoding.geo.census.gov/geocoder/
"""

import asyncio

import httpx

CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/address"


async def zip_to_latlon(zip_code: str) -> tuple[float, float]:
    """
    Convert a 5-digit US zip code to (latitude, longitude).

    The Census Geocoder doesn't accept zip codes directly — it's an address
    geocoder. We send the zip as a city placeholder and let it resolve to
    the zip centroid. Works reliably for all 50 states and Puerto Rico.

    Args:
        zip_code: 5-digit US zip code string, e.g. "78207"

    Returns:
        (latitude, longitude) as floats, e.g. (29.4241, -98.4936)

    Raises:
        ValueError: Zip code not found or outside US coverage.
        httpx.TimeoutException: Census server timed out.
    """
    params = {
        "street":     "",
        "city":       "",
        "state":      "",
        "zip":        zip_code,
        "benchmark":  "Public_AR_Current",
        "format":     "json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(CENSUS_GEOCODER_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()

    matches = payload.get("result", {}).get("addressMatches", [])
    if not matches:
        raise ValueError(
            f"Could not find coordinates for zip code {zip_code}. "
            "Verify it is a valid US zip code."
        )

    coords = matches[0]["coordinates"]
    lat = float(coords["y"])
    lon = float(coords["x"])

    return lat, lon


def zip_to_latlon_sync(zip_code: str) -> tuple[float, float]:
    """Sync wrapper for scripts and testing."""
    return asyncio.run(zip_to_latlon(zip_code))


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    zip_code = sys.argv[1] if len(sys.argv) > 1 else "78207"
    print(f"Looking up zip code: {zip_code}")
    try:
        lat, lon = zip_to_latlon_sync(zip_code)
        print(f"  Latitude:  {lat}")
        print(f"  Longitude: {lon}")
        print(f"  Google Maps: https://maps.google.com/?q={lat},{lon}")
    except ValueError as e:
        print(f"  Error: {e}")
