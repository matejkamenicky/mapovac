"""Stažení ČÚZK ortofota pro výřez (RGB, volitelně CIR pro NDVI).

Dlaždice se stahuje v rozlišení odpovídajícím cílovému měřítku/preview, ať
sedí na stejný extent jako pullauta výstup.
"""
from __future__ import annotations

from pathlib import Path

from backend.data_sources import wms


def _size_for_bbox(bbox_sjtsk, max_px: int = 4000, res_m: float = 0.5) -> tuple[int, int]:
    xmin, ymin, xmax, ymax = bbox_sjtsk
    w = max(16, int(round((xmax - xmin) / res_m)))
    h = max(16, int(round((ymax - ymin) / res_m)))
    # ořež na max rozlišení (WMS limity)
    scale = min(1.0, max_px / max(w, h))
    return max(16, int(w * scale)), max(16, int(h * scale))


def fetch_ortofoto(
    bbox_sjtsk: tuple[float, float, float, float],
    out_png: Path,
    cir: bool = False,
) -> Path | None:
    """Stáhne ortofoto (RGB nebo CIR) pro bbox. Vrátí PNG (+pgw) nebo None."""
    width, height = _size_for_bbox(bbox_sjtsk)
    if cir:
        if not wms.ORTOFOTO_CIR_WMS:
            return None
        return wms.get_map(
            wms.ORTOFOTO_CIR_WMS, wms.ORTOFOTO_CIR_LAYER, bbox_sjtsk,
            out_png, width, height, version="1.1.1",
        )
    return wms.get_map(
        wms.ORTOFOTO_WMS, wms.ORTOFOTO_LAYER, bbox_sjtsk, out_png, width, height,
        version="1.1.1",  # bez axis-order nejasností pro EPSG:5514
    )
