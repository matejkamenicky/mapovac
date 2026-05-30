"""Vektorizace plošných ISOM barev z rastru (pullauta výstup) na polygony.

Po přemapování na ISOM paletu (`recolor.py`) má rastr jen pár plných barev.
Tady z nich uděláme polygony v S-JTSK pro vektorový .omap export: zeleň
(406/408/410) a otevřenou plochu (401). Bílá = les = papír → vynecháme.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from shapely.geometry import Polygon
from skimage import measure
from skimage.morphology import binary_closing, disk, remove_small_objects

from backend.lidar.crop import WorldFile


def _generalize(mask: np.ndarray, close_px: int, min_px: int) -> np.ndarray:
    if not mask.any():
        return mask
    if close_px > 0:
        mask = binary_closing(mask, footprint=disk(close_px))
    if min_px > 1 and mask.any():
        mask = remove_small_objects(mask, min_size=min_px, connectivity=2)
    return mask


def _rings_from_mask(
    mask: np.ndarray, wf: WorldFile, simplify_m: float, round_m: float
) -> list[list[tuple[float, float]]]:
    if not mask.any():
        return []
    padded = np.pad(mask.astype(np.uint8), 1, constant_values=0)
    contours = measure.find_contours(padded, 0.5)
    rings: list[list[tuple[float, float]]] = []
    for c in contours:
        if len(c) < 4:
            continue
        world = [
            (
                wf.origin_x + (col - 1) * wf.pixel_size_x,
                wf.origin_y + (row - 1) * wf.pixel_size_y,
            )
            for row, col in c
        ]
        try:
            poly = Polygon(world)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.area < 1.0:
                continue
            if round_m > 0:
                poly = (
                    poly.buffer(round_m, join_style=1)
                    .buffer(-2 * round_m, join_style=1)
                    .buffer(round_m, join_style=1)
                )
            poly = poly.simplify(simplify_m, preserve_topology=True)
            geoms = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
            for g in geoms:
                if g.is_empty or g.area < 1.0:
                    continue
                ring = list(g.exterior.coords)
                if len(ring) >= 4:
                    rings.append(ring)
        except Exception:
            continue
    return rings


def vectorize_areas(
    png_path: Path,
    pgw_path: Path,
    color_map: dict[tuple[int, int, int], str],
    min_area_m2: float = 25.0,
    close_m: float = 2.0,
    simplify_m: float = 1.2,
    round_m: float = 1.5,
) -> list[tuple[str, list[list[tuple[float, float]]]]]:
    """Pro každou barvu v `color_map` (RGB→ISOM kód) vrátí (kód, rings v S-JTSK)."""
    img = np.asarray(Image.open(png_path).convert("RGB"))
    wf = WorldFile.read(pgw_path)
    res = abs(wf.pixel_size_x)  # m/px
    min_px = max(1, int(min_area_m2 / (res * res)))
    close_px = max(0, int(round(close_m / res)))
    out: list[tuple[str, list[list[tuple[float, float]]]]] = []
    for (r, g, b), code in color_map.items():
        mask = (img[..., 0] == r) & (img[..., 1] == g) & (img[..., 2] == b)
        mask = _generalize(mask, close_px, min_px)
        rings = _rings_from_mask(mask, wf, simplify_m, round_m)
        if rings:
            out.append((code, rings))
    return out
