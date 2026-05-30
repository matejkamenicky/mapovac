"""Přemapování pullauta palety na přesné ISOM 2017 barvy.

Pullauta používá vlastní pevnou paletu (~13 barev): žlutá otevřeno, bílá les,
hnědé vrstevnice, černá, a osmiúrovňový zelený rampa hustoty vegetace. ISOM 2017
má ale pro vegetaci tři jasné stupně (Green 30 % / 60 % / 100 %) a konkrétní
CMYK barvy. Tady pullauta paletu namapujeme na ISOM (barvy převzaté z
oficiálního OO Mapper symbol setu „ISOM 2017-2_10000.omap").

Zelený rampa se binuje podle sytosti (nižší R = hustší porost) do tří ISOM
stupňů → na mapě jsou pak vidět tři odlišné hustoty „hustníku".
"""
from __future__ import annotations

import numpy as np
from PIL import Image

# ISOM 2017 barvy jako RGB (z OO Mapper symbol setu, naive CMYK→RGB).
ISOM_YELLOW = (255, 190, 55)     # Yellow  C0 M.27 Y.79
ISOM_BROWN = (209, 92, 0)        # Brown 100%  C0 M.56 Y1 K.18
ISOM_GREEN_100 = (61, 255, 23)   # Green 100% (fight)   C.76 M0 Y.91
ISOM_GREEN_60 = (139, 255, 116)  # Green 60% (walk)     C.456 M0 Y.546
ISOM_GREEN_30 = (197, 255, 185)  # Green 30% (slow run) C.228 M0 Y.273
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

# Pevné mapování pullauta barva → ISOM barva.
_DIRECT = {
    (255, 219, 166): ISOM_YELLOW,   # otevřená plocha
    (166, 85, 43): ISOM_BROWN,      # vrstevnice
    (0, 0, 0): BLACK,
    (255, 255, 255): WHITE,
    (64, 121, 0): ISOM_GREEN_100,   # tmavá olivová (podrost) → fight
}

# Zelený rampa pullauty → 3 ISOM stupně. Klíč = R složka pullauta zelené
# (nižší = hustší). Hranice binů:
def _green_to_isom(r: int) -> tuple[int, int, int]:
    # Pullauta zelený rampa (po vyladění greenshades) má R ~120–200;
    # nižší R = sytější = hustší porost.
    if r < 135:
        return ISOM_GREEN_100   # nejhustší → fight
    if r < 175:
        return ISOM_GREEN_60    # walk
    return ISOM_GREEN_30        # slow run


def recolor_to_isom(png_path) -> None:
    """Přemapuje barvy PNG na ISOM 2017 paletu (in-place)."""
    img = Image.open(png_path).convert("RGB")
    arr = np.asarray(img).copy()
    flat = arr.reshape(-1, 3)
    colors = np.unique(flat, axis=0)
    for col in colors:
        c = (int(col[0]), int(col[1]), int(col[2]))
        r, g, b = c
        if c in _DIRECT:
            target = _DIRECT[c]
        elif g > r and g > b and g > 150:   # zelený odstín vegetace
            target = _green_to_isom(r)
        else:
            continue
        mask = (flat[:, 0] == r) & (flat[:, 1] == g) & (flat[:, 2] == b)
        flat[mask] = target
    Image.fromarray(flat.reshape(arr.shape), "RGB").save(png_path)
