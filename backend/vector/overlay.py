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


def _draw_dots_along(draw, pts, color, radius: int, spacing_px: float) -> None:
    """Plné kruhy rovnoměrně podél polyline (ISOM 416 tečkovaná hranice)."""
    if spacing_px <= 0:
        return
    acc = 0.0
    cur = pts[0]
    def dot(x, y):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    dot(*cur)
    for nxt in pts[1:]:
        dx, dy = nxt[0] - cur[0], nxt[1] - cur[1]
        seg = (dx * dx + dy * dy) ** 0.5
        if seg == 0:
            cur = nxt
            continue
        ux, uy = dx / seg, dy / seg
        t = spacing_px - acc
        while t < seg:
            dot(cur[0] + ux * t, cur[1] + uy * t)
            t += spacing_px
        acc = (seg + acc) % spacing_px
        cur = nxt


def _smooth_polyline(pts, iters: int = 2):
    """Zaoblí lomené body linie (Chaikinovo „corner cutting").

    OSM cesty mají řídké vrcholy → ostré úhly. Každá iterace nahradí segment
    dvěma body v 1/4 a 3/4 → zaoblené rohy. Koncové body ZACHOVÁME (napojení
    na křižovatky/sousední linie zůstane). 2 iterace = dostatečně hladké,
    minimální odchylka od původní geometrie.
    """
    if len(pts) < 3:
        return pts
    for _ in range(max(0, iters)):
        out = [pts[0]]
        for i in range(len(pts) - 1):
            (px, py), (qx, qy) = pts[i], pts[i + 1]
            out.append((0.75 * px + 0.25 * qx, 0.75 * py + 0.25 * qy))
            out.append((0.25 * px + 0.75 * qx, 0.25 * py + 0.75 * qy))
        out.append(pts[-1])
        pts = out
    return pts


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
    both_sides: bool = False,
    angle_deg: float = 90.0,
) -> None:
    """Zoubky/příčky podél polyline pod úhlem `angle_deg` od směru linie.

    `angle_deg=90` → kolmo. ISOM 516 plot má zoubky pod 60° (skloněné dopředu).
    `both_sides=False` → zoubek na jednu stranu (plot 516).
    `both_sides=True`  → příčka na obě strany, vystředěná na linii (vedení 510).
    """
    if spacing_px <= 0 or tick_len_px <= 0:
        return
    import math
    ca, sa = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    acc = spacing_px  # první zoubek až po `spacing` od začátku
    cur = pixels[0]
    for nxt in pixels[1:]:
        dx, dy = nxt[0] - cur[0], nxt[1] - cur[1]
        seg = (dx * dx + dy * dy) ** 0.5
        if seg == 0:
            cur = nxt
            continue
        ux, uy = dx / seg, dy / seg
        nx, ny = -uy, ux  # normála (kolmice)
        # směr zoubku = složka podél linie (cos) + kolmá složka (sin)
        tx, ty = ux * ca + nx * sa, uy * ca + ny * sa
        t = acc
        while t < seg:
            px = cur[0] + ux * t
            py = cur[1] + uy * t
            p0 = (px - tx * tick_len_px, py - ty * tick_len_px) if both_sides else (px, py)
            p1 = (px + tx * tick_len_px, py + ty * tick_len_px)
            draw.line([p0, p1], fill=color, width=width)
            t += spacing_px
        acc = t - seg
        cur = nxt


def _draw_hline_pattern(overlay, rings, style, target_scale) -> None:
    """Vodorovné čárky uvnitř polygonu (ISOM 308 marsh)."""
    spacing = max(2, int(round(_mm_to_px(style.pattern_spacing_mm, target_scale))))
    line_w = max(1, int(round(_mm_to_px(0.15, target_scale))))
    color = style.pattern_color or style.color
    mask = Image.new("L", overlay.size, 0)
    md = ImageDraw.Draw(mask)
    for ring in rings:
        if len(ring) >= 3:
            md.polygon(ring, fill=255)
    bbox = mask.getbbox()
    if not bbox:
        return
    x0, y0, x1, y1 = bbox
    patt = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(patt)
    for yy in range(y0, y1 + 1, spacing):
        pd.line([(x0, yy), (x1, yy)], fill=color, width=line_w)
    patt.putalpha(Image.composite(patt.getchannel("A"), Image.new("L", overlay.size, 0), mask))
    overlay.alpha_composite(patt)


