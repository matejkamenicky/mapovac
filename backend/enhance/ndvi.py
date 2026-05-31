"""Korekce vegetace podle ortofota (NDVI / RGB index).

LiDAR je snímek z doby skenování (~2019); ortofoto bývá aktuálnější. Tady
porovnáme ISOM podklad (z pullauty) s vegetačním indexem z ortofota a opravíme
zjevné nesoulady:

- les (bílá), ale ortofoto bez vegetace → **mýtina/holina** → otevřeno (žlutá),
- otevřeno (žlutá), ale ortofoto hustá vegetace → **nový nálet** → světlá zeleň.

Konzervativní — měníme jen jasné případy. Vrací počet opravených pixelů.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from backend.lidar.crop import WorldFile
from backend.lidar.recolor import ISOM_GREEN_30, ISOM_YELLOW, WHITE
from backend.vector.isom_mapping import YELLOW_50


def _veg_index(samp: np.ndarray, cir: bool) -> np.ndarray:
    """Vrátí vegetační index 0..1 z RGB/CIR vzorku (H×W×3)."""
    r = samp[..., 0].astype(np.float32)
    g = samp[..., 1].astype(np.float32)
    b = samp[..., 2].astype(np.float32)
    if cir:
        # CIR false-color: NIR→R, red→G. NDVI = (NIR−Red)/(NIR+Red).
        nir, red = r, g
        with np.errstate(invalid="ignore", divide="ignore"):
            ndvi = (nir - red) / np.maximum(nir + red, 1.0)
        return np.clip((ndvi + 1.0) / 2.0, 0.0, 1.0)
    # RGB: HUE greenness (G−R) — nezávislé na jasu, takže TMAVÝ les zůstane
    # vegetací (vysoké), zatímco holina (holá zem/klest, R≳G) vyjde nízká.
    # ExG (jas) by tmavý les chybně označil jako „bez vegetace".
    with np.errstate(invalid="ignore", divide="ignore"):
        vari = (g - r) / np.maximum(g + r - b, 1.0)
    return np.clip((vari + 0.5) / 1.0, 0.0, 1.0)


def correct_vegetation(
    isom_base_png: Path,
    isom_pgw: Path,
    ortofoto_png: Path,
    ortofoto_pgw: Path,
    cir: bool = False,
    veg_high: float = 0.62,
    veg_low: float = 0.42,
) -> dict:
    base = Image.open(isom_base_png).convert("RGB")
    barr = np.asarray(base).copy()
    H, W = barr.shape[:2]
    bwf = WorldFile.read(isom_pgw)

    o = np.asarray(Image.open(ortofoto_png).convert("RGB"))
    OH, OW = o.shape[:2]
    owf = WorldFile.read(ortofoto_pgw)

    # Pro každý sloupec/řádek podkladu spočti odpovídající index v ortofotu.
    cols = np.arange(W)
    rows = np.arange(H)
    X = bwf.origin_x + cols * bwf.pixel_size_x
    Y = bwf.origin_y + rows * bwf.pixel_size_y
    ocol = np.clip(((X - owf.origin_x) / owf.pixel_size_x).astype(int), 0, OW - 1)
    orow = np.clip(((Y - owf.origin_y) / owf.pixel_size_y).astype(int), 0, OH - 1)
    samp = o[np.ix_(orow, ocol)]  # H×W×3 zarovnané na podklad

    vi = _veg_index(samp, cir)

    is_white = np.all(barr == np.array(WHITE), axis=-1)
    is_yellow = np.all(barr == np.array(ISOM_YELLOW), axis=-1)

    # les, ale v ortofotu holá zem (holina/klest) → otevřeno
    to_open = is_white & (vi < veg_low)
    # otevřeno → zeleň jen s PRAVÝM NDVI (CIR); RGB neumí odlišit travnatou
    # otevřenou plochu od porostu (obojí zelené), tak to vynecháme.
    to_veg = (is_yellow & (vi > veg_high)) if cir else np.zeros_like(is_yellow)

    # Holina detekovaná z ortofota = paseka → 403 rough open (Yellow 50 %),
    # ne louka.
    barr[to_open] = YELLOW_50[:3]
    if cir:
        barr[to_veg] = ISOM_GREEN_30

    Image.fromarray(barr, "RGB").save(isom_base_png)
    return {
        "forest_to_open_px": int(to_open.sum()),
        "open_to_veg_px": int(to_veg.sum()),
    }
