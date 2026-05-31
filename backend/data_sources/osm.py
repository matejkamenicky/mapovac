"""OSM Overpass klient.

Stahuje vektorová data relevantní pro OB mapu pro daný bbox WGS84:
- highways (silnice, lesní cesty, pěšiny) → ISOM 502–509
- waterways + water (toky, plochy)         → ISOM 301–305
- buildings                                → ISOM 521
- barriers (ploty)                         → ISOM 524
- power lines                              → ISOM 516
- landuse / natural (otevřená pole, paseky)→ ISOM 401–406
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from backend.data_sources.cache import cache_path

# Overpass server bývá přetížený → držíme seznam zrcadel a zkoušíme je po
# řadě. Přes MAPOVAC_OVERPASS_URL lze předřadit vlastní endpoint.
_DEFAULT_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
_env_url = os.environ.get("MAPOVAC_OVERPASS_URL")
OVERPASS_MIRRORS = ([_env_url] if _env_url else []) + [
    m for m in _DEFAULT_MIRRORS if m != _env_url
]
OVERPASS_URL = OVERPASS_MIRRORS[0]  # zpětná kompatibilita
TIMEOUT = httpx.Timeout(180.0, connect=20.0)
# Stavy, kdy má smysl zkusit znovu / jiné zrcadlo (přetížení, throttling).
_RETRY_STATUS = {429, 502, 503, 504}

# Jeden Overpass dotaz pokrývá všechny vrstvy najednou — výrazně rychlejší než N dotazů.
QUERY_TEMPLATE = """
[out:json][timeout:120][bbox:{s},{w},{n},{e}];
(
  way["highway"];
  way["railway"];
  way["waterway"];
  way["water"];
  relation["water"];
  way["natural"="water"];
  way["building"];
  relation["building"];
  way["barrier"];
  way["power"="line"];
  way["power"="minor_line"];
  way["landuse"];
  relation["landuse"];
  way["natural"];
  relation["natural"];
  way["amenity"="parking"];
  relation["amenity"="parking"];
  way["area:highway"];
);
out body geom;
"""


class OverpassError(RuntimeError):
    pass


def fetch_osm(bbox_wgs: tuple[float, float, float, float]) -> Path:
    """Stáhne Overpass odpověď pro bbox a uloží jako JSON do cache."""
    w, s, e, n = bbox_wgs
    # `qv` (query version) v klíči → změna dotazu (např. přidání railway)
    # invaliduje staré cache bez nutnosti je mazat ručně.
    out = cache_path("osm", {"bbox": [w, s, e, n], "qv": 4}, ".json")
    if out.exists() and out.stat().st_size > 0:
        return out
    query = QUERY_TEMPLATE.format(w=w, s=s, e=e, n=n)
    # POZOR: overpass-api.de vrací 406, pokud klient pošle Accept: application/json
    # (Apache content negotiation). [out:json] v dotazu zajistí JSON odpověď
    # nezávisle na Accept hlavičce, takže ji neposíláme vůbec.
    headers = {
        "User-Agent": "mapovac/0.1 (orienteering map generator)",
    }
    payload = _post_with_retries(query, headers)
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


def _post_with_retries(query: str, headers: dict, attempts: int = 3) -> dict:
    """Pošle Overpass dotaz s opakováním a střídáním zrcadel.

    Overpass často vrací 504/503 (přetížení) — to je dočasné. Projdeme zrcadla
    v `OVERPASS_MIRRORS`, každé zkusíme až `attempts`× s exponenciálním
    backoffem. Vyhodíme až když selžou VŠECHNA zrcadla.
    """
    last_err: Exception | None = None
    with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
        for url in OVERPASS_MIRRORS:
            for attempt in range(attempts):
                try:
                    r = client.post(url, data={"data": query})
                except httpx.HTTPError as exc:  # síťová chyba / timeout
                    last_err = OverpassError(f"{url}: {type(exc).__name__}: {exc}")
                    time.sleep(min(2 ** attempt, 8))
                    continue
                if r.status_code == 200:
                    try:
                        return r.json()
                    except json.JSONDecodeError as exc:
                        last_err = OverpassError(f"{url}: neplatný JSON: {exc}")
                        break  # JSON chyba se opakováním nevyřeší → další zrcadlo
                last_err = OverpassError(f"{url} → {r.status_code}: {r.text[:120]}")
                if r.status_code in _RETRY_STATUS:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                break  # neopravitelný stav (400/403/…) → zkus další zrcadlo
    raise last_err or OverpassError("Overpass: všechna zrcadla selhala")


def summarize(osm_json_path: Path) -> dict[str, int]:
    """Rychlý počet prvků podle typu — pro reporting v UI."""
    data = json.loads(osm_json_path.read_text("utf-8"))
    counts: dict[str, int] = {}
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        for k in ("highway", "waterway", "water", "building", "barrier", "power", "landuse", "natural"):
            if k in tags:
                counts[k] = counts.get(k, 0) + 1
                break
    return counts