def _draw_ties(draw, pixels, color, width, spacing_px, tie_len_px) -> None:
    """Kolmé příčky vystředěné na linii (ISOM 509 železnice — pražce)."""
    if spacing_px <= 0 or tie_len_px <= 0:
        return
    acc = spacing_px
    cur = pixels[0]
    half = tie_len_px / 2.0
    for nxt in pixels[1:]:
        dx, dy = nxt[0] - cur[0], nxt[1] - cur[1]
        seg = (dx * dx + dy * dy) ** 0.5
        if seg == 0:
            cur = nxt
            continue
        ux, uy = dx / seg, dy / seg
        nx, ny = -uy, ux
        t = acc
        while t < seg:
            px = cur[0] + ux * t
            py = cur[1] + uy * t
            draw.line([(px - nx * half, py - ny * half),
                       (px + nx * half, py + ny * half)], fill=color, width=width)
            t += spacing_px
        acc = t - seg
        cur = nxt


def _draw_polyline_pattern(
    draw: ImageDraw.ImageDraw,
    pixels: list[tuple[float, float]],
    color,
    width: int,
    pattern: list[tuple[float, bool]],
) -> None:
    """Vykreslí polyline podle cyklického vzoru (délka_px, kreslit?).

    Umožní jak prostou čárku [(dash,True),(gap,False)], tak skupinové dvojité
    čárkování ISOM 507 [(dash,True),(in_gap,False),(dash,True),(big_gap,False)].
    """
    pattern = [(L, on) for (L, on) in pattern if L > 0] or [(1.0, True)]
    idx = 0
    remaining, on = pattern[0]
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
            idx = (idx + 1) % len(pattern)
            remaining, on = pattern[idx]
            seg_start = end
        if on:
            draw.line([seg_start, nxt], fill=color, width=width)
        seg_start = nxt
        remaining -= (seg_len - traveled)
        cur = nxt


