"""ISOM 416 — Distinct vegetation boundary (experimentální detekce).

Kreslíme JEN ZŘETELNÉ hranice porostů — tj. tam, kde koruna lesa prudce končí
(ostrý sráz korunové výšky). Kde porost přechází volně (rozplizlé paseky,
hustníky, postupné prořídnutí), gradient je nízký → linii nekreslíme.

Postup:
1. CHM (výška korun nad terénem) z klasifikovaného cloudu (zem 2 / vegetace 5).
2. Vyhlazení + gradient korunové výšky.
3. Izočára CHM na prahové výšce `forest_h` (rozhraní les / ne-les).
4. Z izočáry necháme jen úseky, kde je gradient přes hranici STRMÝ (zřetelný)
   a které jsou dostatečně dlouhé (≥ `min_len_m`).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.lidar.chm import Raster, fill_nans
from backend.lidar.openland import _grid_min


def distinct_vegetation_boundaries(
    cloud_path: Path,
    bbox_sjtsk: tuple[float, float, float, float],
    res: float = 2.0,
    forest_h: float = 8.0,
    min_jump: float = 4.5,   # m skok korunové výšky na buňku (strmost = zřetelnost)
    min_len_m: float = 45.0,
    smooth_sigma: float = 1.0,
) -> list[list[tuple[float, float]]]:
    """Vrátí seznam polylinií (S-JTSK) zřetelných hranic porostů."""
    import laspy
    if not Path(cloud_path).exists():
        return []
    las = laspy.read(str(cloud_path))
    x = np.asarray(las.x); y = np.asarray(las.y); z = np.asarray(las.z)
    cls = np.asarray(las.classification)
    xmin, ymin, xmax, ymax = bbox_sjtsk

    g = cls == 2
    if g.sum() < 100:
        return []
    dtm = fill_nans(_grid_min(x[g], y[g], z[g], (xmin, ymin, xmax, ymax), res),
                    max_kernel=12).array
    GH, GW = dtm.shape

    # CHM = max výška vegetace (třída 5) nad terénem v buňce
    v = cls == 5
    chm = np.zeros((GH, GW), dtype=np.float32)
    if v.sum():
        vc = np.clip(((x[v] - xmin) / res).astype(int), 0, GW - 1)
        vr = np.clip(((ymax - y[v]) / res).astype(int), 0, GH - 1)
        vh = z[v] - dtm[vr, vc]
        np.maximum.at(chm, (vr, vc), vh.astype(np.float32))
    chm[~np.isfinite(chm)] = 0.0
    chm = np.clip(chm, 0, 40)

    try:
        from scipy.ndimage import gaussian_filter
        chm_s = gaussian_filter(chm, smooth_sigma)
    except Exception:
        chm_s = chm

    gy, gx = np.gradient(chm_s)
    grad = np.hypot(gx, gy)  # m korunové výšky na buňku

    try:
        from skimage.measure import find_contours
    except Exception:
        return []
    contours = find_contours(chm_s, forest_h)

    min_pts = max(3, int(round(min_len_m / res)))
    GHi, GWi = chm_s.shape
    out: list[list[tuple[float, float]]] = []
    for c in contours:
        # c: pole (row, col) sub-pixelově; gradient vzorkujeme nejbližším bodem
        rr = np.clip(np.round(c[:, 0]).astype(int), 0, GHi - 1)
        cc = np.clip(np.round(c[:, 1]).astype(int), 0, GWi - 1)
        sharp = grad[rr, cc] >= min_jump
        # rozsekat na souvislé úseky se strmým gradientem
        run: list[tuple[float, float]] = []
        for i, ok in enumerate(sharp):
            if ok:
                wx = xmin + c[i, 1] * res
                wy = ymax - c[i, 0] * res
                run.append((wx, wy))
            else:
                if len(run) >= min_pts:
                    out.append(run)
                run = []
        if len(run) >= min_pts:
            out.append(run)
    return out
