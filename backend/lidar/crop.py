"""Crop pullauta výstupu na uživatelův bbox v S-JTSK.

Pullauta píše PNG + PGW world file. PGW obsahuje afinní transformaci
(pixelSize_x, rotation, rotation, pixelSize_y, originX, originY).
Crop děláme čistě pomocí PIL + numpy, bez závislosti na rasterio (rychlejší
build, méně systémových libs).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from backend.data_sources.projection import bbox_wgs_to_sjtsk


@dataclass(frozen=True)
class WorldFile:
    pixel_size_x: float
    rotation_y: float
    rotation_x: float
    pixel_size_y: float  # typicky záporné
    origin_x: float      # střed levého horního pixelu
    origin_y: float

    @classmethod
    def read(cls, path: Path) -> "WorldFile":
        vals = [float(x.strip()) for x in path.read_text().splitlines()[:6]]
        return cls(*vals)

    def world_to_pixel(self, x: float, y: float) -> tuple[float, float]:
        # Inverze afinního: pro nerotovaný world file (rotation=0) zjednodušeně:
        col = (x - self.origin_x) / self.pixel_size_x
        row = (y - self.origin_y) / self.pixel_size_y
        return col, row


def crop_png_to_bbox_wgs(
    png_path: Path,
    pgw_path: Path,
    bbox_wgs: tuple[float, float, float, float],
    out_png: Path,
    target_scale: int = 10000,
    source_scale: int = 10000,
) -> Path:
    """Ořeže PNG (+ PGW) na bbox a volitelně přeškáluje na cílové měřítko.

    target_scale < source_scale → zvětšuje (1:7 500 vs nativní 1:10 000).
    """
    xmin, ymin, xmax, ymax = bbox_wgs_to_sjtsk(bbox_wgs)
    wf = WorldFile.read(pgw_path)

    col_l, row_t = wf.world_to_pixel(xmin, ymax)  # left-top v pixel souř.
    col_r, row_b = wf.world_to_pixel(xmax, ymin)
    left = max(0, int(round(min(col_l, col_r))))
    right = int(round(max(col_l, col_r)))
    top = max(0, int(round(min(row_t, row_b))))
    bottom = int(round(max(row_t, row_b)))

    img = Image.open(png_path)
    right = min(right, img.width)
    bottom = min(bottom, img.height)
    if right <= left or bottom <= top:
        raise ValueError(
            f"BBox je mimo extent pullauta výstupu (crop {left},{top}-{right},{bottom})."
        )
    cropped = img.crop((left, top, right, bottom))

    if target_scale != source_scale:
        factor = source_scale / target_scale
        new_size = (int(cropped.width * factor), int(cropped.height * factor))
        cropped = cropped.resize(new_size, Image.LANCZOS)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(out_png, format="PNG")

    # Vytvoříme i odpovídající PGW (origin = xmin/ymax cropu)
    out_pgw = out_png.with_suffix(".pgw")
    pix_size = wf.pixel_size_x * (source_scale / target_scale)
    out_pgw.write_text(
        f"{pix_size}\n0.0\n0.0\n{-pix_size}\n{xmin}\n{ymax}\n",
        encoding="utf-8",
    )
    return out_png
