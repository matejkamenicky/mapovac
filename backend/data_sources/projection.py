"""WGS84 (EPSG:4326) ↔ S-JTSK / Krovak East-North (EPSG:5514).

ČÚZK i CZ data jsou v S-JTSK. Frontend posílá bbox v WGS84.
"""
from __future__ import annotations

from functools import lru_cache

from pyproj import Transformer

WGS84 = "EPSG:4326"
SJTSK = "EPSG:5514"


@lru_cache(maxsize=4)
def _transformer(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def wgs_to_sjtsk(lon: float, lat: float) -> tuple[float, float]:
    return _transformer(WGS84, SJTSK).transform(lon, lat)


def sjtsk_to_wgs(x: float, y: float) -> tuple[float, float]:
    return _transformer(SJTSK, WGS84).transform(x, y)


def bbox_wgs_to_sjtsk(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """[w, s, e, n] WGS84 → [xmin, ymin, xmax, ymax] S-JTSK.

    Rohy se transformují všechny čtyři a vezme se obálka — Krovak je nelineární.
    """
    w, s, e, n = bbox
    corners = [(w, s), (e, s), (e, n), (w, n)]
    xs, ys = zip(*(wgs_to_sjtsk(lon, lat) for lon, lat in corners))
    return min(xs), min(ys), max(xs), max(ys)
