"""Sloučení více LAZ dlaždic do jednoho souboru (paměťově úsporné).

Pullauta je třeba spustit nad CELOU oblastí najednou — zpracování po dlaždicích
vytváří na hranách artefakty a nekonzistentní vegetaci.

DMPOK je ale extrémně hustý (~25 b/m², 125 M bodů/dlaždice) → naivní načtení
všeho do paměti spadne na OOM. Proto čteme **po chuncích**, volitelně ořežeme na
bbox a podvzorkujeme na cílovou hustotu (pullauta víc než ~1–2 b/m² nepotřebuje).
"""
from __future__ import annotations

from pathlib import Path

import laspy
import numpy as np

CHUNK = 4_000_000


def merge_laz(
    paths: list[Path],
    out_path: Path,
    bbox_sjtsk: tuple[float, float, float, float] | None = None,
    target_density: float | None = None,
    max_points: int = 25_000_000,
) -> Path:
    """Sloučí LAZ (stejný CRS) do jednoho, volitelně ořez na bbox + podvzorkování.

    `bbox_sjtsk` — ponechá jen body uvnitř (S-JTSK).
    `target_density` — pokud hustota dlaždice je vyšší, náhodně podvzorkuje.
    """
    paths = [Path(p) for p in paths if p and Path(p).exists()]
    if not paths:
        raise ValueError("merge_laz: žádné vstupní LAZ")
    if len(paths) == 1 and bbox_sjtsk is None and target_density is None:
        return paths[0]

    rng = np.random.default_rng(42)
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    zs: list[np.ndarray] = []
    cls: list[np.ndarray] = []
    src_header = None

    for p in paths:
        with laspy.open(str(p)) as fh:
            hdr = fh.header
            if src_header is None:
                src_header = hdr
            frac = 1.0
            if target_density:
                area = (hdr.x_max - hdr.x_min) * (hdr.y_max - hdr.y_min)
                dens = (hdr.point_count / area) if area > 0 else 0.0
                if dens > target_density:
                    frac = target_density / dens
            for pts in fh.chunk_iterator(CHUNK):
                x = np.asarray(pts.x)
                y = np.asarray(pts.y)
                z = np.asarray(pts.z)
                c = np.asarray(pts.classification, dtype=np.uint8)
                m = np.ones(len(x), dtype=bool)
                if bbox_sjtsk is not None:
                    xmin, ymin, xmax, ymax = bbox_sjtsk
                    m &= (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)
                if frac < 1.0:
                    m &= rng.random(len(x)) < frac
                if m.any():
                    xs.append(x[m])
                    ys.append(y[m])
                    zs.append(z[m])
                    cls.append(c[m])

    if not xs:
        raise ValueError("merge_laz: po ořezu/podvzorkování nezbyly žádné body")

    x = np.concatenate(xs)
    y = np.concatenate(ys)
    z = np.concatenate(zs)
    classification = np.concatenate(cls)

    # Globální strop bodů — pojistka proti přetečení paměti pullauty.
    if len(x) > max_points:
        keep = rng.choice(len(x), size=max_points, replace=False)
        keep.sort()
        x, y, z, classification = x[keep], y[keep], z[keep], classification[keep]

    header = laspy.LasHeader(
        point_format=src_header.point_format, version=src_header.version
    )
    header.offsets = [float(x.min()), float(y.min()), float(z.min())]
    header.scales = [0.01, 0.01, 0.01]
    las_out = laspy.LasData(header)
    las_out.x = x
    las_out.y = y
    las_out.z = z
    las_out.classification = classification

    out_path.parent.mkdir(parents=True, exist_ok=True)
    las_out.write(str(out_path))
    return out_path
