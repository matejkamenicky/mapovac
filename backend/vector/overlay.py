"""Raster overlay OSM features na pullauta PNG.

Vstup: PNG + PGW (world file) v S-JTSK, OSM JSON, cílové měřítko.
Výstup: nový PNG se overlay; PGW se přebírá beze změny.

Implementace: PIL ImageDraw. Pro nepřímé efekty (přerušované linie, ISOM dvojté
linie u silnic atd.) jdeme zjednodušeně — přesný ISOM symbol set přijde v M6
s pořádným rendering engine.
"""
from __future__ import annotations

from pathlib import Path

from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw

from backend.lidar.crop import WorldFile
from backend.vector.isom_mapping import IsomStyle, px_per_mm, symbol_scale_factor
from backend.vector.osm_features import Feature, parse


def _world_to_pixel(wf: WorldFile, x: float, y: float) -> tuple[float, float]:
    return wf.world_to_pixel(x, y)


def _mm_to_px(mm: float, target_scale: int) -> float:
    """mm na mapě → pixely výstupního rasteru, se zvětšením dle měřítka."""
    return mm * px_per_mm(target_scale) * symbol_scale_factor(target_scale)


def _stroke_width_px(style: IsomStyle, target_scale: int) -> int:
    return max(1, int(round(_mm_to_px(style.width_mm, target_scale))))


def _outline_width_px(style: IsomStyle, target_scale: int) -> int:
    return max(0, int(round(_mm_to_px(style.outline_width_mm, target_scale))))


def _draw_polyline(
    draw: ImageDraw.ImageDraw,
    pixels: list[tuple[float, float]],
    color,
    width: int,
) -> None:
    """Plná linie s kulatými spoji ve vrcholech.

    PIL `draw.line` se širokou šířkou nechává mezery v ohybech; u silnějších
    linií (silnice, lemovky) je dorovnáme kruhem v každém vnitřním vrcholu.
    """
    draw.line(pixels, fill=color, width=width)
    if width >= 3:
        r = width / 2.0
        for x, y in pixels[1:-1]:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


def _whiten_forest(img: Image.Image, forest_feats, to_pixels) -> Image.Image:
    """Vybělí lesní plochy na čistě bílou, ale zachová vrstevnice/symboly.

    Uvnitř lesních polygonů převedeme pullauta žlutou (otevřený terén) na bílou
    podle „žlutosti" pixelu (zelený kanál), zatímco tmavé hnědé vrstevnice a
    černé symboly zůstanou. Smooth přechod přes G drží anti-aliasing vrstevnic.
    """
    mask_img = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask_img)
    for f in forest_feats:
        for ring in to_pixels(f.parts):
            if len(ring) >= 3:
                md.polygon(ring, fill=255)
    mask = np.asarray(mask_img, dtype=bool)
    if not mask.any():
        return img

    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    g = arr[..., 1]
    # alpha=1 pro žlutý/světlý podklad (G vysoké), 0 pro hnědou vrstevnici
    # (G nízké) → vrstevnice se nepřebílí.
    alpha = np.clip((g - 150.0) / (233.0 - 150.0), 0.0, 1.0)
    alpha = np.where(mask, alpha, 0.0)[..., None]
    arr = arr * (1.0 - alpha) + 255.0 * alpha
    out = Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")
    out.putalpha(255)
    return out


def _draw_ticks(
    draw: ImageDraw.ImageDraw,
    pixels: list[tuple[float, float]],
    color,
    width: int,
    spacing_px: float,
    tick_len_px: float,
) -> None:
    """Kolmé zoubky podél polyline (ISOM plot 524)."""
    if spacing_px <= 0 or tick_len_px <= 0:
        return
    acc = spacing_px  # první zoubek až po `spacing` od začátku
    cur = pixels[0]
    for nxt in pixels[1:]:
        dx, dy = nxt[0] - cur[0], nxt[1] - cur[1]
        seg = (dx * dx + dy * dy) ** 0.5
        if seg == 0:
            cur = nxt
            continue
        ux, uy = dx / seg, dy / seg
        # normála (kolmice)
        nx, ny = -uy, ux
        t = acc
        while t < seg:
            px = cur[0] + ux * t
            py = cur[1] + uy * t
            # zoubek na jednu stranu (ISOM 516 plot)
            draw.line(
                [(px, py), (px + nx * tick_len_px, py + ny * tick_len_px)],
                fill=color, width=width,
            )
            t += spacing_px
        acc = t - seg
        cur = nxt


