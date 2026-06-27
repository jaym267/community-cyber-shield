import asyncio
import httpx

async def zip_to_latlon(zip_code: str) -> tuple[float, float]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "postalcode": zip_code,
        "country":    "US",
        "format":     "json",
        "limit":      1,
    }
    headers = {"User-Agent": "EJMapper/1.0"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        results = resp.json()

    if not results:
        raise ValueError(f"Could not find coordinates for zip code {zip_code}.")

    return float(results[0]["lat"]), float(results[0]["lon"])

def zip_to_latlon_sync(zip_code: str) -> tuple[float, float]:
    return asyncio.run(zip_to_latlon(zip_code))