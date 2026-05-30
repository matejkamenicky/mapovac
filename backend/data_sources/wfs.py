"""Obecný WFS klient (GeoJSON) → polygony/linie v S-JTSK. Best-effort.

Vrací seznam (geometry_type, list_of_rings_or_lines, properties). Při jakékoli
chybě vrací prázdno (nikdy nevyhodí). Reprojekci do S-JTSK řešíme přes pyproj,
pokud server vrátí jiný CRS než 5514.
"""
from __future__ import annotations

import json

import httpx

from backend.data_sources.projection import wgs_to_sjtsk

TIMEOUT = httpx.Timeout(60.0, connect=15.0)
UA = {"User-Agent": "mapovac/0.1"}


def _to_sjtsk(coords, srs_is_wgs: bool):
    x, y = coords[0], coords[1]
    if srs_is_wgs:
        return wgs_to_sjtsk(x, y)
    return (x, y)  # předpoklad: už S-JTSK


def fetch_features(
    base_url: str,
    type_name: str,
    bbox_sjtsk: tuple[float, float, float, float],
    *,
    srs: str = "EPSG:5514",
    version: str = "2.0.0",
    max_features: int = 4000,
) -> list[dict]:
    """Vrátí GeoJSON-like features: [{geom_type, parts, props}]."""
    xmin, ymin, xmax, ymax = bbox_sjtsk
    name_param = "typeNames" if version.startswith("2") else "typeName"
    count_param = "count" if version.startswith("2") else "maxFeatures"
    params = {
        "SERVICE": "WFS",
        "REQUEST": "GetFeature",
        "VERSION": version,
        name_param: type_name,
        "SRSNAME": srs,
        "BBOX": f"{xmin},{ymin},{xmax},{ymax},{srs}",
        "OUTPUTFORMAT": "application/json",
        count_param: str(max_features),
    }
    try:
        r = httpx.get(base_url, params=params, headers=UA, timeout=TIMEOUT)
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            return []
        data = json.loads(r.text)
    except Exception:
        return []

    crs_name = json.dumps(data.get("crs", {}))
    srs_is_wgs = "4326" in crs_name or "CRS84" in crs_name
    out: list[dict] = []
    for feat in data.get("features", []):
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        props = feat.get("properties") or {}
        try:
            if gtype == "Polygon":
                parts = [[_to_sjtsk(c, srs_is_wgs) for c in ring] for ring in coords]
                out.append({"geom_type": "polygon", "parts": parts, "props": props})
            elif gtype == "MultiPolygon":
                for poly in coords:
                    parts = [[_to_sjtsk(c, srs_is_wgs) for c in ring] for ring in poly]
                    out.append({"geom_type": "polygon", "parts": parts, "props": props})
            elif gtype == "LineString":
                out.append({
                    "geom_type": "line",
                    "parts": [[_to_sjtsk(c, srs_is_wgs) for c in coords]],
                    "props": props,
                })
            elif gtype == "MultiLineString":
                for ln in coords:
                    out.append({
                        "geom_type": "line",
                        "parts": [[_to_sjtsk(c, srs_is_wgs) for c in ln]],
                        "props": props,
                    })
        except Exception:
            continue
    return out
