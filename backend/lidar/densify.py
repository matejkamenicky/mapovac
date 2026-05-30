"""Densifikace LAZ na pravidelný 1m grid pro Karttapullautin.

ČÚZK DMR 5G LAZ obsahuje řídké LiDAR ground returns (~0.07 pt/m²), což pullauta
v2.12+ nezvládá — vyžaduje plně vyplněný heightmap, jinak panikne.

Řešení: rasterizujeme body do 1m gridu (s vyplněním děr přes IDW/nearest),
pak vygenerujeme syntetický LAZ s 1 bodem na buňku.
"""
from __future__ import annotations

from pathlib import Path

import laspy
import numpy as np
from scipy.ndimage import distance_transform_edt


def _fill_nans_nearest(grid: np.ndarray) -> np.ndarray:
    """Vyplní NaN nejbližším validním sousedem (distance transform)."""
    mask = np.isnan(grid)
    if not mask.any():
        return grid
    # Index nejbližšího nenanu pro každou buňku
    _, indices = distance_transform_edt(mask, return_distances=True, return_indices=True)
    return grid[tuple(indices)]


def densify_laz(
    input_laz: Path, output_laz: Path, resolution: float = 1.0
) -> Path:
    """Načte LAZ, rasterizuje na grid, vyplní díry, zapíše hustý syntetický LAZ."""
    if output_laz.exists() and output_laz.stat().st_size > 0:
        return output_laz

    las = laspy.read(str(input_laz))
    x = np.asarray(las.x, dtype=np.float64)
    y = np.asarray(las.y, dtype=np.float64)
    z = np.asarray(las.z, dtype=np.float64)
    if x.size == 0:
        raise ValueError(f"Prázdný LAZ: {input_laz}")

    xmin, xmax = float(x.min()), float(x.max())
    ymin, ymax = float(y.min()), float(y.max())
    width = max(1, int(np.ceil((xmax - xmin) / resolution)))
    height = max(1, int(np.ceil((ymax - ymin) / resolution)))

    col = np.clip(np.floor((x - xmin) / resolution).astype(np.int64), 0, width - 1)
    row = np.clip(np.floor((ymax - y) / resolution).astype(np.int64), 0, height - 1)
    flat = row * width + col
    size = width * height

    sums = np.bincount(flat, weights=z, minlength=size)
    counts = np.bincount(flat, minlength=size).astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        grid = np.where(counts > 0, sums / np.where(counts == 0, 1, counts), np.nan)
    grid = grid.reshape(height, width).astype(np.float32)
    grid = _fill_nans_nearest(grid)

    # Vygeneruj body na střed každé buňky
    cols = np.arange(width)
    rows = np.arange(height)
    cg, rg = np.meshgrid(cols, rows)
    pts_x = xmin + (cg.flatten() + 0.5) * resolution
    pts_y = ymax - (rg.flatten() + 0.5) * resolution
    pts_z = grid.flatten().astype(np.float64)

    # Zapiš LAZ — point_format 1 (xyzi+gps), scale 0.001m
    header = laspy.LasHeader(point_format=1, version="1.2")
    header.scales = np.array([0.001, 0.001, 0.001])
    header.offsets = np.array([xmin, ymin, 0.0])
    out = laspy.LasData(header)
    out.x = pts_x
    out.y = pts_y
    out.z = pts_z
    # Classification = 2 (ground), ať pullauta nebere body jako vegetaci
    out.classification = np.full(pts_x.shape, 2, dtype=np.uint8)
    output_laz.parent.mkdir(parents=True, exist_ok=True)
    out.write(str(output_laz))
    return output_laz
