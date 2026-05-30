"""Minimální čtečka DXF (jen to, co produkuje Karttapullautin).

Pullauta píše staré ASCII DXF s entitami POLYLINE/VERTEX/SEQEND a POINT.
Souřadnice jsou v S-JTSK (stejné jako vstupní LAZ). Nepotřebujeme ezdxf —
parsujeme přímo dvojice (group_code, value).
"""
from __future__ import annotations

from pathlib import Path


def _tokens(path: Path):
    """Generátor dvojic (code, value) z DXF."""
    lines = path.read_text(errors="ignore").splitlines()
    it = iter(lines)
    for code in it:
        val = next(it, None)
        if val is None:
            break
        yield code.strip(), val.strip()


def read_polylines(path: Path, layers: set[str] | None = None) -> list[list[tuple[float, float]]]:
    """Vrátí seznam polylinií (každá = list (x, y)) z POLYLINE/VERTEX i LWPOLYLINE.

    `layers` — pokud zadáno, ponechá jen entity v těchto vrstvách.
    """
    if not path or not path.exists():
        return []
    out: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] | None = None
    cur_layer = ""
    ent = ""
    x = y = None
    in_lwpoly = False

    def flush_vertex():
        nonlocal x, y
        if cur is not None and x is not None and y is not None:
            cur.append((x, y))
        x = y = None

    for code, val in _tokens(path):
        if code == "0":
            # konec předchozí entity
            if ent == "VERTEX":
                flush_vertex()
            if ent == "LWPOLYLINE" and cur is not None:
                if layers is None or cur_layer in layers:
                    if len(cur) >= 2:
                        out.append(cur)
                cur = None
                in_lwpoly = False
            if val == "POLYLINE":
                if cur and (layers is None or cur_layer in layers) and len(cur) >= 2:
                    out.append(cur)
                cur = []
            elif val == "LWPOLYLINE":
                cur = []
                in_lwpoly = True
            elif val == "SEQEND":
                if cur is not None and (layers is None or cur_layer in layers) and len(cur) >= 2:
                    out.append(cur)
                cur = None
            ent = val
            x = y = None
        elif code == "8":
            cur_layer = val
        elif code == "10":
            if in_lwpoly and cur is not None and x is not None and y is not None:
                cur.append((x, y))  # LWPOLYLINE: opakované 10/20 v jedné entitě
                x = y = None
            x = float(val)
        elif code == "20":
            y = float(val)
    # doběh
    if ent == "VERTEX":
        flush_vertex()
    if cur and (layers is None or cur_layer in layers) and len(cur) >= 2:
        out.append(cur)
    return out


def read_points(path: Path, layers: set[str] | None = None) -> list[tuple[float, float, str]]:
    """Vrátí POINT entity jako (x, y, layer)."""
    if not path or not path.exists():
        return []
    out: list[tuple[float, float, str]] = []
    ent = ""
    layer = ""
    x = y = None
    for code, val in _tokens(path):
        if code == "0":
            if ent == "POINT" and x is not None and y is not None:
                out.append((x, y, layer))
            ent = val
            x = y = None
        elif code == "8":
            layer = val
        elif code == "10":
            x = float(val)
        elif code == "20":
            y = float(val)
    if ent == "POINT" and x is not None and y is not None:
        out.append((x, y, layer))
    return out
