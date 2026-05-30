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
        if el.get("type") != "way":
            # relace zatím ignorujeme (M5+)
            continue
        tags = el.get("tags") or {}
        style = style_for(tags)
        if style is None:
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
