"""Vyhlazení blokových okrajů plošných ISOM barev v rastru.

Pullauta počítá vegetaci/otevřeno na hrubé mřížce (~2,5 m) → na 600 dpi rastru
jsou okraje „rozčtverečkované". Tady plošné barvy (žlutá/zelená/bílá) vyhladíme
mediánovým filtrem, ale vrstevnice (hnědé) a černé symboly necháme ostré
(zachováme je přes masku „linework").
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import median_filter


def _linework_mask(arr: np.ndarray) -> np.ndarray:
    """Pixely, které se NEMAJÍ vyhlazovat (vrstevnice, černé symboly)."""
    r = arr[..., 0].astype(np.int16)
    g = arr[..., 1].astype(np.int16)
    b = arr[..., 2].astype(np.int16)
    black = (r < 70) & (g < 70) & (b < 70)
    # hnědá vrstevnice: vysoké R, střední G (<150 → odliší od žluté/zelené), nízké B
    brown = (r > 140) & (g >= 40) & (g < 150) & (b < 95)
    return black | brown


def smooth_landcover(png_path: Path, size: int = 7) -> None:
    """Vyhladí plošné barvy v PNG; linework (vrstevnice/černá) ponechá ostrý."""
    img = Image.open(png_path).convert("RGB")
    arr = np.asarray(img)
    line = _linework_mask(arr)
    # Median na všech kanálech → zaoblí blokové hranice ploch.
    smoothed = median_filter(arr, size=(size, size, 1))
    # Plochy vezmou vyhlazenou verzi, linework zůstane původní (ostrý).
    out = np.where(line[..., None], arr, smoothed)
    Image.fromarray(out.astype(np.uint8), "RGB").save(png_path)
