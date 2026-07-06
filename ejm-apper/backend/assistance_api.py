"""
assistance_api.py — hazard history + "how to get help" for any US zip.

Two live keyless federal sources plus a curated assistance directory:

  county    — FCC Area API (geo.fcc.gov): lat/lon → county FIPS + name.
  disasters — FEMA OpenFEMA DisasterDeclarationsSummaries (v2): federally
              declared disasters for that county, newest first, with flags
              for when Individual Assistance (money/housing help for
              residents) was made available.
  resources — curated national assistance programs (211, DisasterAssistance,
              LIHEAP, weatherization) plus state-specific extras. These are
              maintained by hand — every entry carries an official .gov/.org
              URL so the app never invents a phone number or program.

Both remote sources degrade independently: FCC failure → no county/disaster
data but resources still return; FEMA failure → disasters: null with the
resources intact. Never raises.
"""

import logging
import time
from urllib.parse import quote

import httpx

logger = logging.getLogger("ejmapper")

FCC_AREA_URL = "https://geo.fcc.gov/api/census/area"
FEMA_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"

# How many declarations to show, and how recent an incident must be (no end
# date, or ended within this window) to be badged "recent".
_MAX_DECLARATIONS = 8
_RECENT_DAYS = 120


async def county_for_point(client: httpx.AsyncClient, lat: float, lon: float) -> dict | None:
    """{county_fips, county_name, state_code, state_name} or None."""
    r = await client.get(FCC_AREA_URL, params={"lat": lat, "lon": lon, "format": "json"})
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        return None
    first = results[0]
    return {
        "county_fips": first.get("county_fips"),
        "county_name": first.get("county_name"),
        "state_code": first.get("state_code"),
        "state_name": first.get("state_name"),
    }


async def fema_declarations(client: httpx.AsyncClient, county_fips: str) -> list[dict]:
    """Recent federal disaster declarations for a 5-digit county FIPS."""
    state_fips, county3 = county_fips[:2], county_fips[2:]
    # OpenFEMA wants OData params with a literal '$' — encode it ourselves;
    # naive param encoding has bitten us here before (Drupal 404 page).
    flt = quote(f"fipsStateCode eq '{state_fips}' and fipsCountyCode eq '{county3}'")
    url = (f"{FEMA_URL}?%24filter={flt}"
           f"&%24orderby={quote('declarationDate desc')}&%24top={_MAX_DECLARATIONS}")
    r = await client.get(url)
    r.raise_for_status()
    rows = r.json().get("DisasterDeclarationsSummaries", [])

    now = time.time()
    out = []
    for d in rows:
        end = d.get("incidentEndDate")
        recent = end is None
        if end:
            try:
                end_ts = time.mktime(time.strptime(end[:10], "%Y-%m-%d"))
                recent = (now - end_ts) < _RECENT_DAYS * 86400
            except ValueError:
                pass
        out.append({
            "title": d.get("declarationTitle", "").title(),
            "type": d.get("incidentType"),
            "declared": (d.get("declarationDate") or "")[:10],
            "incident_begin": (d.get("incidentBeginDate") or "")[:10] or None,
            "incident_end": (end or "")[:10] or None,
            "individual_assistance": bool(
                d.get("ihProgramDeclared") or d.get("iaProgramDeclared")),
            "public_assistance": bool(d.get("paProgramDeclared")),
            "recent": recent,
            "fema_id": d.get("femaDeclarationString"),
        })
    return out