def _draw_polyline_dashed(
    draw: ImageDraw.ImageDraw,
    pixels: list[tuple[float, float]],
    color,
    width: int,
    dash_px: float,
    gap_px: float,
) -> None:
    """Pseudo-dashed: rozseká polyline na úseky dle délky."""
    if dash_px <= 0:
        draw.line(pixels, fill=color, width=width)
        return
    on = True
    remaining = dash_px
    cur = pixels[0]
    seg_start = cur
    for nxt in pixels[1:]:
        dx, dy = nxt[0] - cur[0], nxt[1] - cur[1]
        seg_len = (dx * dx + dy * dy) ** 0.5
        if seg_len == 0:
            cur = nxt
            continue
        ux, uy = dx / seg_len, dy / seg_len
        traveled = 0.0
        while traveled + remaining < seg_len:
            end = (cur[0] + ux * (traveled + remaining), cur[1] + uy * (traveled + remaining))
            if on:
                draw.line([seg_start, end], fill=color, width=width)
            traveled += remaining
            on = not on
            remaining = dash_px if on else gap_px
            seg_start = end
        # zbytek segmentu zatím nedokončený
        if on:
            draw.line([seg_start, nxt], fill=color, width=width)
            seg_start = nxt
        else:
            seg_start = nxt
        remaining -= (seg_len - traveled)
        cur = nxt


def render_overlay(
    base_png: Path,
    pgw: Path,
    osm_json: Path | None,
    out_png: Path,
    target_scale: int,
    extra_features: Iterable | None = None,
) -> Path:
    img = Image.open(base_png).convert("RGBA")
    wf = WorldFile.read(pgw)

    features: list = []
    if osm_json is not None and osm_json.exists():
        features.extend(parse(osm_json))
    if extra_features:
        features.extend(extra_features)
    # Z-order: nižší z první (podklady → cesty → budovy)
    features.sort(key=lambda f: f.style.z)

    width_px, height_px = img.size

    def _to_pixels(parts: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
        out = []
        for part in parts:
            px = [_world_to_pixel(wf, x, y) for x, y in part]
            # ořez polylines na obrazové okno: PIL si poradí s pixely mimo, ale
            # zúžíme rozsah, abychom šetřili práci.
            if any(-1000 < x < width_px + 1000 and -1000 < y < height_px + 1000 for x, y in px):
                out.append(px)
        return out

    # --- Les (ISOM 405): čistě bílá výplň přímo do podkladu ---
    # Pullauta kreslí podklad žlutý (otevřený terén) i v lese, protože náš
    # densifikovaný LAZ obsahuje jen zem (žádná vegetace). Les proto vybělíme
    # selektivně: žlutou plochu převedeme na bílou, ale hnědé vrstevnice a černé
    # symboly necháme. Rozlišení podle zeleného kanálu (žlutá G≈233, hnědá
    # vrstevnice G≈105). Forest features tím vyřadíme z normálního overlay.
    forest_feats = [f for f in features if f.style.isom_code == "405"]
    features = [f for f in features if f.style.isom_code != "405"]
    if forest_feats:
        img = _whiten_forest(img, forest_feats, _to_pixels)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    drawn = 0
    for feat in features:
        s = feat.style
        parts_px = _to_pixels(feat.parts)
        if not parts_px:
            continue
        if s.kind == "polygon":
            for ring in parts_px:
                if len(ring) >= 3:
                    draw.polygon(ring, fill=s.color)
                    if s.outline and s.outline_width_mm > 0:
                        draw.line(ring, fill=s.outline,
                                  width=_outline_width_px(s, target_scale))
            drawn += 1
        elif s.kind == "line":
            w = _stroke_width_px(s, target_scale)
            # Lemovka (ISOM dvojitá linie u širokých silnic): nejdřív širší
            # černá pod výplní.
            casing_w = (
                _mm_to_px(s.casing_width_mm, target_scale)
                if s.casing_color and s.casing_width_mm > 0
                else 0
            )
            for poly in parts_px:
                if s.dash_mm:
                    dash_px = _mm_to_px(s.dash_mm[0], target_scale)
                    gap_px = _mm_to_px(s.dash_mm[1], target_scale)
                    _draw_polyline_dashed(draw, poly, s.color, w, dash_px, gap_px)
                else:
                    if casing_w >= 1:
                        _draw_polyline(draw, poly, s.casing_color,
                                       max(1, int(round(casing_w))))
                    _draw_polyline(draw, poly, s.color, w)
                # ISOM zoubky (plot 524) — kolmé čárky podél plné linie
                if s.tick_spacing_mm > 0 and s.tick_len_mm > 0:
                    _draw_ticks(
                        draw, poly, s.color, w,
                        _mm_to_px(s.tick_spacing_mm, target_scale),
                        _mm_to_px(s.tick_len_mm, target_scale),
                    )
            drawn += 1

    out = Image.alpha_composite(img, overlay).convert("RGB")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_png, format="PNG")
    return out_png
