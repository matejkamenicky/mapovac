"""Klasifikace vegetace z CHM podle ISOM 2017 + vektorizace na polygony.

ISOM 2017 vegetační symboly (relevantní):
- 405 — Forest: snadno průchozí → bílá (žádný overlay)
- 406 — Vegetation: slow running → světle zelená (cca 50% sytost)
- 408 — Vegetation: walk → zelená
- 410 — Vegetation: fight → tmavě zelená
- 401–404 — otevřená plocha → žlutá

Klasifikace z CHM (Canopy Height Model):
- < 0.5 m  → otevřeno (žlutá → ISOM 401, NEKLASIFIKUJEME pokud nemáme i ground type)
- 0.5–2 m  → křoviny/mladý porost → ISOM 406 (light green)
- 2–6 m    → střední vegetace → ISOM 408 (medium green)
- 6–15 m   → vysoký podrost → ISOM 408 (medium green) nebo 405 (bílá) podle hustoty
- > 15 m   → vzrostlý les → bílá (= ISOM 405, neoverlay)

Vektorizace: scikit-image find_contours per třída maska → seznam polygonů.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np
from shapely.geometry import Polygon
from skimage import measure
from skimage.morphology import (
    binary_closing,
    binary_opening,
    disk,
    remove_small_holes,
    remove_small_objects,
)

from backend.lidar.chm import Raster
from backend.vector.isom_mapping import IsomStyle, _poly


# Polygony překládáme do S-JTSK; pro nakreslení v overlay budou převedeny
# přímo přes PGW (overlay.py se umí o souřadnice postarat).

LIGHT_GREEN = (170, 215, 165, 200)   # ISOM 406
MEDIUM_GREEN = (110, 190, 120, 220)  # ISOM 408
DARK_GREEN = (60, 145, 95, 230)      # ISOM 410


VEG_STYLES: dict[str, IsomStyle] = {
    "veg_light": _poly("406", LIGHT_GREEN, z=4),
    "veg_medium": _poly("408", MEDIUM_GREEN, z=4),
    "veg_dense": _poly("410", DARK_GREEN, z=4),
}

# ISOM 405 Forest — bílá. Reálná barva nehraje roli: overlay.py polygony s
# isom_code "405" zpracuje speciálně (selektivní vybělení žlutého podkladu,
# vrstevnice zůstanou). z=1 → pod zelenou vegetací i ostatními prvky.
FOREST_STYLE = _poly("405", (255, 255, 255, 255), z=1)


@dataclass(frozen=True)
class VegFeature:
    style: IsomStyle
    parts: list[list[tuple[float, float]]]  # S-JTSK rings
    tags: dict


def classify(chm: Raster) -> dict[str, np.ndarray]:
    """CHM → masky binární masky per ISOM třída.

    Hranice tříd jsou přiškrcené tak, aby tmavě zelené (fight) byly
    opravdu jen husté koruny, ne každý vzrostlý strom.
    """
    a = chm.array
    # POZOR: výška zápoje ≠ průchodnost. Vzrostlý les (>5 m) považujeme za
    # průchodný → BÍLÝ (les se vykreslí přes `forest_feature`), žádná zelená.
    # Zelenou rezervujeme jen pro NÍZKOU/hustou vegetaci (mlází, křoví, nálety),
    # která reálně brzdí běh. Bez plného point cloudu (jen zem+povrch) hustotu
    # podrostu neměříme, takže tohle je konzervativní ISOM odhad.
    return {
        "veg_light": (a >= 0.5) & (a < 2.0),    # 406 — nízké mlází/křoví
        "veg_medium": (a >= 2.0) & (a < 3.5),   # 408 — hustší nálet
        "veg_dense": (a >= 3.5) & (a < 5.0),    # 410 — husté mladé houští
        # > 5m → vzrostlý les → bílá (neoverlay)
    }


def _mask_pixel_to_sjtsk(row: float, col: float, chm: Raster) -> tuple[float, float]:
    x = chm.xmin + (col + 0.5) * chm.resolution
    y = chm.ymax - (row + 0.5) * chm.resolution
    return x, y


def _generalize_mask(mask: np.ndarray, closing_px: int, opening_px: int,
                     min_pixels: int) -> np.ndarray:
    """Morfologická generalizace:
    1) closing — slepí blízké stromy do souvislých korun (zaplní mezery)
    2) opening — odstraní úzké výběžky a izolované jednotlivé stromy
    3) remove_small_objects — definitivně odfiltruje malé skvrny
    """
    if not mask.any():
        return mask
    if closing_px > 0:
        mask = binary_closing(mask, footprint=disk(closing_px))
    if opening_px > 0:
        mask = binary_opening(mask, footprint=disk(opening_px))
    if min_pixels > 1 and mask.any():
        mask = remove_small_objects(mask, min_size=min_pixels, connectivity=2)
    return mask


def _round_polygon(poly: Polygon, radius_m: float):
    """Zaoblí rohy polygonu morfologickým open+close přes buffer.

    buffer(+r).buffer(-r) zaoblí konvexní rohy (a slepí blízké výběžky),
    buffer(-r).buffer(+r) zaoblí konkávní (a odstraní úzké zářezy). Spojením
    obojího vznikne plynulý „organický" obrys místo pixelového schodiště.
    Použijeme kulaté spoje (join_style=1).
    """
    if radius_m <= 0:
        return poly
    try:
        rounded = (
            poly.buffer(radius_m, join_style=1)
            .buffer(-2 * radius_m, join_style=1)
            .buffer(radius_m, join_style=1)
        )
        if rounded.is_empty:
            return poly
        return rounded
    except Exception:
        return poly


def _iter_polygons(geom):
    """Vrátí jednotlivé Polygony z Polygon/MultiPolygon."""
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield geom
    elif geom.geom_type == "MultiPolygon":
        yield from geom.geoms


def _vectorize_mask(
    mask: np.ndarray, chm: Raster,
    simplify_tolerance_m: float = 1.5,
    round_radius_m: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Mask → polygony v S-JTSK, vyhlazené přes zaoblení rohů + simplify."""
    if not mask.any():
        return []
    # Padding nulovým okrajem: find_contours neuzavře kontury, které se dotýkají
    # hrany pole → plochy u okraje (typicky les přes celý výřez) by se rozpadly
    # na fragmenty. Padding o 1px je uzavře; souřadnice pak posuneme zpět (−1).
    padded = np.pad(mask.astype(np.uint8), 1, constant_values=0)
    contours = measure.find_contours(padded, level=0.5)
    rings: list[list[tuple[float, float]]] = []
    for c in contours:
        if len(c) < 4:
            continue
        ring_world = [_mask_pixel_to_sjtsk(row - 1, col - 1, chm) for row, col in c]
        try:
            poly = Polygon(ring_world)
            if not poly.is_valid:
                poly = poly.buffer(0)  # auto-fix self-intersections
            if poly.is_empty or poly.area < 1.0:
                continue
            # Nejdřív zaoblit rohy (organický tvar), pak zjednodušit počet bodů.
            geom = _round_polygon(poly, round_radius_m)
            geom = geom.simplify(simplify_tolerance_m, preserve_topology=True)
            for part in _iter_polygons(geom):
                if part.area < 1.0:
                    continue
                ring = list(part.exterior.coords)
                if len(ring) < 4:
                    continue
                rings.append(ring)
        except Exception:
            continue
    return rings