def _draw_polyline_dashed(
    draw: ImageDraw.ImageDraw,
    pixels: list[tuple[float, float]],
    color,
    width: int,
    dash_px: float,
    gap_px: float,
) -> None:
    """Prosté čárkování — speciální případ vzoru [(dash,on),(gap,off)]."""
    if dash_px <= 0:
        draw.line(pixels, fill=color, width=width)
        return
    _draw_polyline_pattern(draw, pixels, color, width, [(dash_px, True), (gap_px, False)])


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
    # Výplň land-cover řeší rastrový podklad (pullauta + recolor + openland).
    # Forest (405) a paseku (403) vynecháme úplně. Louku (401) a pole (412)
    # z autoritativního OSM/ZABAGED necháme — ale jen jako ČERNÝ OBRYS (bez
    # výplně), jako na mapant. LiDAR paseky obrys nemají (nejsou to Feature).
    # 403 paseka / 405 les / 213 holá skála: výplň řeší rastr (zachová
    # vrstevnice/srázy), overlay je NEkreslí, ať nepřekryjí detail neprůhlednou plochou.
    features = [f for f in features
                if not (f.style.kind == "polygon"
                        and f.style.isom_code in ("403", "405", "213"))]
    _OUTLINE_ONLY = {"401", "412", "520"}  # louka/pole/zákaz vstupu → jen černý obrys
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
        if s.kind == "point":
            r = max(1, int(round(_mm_to_px(s.point_radius_mm, target_scale))))
            ow = (int(round(_mm_to_px(s.point_outline_mm, target_scale)))
                  if s.point_outline_color and s.point_outline_mm > 0 else 0)
            for part in parts_px:
                for (x, y) in part:
                    bbox = (x - r, y - r, x + r, y + r)
                    if ow >= 1:
                        # kroužek (bílý/prázdný střed + barevný obrys)
                        draw.ellipse(bbox, fill=(255, 255, 255, 255))
                        draw.ellipse(bbox, outline=s.point_outline_color, width=ow)
                    else:
                        draw.ellipse(bbox, fill=s.color)
            drawn += 1
            continue
        if s.kind == "polygon":
            if s.pattern == "hlines":
                # ISOM 308 marsh — vodorovné modré čárky uvnitř polygonu.
                _draw_hline_pattern(overlay, parts_px, s, target_scale)
                drawn += 1
                continue
            if s.isom_code in _OUTLINE_ONLY:
                # Louka/pole (401/412) z OSM/ZABAGED — jen černý obrys, výplň je
                # už v rastru. Odlišuje reálné louky/pole od LiDAR pasek.
                bw = max(1, int(round(_mm_to_px(0.18, target_scale))))
                for ring in parts_px:
                    if len(ring) >= 3:
                        draw.line(ring + [ring[0]], fill=(0, 0, 0, 255), width=bw)
                drawn += 1
                continue
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
                poly = _smooth_polyline(poly)  # zaoblení ostrých úhlů cest
                if s.dot_line:
                    # ISOM 416 — tečkovaná linie (plné kruhy podél linie).
                    r = max(1, int(round(_mm_to_px(s.dot_radius_mm, target_scale))))
                    sp = max(2, _mm_to_px(s.dot_spacing_mm, target_scale))
                    _draw_dots_along(draw, poly, s.color, r, sp)
                    drawn += 1
                    continue
                if s.railway:
                    # ISOM 509 železnice: plná černá linie s úzkým černým borderem
                    # + bílé vnitřní mezery → černý okraj kolem, uvnitř střídání
                    # černá/bílá. (Black 0.15 mm border, vnitřek bílý v mezerách.)
                    _draw_polyline(draw, poly, s.color, w)
                    border = max(1, int(round(_mm_to_px(0.15, target_scale))))
                    inner_w = max(1, w - 2 * border)
                    if s.dash_mm:
                        # bílé úseky délky `gap` (1.5) ob `dash` (2.25) — užší než w
                        _draw_polyline_dashed(
                            draw, poly, (255, 255, 255, 255), inner_w,
                            _mm_to_px(s.dash_mm[1], target_scale),
                            _mm_to_px(s.dash_mm[0], target_scale),
                        )
                    continue
                if s.dash_mm:
                    dash_px = _mm_to_px(s.dash_mm[0], target_scale)
                    gap_px = _mm_to_px(s.dash_mm[1], target_scale)
                    if s.dash_group > 1:
                        # Skupinové dvojité čárkování (ISOM 507).
                        big_gap = _mm_to_px(s.dash_group_gap_mm, target_scale)
                        pat = []
                        for _i in range(s.dash_group):
                            pat.append((dash_px, True))
                            pat.append((gap_px, False))
                        pat[-1] = (big_gap, False)  # poslední mezera = mezi skupinami
                        _draw_polyline_pattern(draw, poly, s.color, w, pat)
                    else:
                        _draw_polyline_dashed(draw, poly, s.color, w, dash_px, gap_px)
                else:
                    if casing_w >= 1:
                        _draw_polyline(draw, poly, s.casing_color,
                                       max(1, int(round(casing_w))))
                    _draw_polyline(draw, poly, s.color, w)
                # ISOM zoubky/příčky — plot 516 (jedna strana), vedení 510 (obě).
                if s.tick_spacing_mm > 0 and s.tick_len_mm > 0:
                    tw = (max(1, int(round(_mm_to_px(s.tick_width_mm, target_scale))))
                          if s.tick_width_mm > 0 else w)
                    _draw_ticks(
                        draw, poly, s.color, tw,
                        _mm_to_px(s.tick_spacing_mm, target_scale),
                        _mm_to_px(s.tick_len_mm, target_scale),
                        both_sides=s.tick_both_sides,
                        angle_deg=s.tick_angle_deg,
                    )
            drawn += 1

    out = Image.alpha_composite(img, overlay).convert("RGB")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_png, format="PNG")
    return out_png
