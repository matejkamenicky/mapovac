"""Parsování Overpass JSON do strukturovaných features se ISOM stylem.

Vstupy:
- Overpass dotaz s `out body geom;` (viz `data_sources/osm.py`)
- Bbox v WGS84

Výstup: iterátor `(IsomStyle, geometry_sjtsk)` kde geometry je:
- pro `line`:    list[list[(x, y)]]  — jedna nebo více polyline (multipart)
- pro `polygon`: list[list[(x, y)]]  — uzavřené ringy (jen outer; holes zatím ne)
- pro `point`:   list[(x, y)]
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from backend.data_sources.projection import wgs_to_sjtsk
from backend.vector.isom_mapping import IsomStyle, style_for


@dataclass
class Feature:
    style: IsomStyle
    parts: list[list[tuple[float, float]]]  # S-JTSK
    tags: dict


def _way_coords_sjtsk(geom: list[dict]) -> list[tuple[float, float]]:
    return [wgs_to_sjtsk(pt["lon"], pt["lat"]) for pt in geom if "lon" in pt and "lat" in pt]


def _is_closed(coords: list[tuple[float, float]]) -> bool:
    return len(coords) >= 3 and coords[0] == coords[-1]


def _stitch_rings(segments: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Pospojuje úseky (members multipolygonu) sdílející koncové body do
    uzavřených prstenců. OSM multipolygon má outer/inner často rozsekané na
    víc way — bez spojení by vznikly otevřené/chybné polygony.

    Sdílené uzly mají identické lat/lon → po projekci identické (x, y), takže
    porovnání koncových bodů na rovnost funguje.
    """
    segs = [list(s) for s in segments if len(s) >= 2]
    rings: list[list[tuple[float, float]]] = []
    while segs:
        cur = segs.pop()
        changed = True
        while not _is_closed(cur) and changed:
            changed = False
            for i, s in enumerate(segs):
                if cur[-1] == s[0]:
                    cur = cur + s[1:]
                elif cur[-1] == s[-1]:
                    cur = cur + s[-2::-1]
                elif cur[0] == s[-1]:
                    cur = s + cur[1:]
                elif cur[0] == s[0]:
                    cur = s[::-1] + cur[1:]
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if len(cur) >= 3:
            if cur[0] != cur[-1]:
                cur = cur + [cur[0]]
            rings.append(cur)
    return rings


def _relation_rings(el: dict) -> list[list[tuple[float, float]]]:
    """Sestaví outer prstence multipolygon relace (inner/díry zatím vynecháme —
    vegetace/voda je překreslí navrch). Každý outer = jeden polygon."""
    outers: list[list[tuple[float, float]]] = []
    for m in el.get("members", []):
        if m.get("type") != "way" or m.get("role") not in ("outer", ""):
            continue
        coords = _way_coords_sjtsk(m.get("geometry") or [])
        if len(coords) >= 2:
            outers.append(coords)
    return _stitch_rings(outers)


def parse(osm_json_path: Path) -> Iterator[Feature]:
    """Vrátí features s ISOM stylem, reprojektované do S-JTSK.

    Heuristika polygon vs. line:
    - building / natural=water / water=* / landuse=* / natural=wood/scrub/...
      → polygon, i když není explicitně uzavřená v JSONu (Overpass občas
      vrátí uzavřenou way ale bez duplicitního koncového bodu).
    - jinak → line.
    """
    polygon_indicating_tags = {
        "building", "landuse", "water",
    }
    polygon_indicating_kv = {
        ("natural", "water"),
        ("natural", "wood"),
        ("natural", "scrub"),
        ("natural", "heath"),
        ("natural", "wetland"),
        ("natural", "bare_rock"),
    }

    data = json.loads(osm_json_path.read_text("utf-8"))
    for el in data.get("elements", []):
        etype = el.get("type")
        if etype not in ("way", "relation"):
            continue
        tags = el.get("tags") or {}
        style = style_for(tags)
        if style is None:
            continue

        # Multipolygon relace (landuse/natural/water/building) → polygon(y).
        if etype == "relation":
            for ring in _relation_rings(el):
                yield Feature(style=style, parts=[ring], tags=tags)
            continue

        geom = el.get("geometry") or []
        coords = _way_coords_sjtsk(geom)
        if len(coords) < 2:
            continue

        wants_polygon = (
            style.kind == "polygon"
            or any(k in tags for k in polygon_indicating_tags)
            or any((k, tags.get(k)) in polygon_indicating_kv for k, _ in polygon_indicating_kv)
        )

        if wants_polygon:
            if not _is_closed(coords):
                coords = coords + [coords[0]]
            yield Feature(style=style, parts=[coords], tags=tags)
        else:
            yield Feature(style=style, parts=[coords], tags=tags)
