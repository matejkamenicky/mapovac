"""Vytvoří prázdné bílé PNG + PGW pro bbox v S-JTSK — fallback když není LAZ.

Slouží jako podklad pro OSM overlay, aby uživatel získal aspoň základní mapu
cest a vodstva i bez LiDAR dat.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from backend.data_sources.projection import bbox_wgs_to_sjtsk
from backend.vector.isom_mapping import PX_PER_MM_AT_NATIVE


def make_blank(
    bbox_wgs: tuple[float, float, float, float],
    target_scale: int,
    out_png: Path,
) -> tuple[Path, Path]:
    """Vytvoří bílé PNG + PGW pokrývající bbox v cílovém měřítku.

    Pixel = 1 mm na mapě v target_scale, tj. v terénu target_scale * 0.001 m.
    Při 1:10 000 a 600 DPI → 1 pixel ≈ 0,423 m.
    """
    xmin, ymin, xmax, ymax = bbox_wgs_to_sjtsk(bbox_wgs)
    # velikost pixelu v metrech v terénu (1 mm na mapě × měřítko / 1000)
    pixel_size_m = target_scale / 1000.0 / PX_PER_MM_AT_NATIVE
    width = max(1, int(round((xmax - xmin) / pixel_size_m)))
    height = max(1, int(round((ymax - ymin) / pixel_size_m)))

    img = Image.new("RGB", (width, height), (255, 255, 255))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, format="PNG")

    pgw = out_png.with_suffix(".pgw")
    pgw.write_text(
        f"{pixel_size_m}\n0.0\n0.0\n{-pixel_size_m}\n{xmin}\n{ymax}\n",
        encoding="utf-8",
    )
    return out_png, pgw
