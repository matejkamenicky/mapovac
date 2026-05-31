"""Experimentální bodová detekce z LiDAR cloudu (ISOM 418 strom, 206 balvan).

Prohlubně/kupky (109/111) dělá pullauta sama (jen je doplníme do .omap z DXF).
Tady navíc z klasifikovaného cloudu detekujeme:
- **418 výrazný strom** — izolovaná vysoká vegetace (třída 5) v otevřené ploše,
- **206 balvan** — ostré lokální maximum terénu malého půdorysu (experimentální).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.lidar.chm import Raster, fill_nans


def _ground_dtm(x, y, z, cls, bbox, res):
    g = cls == 2
    xmin, ymin, xmax, ymax = bbox
    W = max(1, int(np.ceil((xmax - xmin) / res)))
    H = max(1, int(np.ceil((ymax - ymin) / res)))
    col = np.clip(((x[g] - xmin) / res).astype(int), 0, W - 1)
    row = np.clip(((ymax - y[g]) / res).astype(int), 0, H - 1)
    gm = np.full(W * H, np.inf, dtype=np.float32)
    np.minimum.at(gm, row * W + col, z[g].astype(np.float32))
    gm[~np.isfinite(gm)] = np.nan
    return fill_nans(Raster(gm.reshape(H, W), xmin, ymin, xmax, ymax, res), 12).array


def _read_cloud(cloud_path, bbox):
    import laspy
    las = laspy.read(str(cloud_path))
    x = np.asarray(las.x); y = np.asarray(las.y); z = np.asarray(las.z)
    cls = np.asarray(las.classification)
    xmin, ymin, xmax, ymax = bbox
    m = (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)
    return x[m], y[m], z[m], cls[m]


def distinct_trees(
    cloud_path: Path,
    bbox_sjtsk: tuple[float, float, float, float],
    res: float = 2.0,
    min_height: float = 5.0,
    isolation_radius_m: float = 20.0,
    max_local_frac: float = 0.06,
    max_crown_cells: int = 8,
) -> list[tuple[float, float]]:
    """Izolovaná vysoká vegetace v otevřené ploše → výrazné stromy (body)."""
    try:
        from scipy.ndimage import label, uniform_filter
    except Exception:
        return []
    x, y, z, cls = _read_cloud(cloud_path, bbox_sjtsk)
    if x.size == 0:
        return []
    xmin, ymin, xmax, ymax = bbox_sjtsk
    dtm = _ground_dtm(x, y, z, cls, bbox_sjtsk, res)
    H, W = dtm.shape
    v = cls == 5
    vc = np.clip(((x[v] - xmin) / res).astype(int), 0, W - 1)
    vr = np.clip(((ymax - y[v]) / res).astype(int), 0, H - 1)
    vh = z[v] - dtm[vr, vc]
    canopy = np.zeros((H, W), dtype=np.float32)
    np.maximum.at(canopy, (vr, vc), vh.astype(np.float32))

    tall = canopy >= min_height
    if not tall.any():
        return []
    rad = max(1, int(isolation_radius_m / res))
    frac = uniform_filter(tall.astype(np.float32), size=2 * rad + 1, mode="constant")
    isolated = tall & (frac <= max_local_frac)

    lbl, n = label(isolated)
    trees: list[tuple[float, float]] = []
    for i in range(1, n + 1):
        ys, xs = np.where(lbl == i)
        if ys.size > max_crown_cells:  # velký shluk = okraj lesa, ne strom
            continue
        cx = xmin + (xs.mean() + 0.5) * res
        cy = ymax - (ys.mean() + 0.5) * res
        trees.append((float(cx), float(cy)))
    return trees


def boulders(
    cloud_path: Path,
    bbox_sjtsk: tuple[float, float, float, float],
    res: float = 1.0,
    min_rise: float = 2.0,
    radius_m: float = 3.0,
    max_footprint_cells: int = 5,
    max_bg_slope: float = 0.12,
    isolation_radius_m: float = 8.0,
    max_neighbors: int = 2,
) -> list[tuple[float, float]]:
    """Ostrý hrbol terénu malého půdorysu na ROVINĚ → balvan (experimentální).

    Klíčové: gate na rovinatost okolí — bez něj DTM mikro-reliéf na svazích
    dělá tisíce falešných balvanů. Balvan = ostré lokální maximum tam, kde je
    okolní terén plochý.
    """
    try:
        from scipy.ndimage import gaussian_filter, label, maximum_filter, minimum_filter
    except Exception:
        return []
    x, y, z, cls = _read_cloud(cloud_path, bbox_sjtsk)
    if x.size == 0:
        return []
    xmin, ymin, xmax, ymax = bbox_sjtsk
    dtm = _ground_dtm(x, y, z, cls, bbox_sjtsk, res)
    rad = max(1, int(radius_m / res))
    surround_min = minimum_filter(dtm, size=2 * rad + 1, mode="nearest")
    rise = dtm - surround_min
    peak = dtm >= maximum_filter(dtm, size=3, mode="nearest") - 1e-3
    # Sklon pozadí (vyhlazený DTM) — balvan musí být na rovině, ne na svahu.
    bg = gaussian_filter(dtm, sigma=max(2.0, rad * 2.0))
    gy, gx = np.gradient(bg, res)
    bg_slope = np.sqrt(gx * gx + gy * gy)
    cand = peak & (rise >= min_rise) & (bg_slope < max_bg_slope)
    if not cand.any():
        return []
    # Izolace: balvan je osamocený. Husté shluky kandidátů = mikro-reliéf/šum.
    irad = max(1, int(isolation_radius_m / res))
    from scipy.ndimage import uniform_filter
    win = 2 * irad + 1
    neigh = uniform_filter(cand.astype(np.float32), size=win, mode="constant") * (win * win)
    cand = cand & (neigh <= max_neighbors + 1)
    if not cand.any():
        return []
    lbl, n = label(cand)
    out: list[tuple[float, float]] = []
    for i in range(1, n + 1):
        ys, xs = np.where(lbl == i)
        if ys.size > max_footprint_cells:  # velká plocha = svah, ne balvan
            continue
        cx = xmin + (xs.mean() + 0.5) * res
        cy = ymax - (ys.mean() + 0.5) * res
        out.append((float(cx), float(cy)))
    return out
