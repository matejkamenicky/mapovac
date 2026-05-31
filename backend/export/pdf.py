"""Tisknutelný PDF export mapy v PRAVÉM měřítku (1:10 000 / 1:7 500).

Rastr (preview) se uloží do PDF s DPI tak, aby vytištěná mapa měla přesné
měřítko: 1 mm na papíře = `scale` mm v terénu. DPI počítáme z velikosti pixelu
ve world file:

    dpi = 25.4 · scale / (pixel_size_m · 1000)

(pro pullauta 600 dpi @ 1:10 000 vyjde 600). Přidá se bílý okraj a titulek
(měřítko, bbox, datum). Bez závislosti na reportlabu — jen Pillow.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from backend.lidar.crop import WorldFile


def _font(size: int):
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def export_pdf(
    preview_png: Path,
    pgw: Path,
    out_pdf: Path,
    scale: int,
    bbox_wgs: tuple[float, float, float, float] | None = None,
    margin_mm: float = 10.0,
) -> Path:
    img = Image.open(preview_png).convert("RGB")
    wf = WorldFile.read(pgw)
    px_m = abs(wf.pixel_size_x)
    # DPI pro tisk v pravém měřítku.
    dpi = max(50.0, 25.4 * scale / (px_m * 1000.0))

    margin = int(round(margin_mm / 25.4 * dpi))
    title_h = int(round(8.0 / 25.4 * dpi))  # pruh na titulek
    W, H = img.size
    canvas = Image.new("RGB", (W + 2 * margin, H + 2 * margin + title_h), (255, 255, 255))
    canvas.paste(img, (margin, margin + title_h))

    draw = ImageDraw.Draw(canvas)
    font = _font(int(round(4.5 / 25.4 * dpi)))  # ~4.5 mm text
    date = datetime.date.today().isoformat()
    title = f"Mapa pro OB  1:{scale:,}".replace(",", " ")
    sub = f"ISOM 2017-2  ·  {date}"
    if bbox_wgs:
        w, s, e, n = bbox_wgs
        sub += f"  ·  střed {(s + n) / 2:.4f}, {(w + e) / 2:.4f}"
    draw.text((margin, int(margin * 0.4)), title, fill=(0, 0, 0), font=font)
    small = _font(int(round(3.0 / 25.4 * dpi)))
    draw.text((margin, int(margin * 0.4) + int(5.0 / 25.4 * dpi)), sub,
              fill=(60, 60, 60), font=small)
    # rámeček kolem mapy
    draw.rectangle(
        [margin - 2, margin + title_h - 2, margin + W + 1, margin + title_h + H + 1],
        outline=(0, 0, 0), width=max(1, int(0.2 / 25.4 * dpi)),
    )

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_pdf, "PDF", resolution=dpi)
    return out_pdf
