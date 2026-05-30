"""Orchestrace pokročilého generování (opt-in přes options.advanced).

Každý zdroj je best-effort — při chybě/nedostupnosti se přeskočí a základní
mapa se vygeneruje normálně. Self-contained části (ortofoto, NDVI, deklinace,
DMPOK, template) fungují rovnou; autoritativní WFS zdroje (RÚIAN budovy,
DIBAVOD voda, katastr) jsou env-konfigurovatelné (nelze je univerzálně ověřit).
"""
from __future__ import annotations

import os

from backend.data_sources import wfs
from backend.data_sources.cuzk_atom import FEED_DMP1G, FEED_DMPOK
from backend.vector.isom_mapping import BUILDING, WATER_AREA, WATERWAY_STYLES
from backend.vector.osm_features import Feature


def lidar_feed(advanced: bool) -> str:
    """Pokročilé → hustší DMPOK point cloud, jinak DMP1G."""
    return FEED_DMPOK if advanced else FEED_DMP1G


# WFS endpointy (ENV) — autoritativní vektory. Bez konfigurace se přeskočí.
RUIAN_WFS = os.environ.get("MAPOVAC_RUIAN_WFS", "")
RUIAN_TYPENAME = os.environ.get("MAPOVAC_RUIAN_BUILDINGS_TYPENAME", "")
DIBAVOD_WFS = os.environ.get("MAPOVAC_DIBAVOD_WFS", "")
DIBAVOD_WATER_TYPENAME = os.environ.get("MAPOVAC_DIBAVOD_WATER_TYPENAME", "")
DIBAVOD_STREAM_TYPENAME = os.environ.get("MAPOVAC_DIBAVOD_STREAM_TYPENAME", "")


def extra_vector_features(bbox_sjtsk) -> tuple[list, dict]:
    """Vrátí (features, report) z autoritativních WFS zdrojů (additivní)."""
    features: list = []
    rep: dict = {}

    if RUIAN_WFS and RUIAN_TYPENAME:
        rows = wfs.fetch_features(RUIAN_WFS, RUIAN_TYPENAME, bbox_sjtsk)
        for r in rows:
            if r["geom_type"] == "polygon" and r["parts"]:
                features.append(Feature(style=BUILDING, parts=[r["parts"][0]],
                                        tags={"src": "ruian", "building": "yes"}))
        rep["ruian_buildings"] = len(rows)

    if DIBAVOD_WFS and DIBAVOD_WATER_TYPENAME:
        rows = wfs.fetch_features(DIBAVOD_WFS, DIBAVOD_WATER_TYPENAME, bbox_sjtsk)
        for r in rows:
            if r["geom_type"] == "polygon" and r["parts"]:
                features.append(Feature(style=WATER_AREA, parts=[r["parts"][0]],
                                        tags={"src": "dibavod", "natural": "water"}))
        rep["dibavod_water"] = len(rows)

    if DIBAVOD_WFS and DIBAVOD_STREAM_TYPENAME:
        rows = wfs.fetch_features(DIBAVOD_WFS, DIBAVOD_STREAM_TYPENAME, bbox_sjtsk)
        stream = WATERWAY_STYLES.get("stream")
        for r in rows:
            if r["geom_type"] == "line" and stream:
                features.append(Feature(style=stream, parts=r["parts"],
                                        tags={"src": "dibavod", "waterway": "stream"}))
        rep["dibavod_streams"] = len(rows)

    return features, rep
