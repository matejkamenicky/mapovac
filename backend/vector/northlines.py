"""ISOM 601 — severojižní (magnetické) čáry.

Rovnoběžné čáry mířící k MAGNETICKÉMU severu, rozteč 300 m v terénu (= 20 mm
@ 1:15 000, 30 mm @ 1:10 000, 40 mm @ 1:7 500). Barva modrá (nebo černá),
tloušťka 0.12 mm.

Náš rastr je orientovaný na mřížkový sever S-JTSK (crop na osově zarovnanou
obálku). Magnetický sever se proto vůči svislici obrázku stáčí o úhel mezi
mřížkovým a magnetickým severem (= konvergence poledníků + deklinace). Ten
spočítáme přímo z projekce: porovnáme směr „o kousek na sever" v zeměpisných
souřadnicích převedený zpět do S-JTSK, a přidáme magnetickou deklinaci.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from backend.data_sources.projection import sjtsk_to_wgs, wgs_to_sjtsk
from backend.lidar.crop import WorldFile
from backend.vector.isom_mapping import px_per_mm

SPACING_GROUND_M = 300.0  # rozteč severek v terénu (ISOM: 300 m)
BLUE = (0, 200, 235)
BLACK = (0, 0, 0)


def _magnetic_north_world(wf: WorldFile, W: int, H: int, declination_deg: float):
    """Jednotkový vektor magnetického severu ve world (S-JTSK) souřadnicích."""
    cx = wf.origin_x + (W / 2.0) * wf.pixel_size_x
    cy = wf.origin_y + (H / 2.0) * wf.pixel_size_y
    lon, lat = sjtsk_to_wgs(cx, cy)
    # bod ~o 10 m severněji (pravý/zeměpisný sever) → směr v S-JTSK mřížce
    nx, ny = wgs_to_sjtsk(lon, lat + 0.0001)
    tx, ty = nx - cx, ny - cy
    norm = math.hypot(tx, ty) or 1.0
    tx, ty = tx / norm, ty / norm
    # rotace o -deklinaci (východní deklinace = magnetický sever na východ od
    # pravého severu, tj. ve směru hodinových ručiček v rovině X-východ/Y-sever)
    d = math.radians(declination_deg)
    mx = tx * math.cos(d) + ty * math.sin(d)
    my = -tx * math.sin(d) + ty * math.cos(d)
    return mx, my


def _clip_line(px, py, dx, dy, W, H):
    """Liang–Barsky ořez nekonečné přímky (bod+směr) na obdélník [0,W]×[0,H]."""
    t0, t1 = -1e9, 1e9
    for p, q in ((-dx, px - 0), (dx, W - px), (-dy, py - 0), (dy, H - py)):
        if abs(p) < 1e-9:
            if q < 0:
                return None  # rovnoběžná a mimo
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    if t0 > t1:
        return None
    return (px + t0 * dx, py + t0 * dy), (px + t1 * dx, py + t1 * dy)


def _line_pixels(wf: WorldFile, W: int, H: int, declination_deg: float):
    """Vrátí severky jako úsečky v PIXELECH (dvojice koncových bodů)."""
    mx, my = _magnetic_north_world(wf, W, H, declination_deg)
    # směr v pixelech (řádek roste dolů → dělíme zápornou pixel_size_y)
    dpx, dpy = mx / wf.pixel_size_x, my / wf.pixel_size_y
    dn = math.hypot(dpx, dpy) or 1.0
    dpx, dpy = dpx / dn, dpy / dn
    nx, ny = -dpy, dpx  # normála (kolmice na čáry)
    pixel_m = abs(wf.pixel_size_x)
    spacing_px = SPACING_GROUND_M / pixel_m
    if spacing_px < 2:
        return []
    cx, cy = W / 2.0, H / 2.0
    # rozsah posunů podél normály tak, aby pokryl všechny rohy
    corners = [(0, 0), (W, 0), (0, H), (W, H)]
    projs = [(x - cx) * nx + (y - cy) * ny for x, y in corners]
    lo, hi = min(projs), max(projs)
    segs = []
    k = math.floor(lo / spacing_px)
    while k * spacing_px <= hi:
        o = k * spacing_px
        bx, by = cx + o * nx, cy + o * ny
        clip = _clip_line(bx, by, dpx, dpy, W, H)
        if clip:
            segs.append(clip)
        k += 1
    return segs


def draw_north_lines(
    preview_png: Path,
    pgw: Path,
    declination_deg: float,
    color=BLUE,
    width_mm: float = 0.12,
) -> dict:
    """Nakreslí magnetické severky do preview PNG (in-place)."""
    img = Image.open(preview_png).convert("RGB")
    W, H = img.size
    wf = WorldFile.read(pgw)
    segs = _line_pixels(wf, W, H, declination_deg)
    if not segs:
        return {"north_lines": 0}
    draw = ImageDraw.Draw(img)
    w = max(1, int(round(width_mm * px_per_mm(10000))))
    for (x0, y0), (x1, y1) in segs:
        draw.line([(x0, y0), (x1, y1)], fill=tuple(color), width=w)
    img.save(preview_png)
    return {"north_lines": len(segs), "declination_deg": declination_deg}


def north_line_world_segments(wf: WorldFile, W: int, H: int, declination_deg: float):
    """Severky v world (S-JTSK) souřadnicích — pro .omap export."""
    out = []
    for (x0, y0), (x1, y1) in _line_pixels(wf, W, H, declination_deg):
        wx0 = wf.origin_x + x0 * wf.pixel_size_x
        wy0 = wf.origin_y + y0 * wf.pixel_size_y
        wx1 = wf.origin_x + x1 * wf.pixel_size_x
        wy1 = wf.origin_y + y1 * wf.pixel_size_y
        out.append([(wx0, wy0), (wx1, wy1)])
    return out
