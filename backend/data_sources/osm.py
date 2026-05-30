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
from pathlib import Path

import httpx

from backend.data_sources.cache import cache_path

OVERPASS_URL = os.environ.get("MAPOVAC_OVERPASS_URL", "https://overpass-api.de/api/interpreter")
TIMEOUT = httpx.Timeout(180.0, connect=20.0)

# Jeden Overpass dotaz pokrývá všechny vrstvy najednou — výrazně rychlejší než N dotazů.
QUERY_TEMPLATE = """
[out:json][timeout:120][bbox:{s},{w},{n},{e}];
(
  way["highway"];
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
  way["natural"];
);
out body geom;
"""


class OverpassError(RuntimeError):
    pass


def fetch_osm(bbox_wgs: tuple[float, float, float, float]) -> Path:
    """Stáhne Overpass odpověď pro bbox a uloží jako JSON do cache."""
    w, s, e, n = bbox_wgs
    out = cache_path("osm", {"bbox": [w, s, e, n]}, ".json")
    if out.exists() and out.stat().st_size > 0:
        return out
    query = QUERY_TEMPLATE.format(w=w, s=s, e=e, n=n)
    # POZOR: overpass-api.de vrací 406, pokud klient pošle Accept: application/json
    # (Apache content negotiation). [out:json] v dotazu zajistí JSON odpověď
    # nezávisle na Accept hlavičce, takže ji neposíláme vůbec.
    headers = {
        "User-Agent": "mapovac/0.1 (orienteering map generator)",
    }
    with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
        r = client.post(OVERPASS_URL, data={"data": query})
    if r.status_code != 200:
        raise OverpassError(f"Overpass {r.status_code}: {r.text[:200]}")
    try:
        payload = r.json()
    except json.JSONDecodeError as exc:
        raise OverpassError(f"Overpass: neplatný JSON: {exc}") from exc
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


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
