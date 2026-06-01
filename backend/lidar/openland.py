"""Rozlišení žlutých (otevřených) ploch na ISOM 401/403/412 v rastru.

Pullauta dělá veškerou otevřenou plochu jako 401 (žlutá). Tady podle OSM
landuse přebarvíme:
- 403 Rough open land (paseka/vřesoviště) → světlejší žlutá (Yellow 50 %),
- 412 Cultivated land (pole/sad) → žlutá + mřížka černých teček.

Pracujeme jen na ŽLUTÝCH pixelech (zachováme vrstevnice, zeleň i ostatní).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import laspy

from backend.lidar.chm import Raster, fill_nans, rasterize
from backend.lidar.crop import WorldFile
from backend.lidar.recolor import ISOM_YELLOW
from backend.vector.isom_mapping import (
    GRAY as _GRAY, OLIVE_520 as _OLIVE, YELLOW_50 as _Y50, px_per_mm,
)

YELLOW_50 = _Y50[:3]  # RGB (bez alfa) pro přiřazení do RGB rastru
OLIVE_520 = _OLIVE[:3]  # ISOM 520 olivová (Yellow 100 % + Green 50 %)
GRAY_213 = _GRAY[:3]   # ISOM 213/214 holá skála (šedá)

# Kód plochy → barva výplně v rastru (zachová vrstevnice/černé symboly).
# Bare rock (213) plníme v rastru a NEpřekrýváme jím vrstevnice/srázy/balvany.
_FILL_COLORS = {"401": ISOM_YELLOW, "412": ISOM_YELLOW, "520": OLIVE_520, "213": GRAY_213}


def mark_rough_open(
    isom_base_png: Path,
    pgw: Path,
    cloud_path: Path,
    low_lo: float = 0.25,
    low_hi: float = 3.0,
    res: float = 4.0,
    min_density: float = 0.03,
    dilate_cells: int = 2,
) -> dict:
    """Otevřené plochy (žlutá) s NÍZKOU vegetací → 403 paseka (rough open).

    Karttapullautin sám paseky/louky nerozlišuje. Rozdíl je v datech: paseka
    má nízký nálet/klest (vegetační body 0,3–2,5 m nad zemí), louka ne. Spočteme
    hustotu nízké vegetace z klasifikovaného cloudu (třída 5) a kde je nad práh
    a podklad je žlutý, přebarvíme na světlejší žlutou (Yellow 50 %).
    """
    if not Path(cloud_path).exists():
        return {"rough_lidar_px": 0}
    base = Image.open(isom_base_png).convert("RGB")
    arr = np.asarray(base).copy()
    H, W = arr.shape[:2]
    wf = WorldFile.read(pgw)
    xmin = wf.origin_x
    ymax = wf.origin_y
    xmax = xmin + W * wf.pixel_size_x
    ymin = ymax + H * wf.pixel_size_y  # pixel_size_y < 0

    las = laspy.read(str(cloud_path))
    x = np.asarray(las.x); y = np.asarray(las.y); z = np.asarray(las.z)
    cls = np.asarray(las.classification)
    bbox = (xmin, ymin, xmax, ymax)

    # Terén (ground min) interpolovaný → výška vegetace nad zemí.
    g = cls == 2
    dtm = fill_nans(_grid_min(x[g], y[g], z[g], bbox, res), max_kernel=12).array
    GH, GW = dtm.shape

    v = cls == 5
    vc = np.clip(((x[v] - xmin) / res).astype(int), 0, GW - 1)
    vr = np.clip(((ymax - y[v]) / res).astype(int), 0, GH - 1)
    vh = z[v] - dtm[vr, vc]
    low = (vh >= low_lo) & (vh < low_hi)
    cnt = np.zeros(GH * GW, dtype=np.float32)
    np.add.at(cnt, vr[low] * GW + vc[low], 1.0)
    density = cnt.reshape(GH, GW) / (res * res)
    rough_coarse = density >= min_density
    # Paseky mají nízkou vegetaci roztroušenou → rozšíříme masku (closing +
    # dilatace), aby řídké body pokryly celou paseku, ne jen buňky s body.
    if dilate_cells > 0 and rough_coarse.any():
        from skimage.morphology import binary_closing, binary_dilation, disk
        rough_coarse = binary_closing(rough_coarse, footprint=disk(dilate_cells))
        rough_coarse = binary_dilation(rough_coarse, footprint=disk(dilate_cells))

    # Upsample na pixely podkladu + maska žluté
    cols = np.arange(W)
    rows = np.arange(H)
    X = xmin + (cols + 0.5) * wf.pixel_size_x
    Y = ymax + (rows + 0.5) * wf.pixel_size_y
    ccol = np.clip(((X - xmin) / res).astype(int), 0, GW - 1)
    crow = np.clip(((ymax - Y) / res).astype(int), 0, GH - 1)
    rough = rough_coarse[np.ix_(crow, ccol)]
    yellow = np.all(arr == np.array(ISOM_YELLOW), axis=-1)
    m = yellow & rough
    arr[m] = YELLOW_50
    Image.fromarray(arr, "RGB").save(isom_base_png)
    return {"rough_lidar_px": int(m.sum())}


def _grid_min(x, y, z, bbox, res) -> Raster:
    xmin, ymin, xmax, ymax = bbox
    W = max(1, int(np.ceil((xmax - xmin) / res)))
    H = max(1, int(np.ceil((ymax - ymin) / res)))
    col = np.clip(((x - xmin) / res).astype(int), 0, W - 1)
    row = np.clip(((ymax - y) / res).astype(int), 0, H - 1)
    gm = np.full(W * H, np.inf, dtype=np.float32)
    np.minimum.at(gm, row * W + col, z.astype(np.float32))
    gm[~np.isfinite(gm)] = np.nan
    return Raster(gm.reshape(H, W), xmin, ymin, xmax, ymax, res)

# Kódy ploch řešené přímo v rastru (overlay je nekreslí, ať nepřekryjí vrstevnice).
BASE_HANDLED_CODES = {"401", "403", "412", "405"}


def _poly_mask(size, wf: WorldFile, rings) -> np.ndarray:
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    for ring in rings:
        px = [wf.world_to_pixel(x, y) for x, y in ring]
        if len(px) >= 3:
            d.polygon(px, fill=255)
    return np.asarray(m, dtype=bool)


def _linework(arr: np.ndarray) -> np.ndarray:
    """Pixely vrstevnic (hnědá) a černé symboly — při fillu se zachovají."""
    r = arr[..., 0].astype(np.int16)
    g = arr[..., 1].astype(np.int16)
    b = arr[..., 2].astype(np.int16)
    black = (r < 70) & (g < 70) & (b < 70)
    brown = (r > 140) & (g >= 40) & (g < 150) & (b < 95)
    return black | brown


def fill_open_land(isom_base_png: Path, pgw: Path, features) -> dict:
    """Vyplní vnitřek polygonů (OSM/ZABAGED) plnou barvou, aby výplň přesně
    seděla na hranici (obrys 416/520). Zachová vrstevnice a černé symboly.

    - 401/412 (louka/pole) → žlutá,
    - 520 (zákaz vstupu, průmysl) → olivová (Yellow 100 % + Green 50 %).

    Bez tohoto by barva pocházela jen z pullauta (LiDAR) a neseděla by na
    katastrální hranici.
    """
    base = Image.open(isom_base_png).convert("RGB")
    arr = np.asarray(base).copy()
    wf = WorldFile.read(pgw)
    H, W = arr.shape[:2]
    line = _linework(arr)
    rep = {"filled_px": 0, "out_of_bounds_px": 0}
    for f in features:
        col = _FILL_COLORS.get(f.style.isom_code)
        if col is None:
            continue
        mask = _poly_mask((W, H), wf, f.parts) & ~line
        arr[mask] = col
        if f.style.isom_code == "520":
            rep["out_of_bounds_px"] += int(mask.sum())
        else:
            rep["filled_px"] += int(mask.sum())
    Image.fromarray(arr, "RGB").save(isom_base_png)
    return rep


def differentiate_open(isom_base_png: Path, pgw: Path, features) -> dict:
    """Aplikuje 403/412 rozlišení na žluté plochy podle OSM landuse features."""
    base = Image.open(isom_base_png).convert("RGB")
    arr = np.asarray(base).copy()
    wf = WorldFile.read(pgw)
    H, W = arr.shape[:2]
    yellow = np.all(arr == np.array(ISOM_YELLOW), axis=-1)
    rep = {"rough_403_px": 0, "cultivated_412_px": 0}

    # 403 rough open → světlejší žlutá (jen kde je teď žlutá = otevřeno)
    for f in features:
        if f.style.isom_code != "403":
            continue
        mask = _poly_mask((W, H), wf, f.parts) & yellow
        arr[mask] = YELLOW_50
        rep["rough_403_px"] += int(mask.sum())

    # 412 cultivated → ponech žlutou + mřížka černých teček
    out = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(out)
    yellow2 = (np.all(np.asarray(out) == np.array(ISOM_YELLOW), axis=-1))
    # ISOM 412: tečky Ø 0.2 mm v mřížce 0.8 mm (CC), orientace k severu.
    spacing = max(4, int(round(0.8 * px_per_mm(10000))))   # 0.8 mm mřížka
    r = max(1, int(round(0.1 * px_per_mm(10000))))         # Ø 0.2 mm → poloměr 0.1 mm
    for f in features:
        if f.style.isom_code != "412":
            continue
        mask = _poly_mask((W, H), wf, f.parts) & yellow2
        if not mask.any():
            continue
        ys, xs = np.where(mask)
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        cnt = 0
        for yy in range(y0, y1 + 1, spacing):
            for xx in range(x0, x1 + 1, spacing):
                if mask[yy, xx]:
                    draw.ellipse((xx - r, yy - r, xx + r, yy + r), fill=(0, 0, 0))
                    cnt += 1
        rep["cultivated_412_px"] += cnt
    out.save(isom_base_png)
    return rep