def assistance_resources(state_code: str | None) -> list[dict]:
    """Curated help directory — national programs plus state extras."""
    # Every entry carries desc (EN) and desc_es (ES) — the frontend picks by
    # the user's language toggle. Program names stay official/untranslated.
    resources = [
        {
            "name": "211 Helpline",
            "desc": "Free 24/7 referrals for utility bills, cooling/heating help, "
                    "food, and housing — the fastest way to find local aid.",
            "desc_es": "Referencias gratuitas 24/7 para facturas de servicios, ayuda "
                       "de enfriamiento/calefacción, comida y vivienda — la forma más "
                       "rápida de encontrar ayuda local.",
            "contact": "Dial 211",
            "url": "https://www.211.org",
        },
        {
            "name": "FEMA Disaster Assistance",
            "desc": "Apply for federal help (housing, repairs, expenses) after a "
                    "presidentially declared disaster in your county.",
            "desc_es": "Solicite ayuda federal (vivienda, reparaciones, gastos) después "
                       "de un desastre declarado presidencialmente en su condado.",
            "contact": "1-800-621-3362",
            "url": "https://www.disasterassistance.gov",
        },
        {
            "name": "LIHEAP — Energy Bill Help",
            "desc": "Federal program that helps low-income households pay "
                    "cooling and heating bills. Apply through your state office.",
            "desc_es": "Programa federal que ayuda a hogares de bajos ingresos a pagar "
                       "facturas de aire acondicionado y calefacción. Solicite a través "
                       "de su oficina estatal.",
            "contact": None,
            "url": "https://www.acf.hhs.gov/ocs/map/liheap-map-state-and-territory-contact-listing",
        },
        {
            "name": "Weatherization Assistance Program",
            "desc": "Free home weatherization (insulation, sealing, efficiency "
                    "repairs) for income-eligible households — lowers heat risk "
                    "and energy bills permanently.",
            "desc_es": "Climatización gratuita del hogar (aislamiento, sellado, "
                       "reparaciones de eficiencia) para hogares elegibles por ingresos "
                       "— reduce el riesgo de calor y las facturas de energía "
                       "permanentemente.",
            "contact": None,
            "url": "https://www.energy.gov/scep/wap/weatherization-assistance-program",
        },
        {
            "name": "findhelp.org",
            "desc": "Search thousands of local free/reduced-cost programs by zip "
                    "code — health, housing, transit, legal aid.",
            "desc_es": "Busque miles de programas locales gratuitos o de bajo costo por "
                       "código postal — salud, vivienda, transporte, ayuda legal.",
            "contact": None,
            "url": "https://www.findhelp.org",
        },
    ]
    if state_code == "TX":
        resources.extend([
            {
                "name": "2-1-1 Texas",
                "desc": "Texas-specific helpline and program search, including "
                        "utility assistance and summer cooling programs.",
                "desc_es": "Línea de ayuda y búsqueda de programas de Texas, incluyendo "
                           "asistencia con servicios públicos y programas de "
                           "enfriamiento de verano.",
                "contact": "Dial 211",
                "url": "https://www.211texas.org",
            },
            {
                "name": "Help for Texans (TDHCA)",
                "desc": "State directory for rent, utility, and home-repair "
                        "assistance providers near you.",
                "desc_es": "Directorio estatal de proveedores de asistencia para renta, "
                           "servicios públicos y reparaciones del hogar cerca de usted.",
                "contact": None,
                "url": "https://www.tdhca.texas.gov/help-for-texans",
            },
        ])
    return resources


async def get_assistance(lat: float, lon: float) -> dict:
    """The /api/assistance payload. Per-source graceful degradation."""
    county = None
    disasters = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        try:
            county = await county_for_point(client, lat, lon)
        except Exception as e:
            logger.warning("FCC county lookup failed: %r", e)
        if county and county.get("county_fips"):
            try:
                disasters = await fema_declarations(client, county["county_fips"])
            except Exception as e:
                logger.warning("FEMA declarations failed: %r", e)

    return {
        "county": county,
        "disasters": disasters,   # null = FEMA/FCC unavailable; [] = none found
        "resources": assistance_resources((county or {}).get("state_code")),
        "sources": {
            "county": "live" if county else "unavailable",
            "disasters": "live" if disasters is not None else "unavailable",
            "resources": "curated",
        },
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
