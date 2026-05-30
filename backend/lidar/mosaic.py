"""Mosaic pullauta PNG dlaždic + crop na uživatelův bbox.

Pullauta vyrobí PNG + PGW pro každou SM5 dlaždici. Všechny mají stejné
pixel size (0.4233 m při 600 DPI / 1:10 000) v S-JTSK. Mosaicujeme je do
jednoho rastru pokrývajícího bbox a pak ořežeme.

Pixel-perfect alignment je zaručen, protože pullauta používá deterministický
grid (1m v terénu × scale faktor). Mezery mezi dlaždicemi (kvůli Krovák
zkreslení nebo gaps v ČÚZK datech) zůstanou bílé.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from backend.data_sources.projection import bbox_wgs_to_sjtsk
from backend.lidar.crop import WorldFile


@dataclass(frozen=True)
class TileInput:
    png: Path
    pgw: Path


def mosaic_and_crop(
    tiles: list[TileInput],
    bbox_wgs: tuple[float, float, float, float],
    out_png: Path,
    target_scale: int = 10000,
    source_scale: int = 10000,
    background: tuple[int, int, int] = (255, 255, 255),
) -> Path:
    """Spojí pullauta dlaždice a ořeže na uživatelův bbox.

    Předpoklady: všechny dlaždice mají identické `pixel_size` a osy zarovnané.
    Pokud ne, nejbližší dlaždice se rozhoduje per-pixel (jednoduchý paste s
    překrytím; pozdější dlaždice mohou přebijet dřívější — pořadí zachovává
    vstup).
    """
    if not tiles:
        raise ValueError("Žádné dlaždice k merge")

    # Extent bboxu v S-JTSK
    xmin, ymin, xmax, ymax = bbox_wgs_to_sjtsk(bbox_wgs)

    # Pixel size z první dlaždice (všechny by měly být stejné — ověříme)
    wfs = [WorldFile.read(t.pgw) for t in tiles]
    src_pix = wfs[0].pixel_size_x
    for wf in wfs[1:]:
        if abs(wf.pixel_size_x - src_pix) > 1e-6:
            raise ValueError(
                f"Pullauta dlaždice mají různou pixel velikost: {src_pix} vs {wf.pixel_size_x}"
            )

    width = max(1, int(round((xmax - xmin) / src_pix)))
    height = max(1, int(round((ymax - ymin) / src_pix)))
    canvas = Image.new("RGB", (width, height), background)

    for tile, wf in zip(tiles, wfs):
        src = Image.open(tile.png).convert("RGB")
        # Offset NW rohu zdroje vůči NW rohu canvasu (xmin, ymax)
        col = int(round((wf.origin_x - xmin) / src_pix))
        row = int(round((ymax - wf.origin_y) / src_pix))
        canvas.paste(src, (col, row))

    # Resample do cílového měřítka
    if target_scale != source_scale:
        factor = source_scale / target_scale
        new_size = (int(canvas.width * factor), int(canvas.height * factor))
        canvas = canvas.resize(new_size, Image.LANCZOS)
        out_pix = src_pix * target_scale / source_scale
    else:
        out_pix = src_pix

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png, format="PNG")

    out_pgw = out_png.with_suffix(".pgw")
    out_pgw.write_text(
        f"{out_pix}\n0.0\n0.0\n{-out_pix}\n{xmin}\n{ymax}\n",
        encoding="utf-8",
    )
    return out_png