def features_from_chm(
    chm: Raster,
    min_area_m2: float = 40.0,
    closing_m: float = 3.0,
    opening_m: float = 0.0,
    simplify_m: float = 1.5,
    round_m: float = 0.0,
) -> Iterator[VegFeature]:
    """Klasifikuje CHM, generalizuje masky a vrátí ISOM vegetační polygony.

    Default-tuning po prvním reálném testu (Stromovka):
    - closing_m=3 → spojí blízké stromy v souvislé koruny (lesní okraje, háje)
    - opening_m=0 → ZÁMĚRNĚ vypnuto, jinak mizí celé háje a remízky
    - min_area_m2=40 → individuální stromy <40 m² (~7×6 m) vypadnou, skupinky zůstanou
    - simplify_m=1.5 → vyhlazení hran z pixel-staircase na plynulou linii
    """
    min_px = max(1, int(min_area_m2 / (chm.resolution ** 2)))
    closing_px = max(0, int(round(closing_m / chm.resolution)))
    opening_px = max(0, int(round(opening_m / chm.resolution)))
    masks = classify(chm)
    for key, mask in masks.items():
        m = _generalize_mask(mask, closing_px, opening_px, min_px)
        rings = _vectorize_mask(
            m, chm, simplify_tolerance_m=simplify_m, round_radius_m=round_m
        )
        if not rings:
            continue
        yield VegFeature(style=VEG_STYLES[key], parts=rings,
                         tags={"chm_class": key})


def forest_feature(
    forest_mask: np.ndarray,
    chm: Raster,
    closing_m: float = 7.5,
    hole_fill_m2: float = 80.0,
    min_area_m2: float = 200.0,
    simplify_m: float = 2.5,
    round_m: float = 3.0,
) -> VegFeature | None:
    """Z LiDAR masky lesa udělá jeden bílý ISOM 405 polygon s organickým okrajem.

    Maska z `compute_chm_and_forest` je „sůl a pepř" (řídké pozemní návraty),
    proto silnější generalizace:
    1) closing — slepí zápoj a zaplní mezery mezi stromy,
    2) remove_small_holes — zacelí dírky uvnitř lesa (jinak žluté tečky),
    3) remove_small_objects — zahodí ojedinělé stromy v poli,
    4) zaoblení rohů + simplify → plynulý okraj místo schodiště.
    """
    if not forest_mask.any():
        return None
    res = chm.resolution
    closing_px = max(1, int(round(closing_m / res)))
    min_px = max(1, int(min_area_m2 / (res ** 2)))
    hole_px = max(1, int(hole_fill_m2 / (res ** 2)))

    m = binary_closing(forest_mask, footprint=disk(closing_px))
    m = remove_small_holes(m, area_threshold=hole_px)
    m = remove_small_objects(m, min_size=min_px, connectivity=2)
    if not m.any():
        return None
    rings = _vectorize_mask(
        m, chm, simplify_tolerance_m=simplify_m, round_radius_m=round_m
    )
    if not rings:
        return None
    return VegFeature(style=FOREST_STYLE, parts=rings, tags={"landcover": "forest"})
