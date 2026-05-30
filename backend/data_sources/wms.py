"""Obecný WMS GetMap klient (EPSG:5514) pro ortofoto, katastr atd.

Vrací staženou dlaždici jako PNG + world file (.pgw) v S-JTSK. Vše best-effort:
při chybě/HTML odpovědi vrací None (nikdy nevyhodí, aby nerozbil pipeline).
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx

TIMEOUT = httpx.Timeout(60.0, connect=15.0)
UA = {"User-Agent": "mapovac/0.1 (orienteering map generator)"}


def get_map(
    base_url: str,
    layers: str,
    bbox_sjtsk: tuple[float, float, float, float],
    out_png: Path,
    width: int,
    height: int,
    *,
    version: str = "1.3.0",
    styles: str = "",
    fmt: str = "image/png",
    crs: str = "EPSG:5514",
    extra: dict | None = None,
) -> Path | None:
    """Stáhne WMS GetMap dlaždici. Vrátí cestu k PNG (+ .pgw) nebo None."""
    xmin, ymin, xmax, ymax = bbox_sjtsk
    # WMS 1.3.0 řadí osy podle CRS; pro EPSG:5514 (X=easting, Y=northing) je
    # pořadí x,y. 1.1.1 vždy minx,miny,maxx,maxy. Použijeme parametr dle verze.
    params = {
        "SERVICE": "WMS",
        "REQUEST": "GetMap",
        "VERSION": version,
        "LAYERS": layers,
        "STYLES": styles,
        "FORMAT": fmt,
        "TRANSPARENT": "FALSE",
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "BBOX": f"{xmin},{ymin},{xmax},{ymax}",
    }
    if version == "1.3.0":
        params["CRS"] = crs
    else:
        params["SRS"] = crs
    if extra:
        params.update(extra)

    try:
        r = httpx.get(base_url, params=params, headers=UA, timeout=TIMEOUT)
        ct = r.headers.get("content-type", "")
        if r.status_code != 200 or "image" not in ct:
            return None
        out_png.parent.mkdir(parents=True, exist_ok=True)
        out_png.write_bytes(r.content)
    except Exception:
        return None

    # world file: pixel size + origin (střed levého horního pixelu)
    psx = (xmax - xmin) / width
    psy = (ymax - ymin) / height
    out_png.with_suffix(".pgw").write_text(
        f"{psx}\n0.0\n0.0\n{-psy}\n{xmin + psx / 2}\n{ymax - psy / 2}\n",
        encoding="utf-8",
    )
    return out_png


# Endpointy (override přes ENV). Default = veřejné ČÚZK ArcGIS WMS.
ORTOFOTO_WMS = os.environ.get(
    "MAPOVAC_ORTOFOTO_WMS",
    "https://ags.cuzk.gov.cz/arcgis1/services/ORTOFOTO/MapServer/WMSServer",
)
ORTOFOTO_LAYER = os.environ.get("MAPOVAC_ORTOFOTO_LAYER", "0")
# CIR (color-infrared) — pokud dostupné, umožní pravé NDVI.
ORTOFOTO_CIR_WMS = os.environ.get("MAPOVAC_ORTOFOTO_CIR_WMS", "")
ORTOFOTO_CIR_LAYER = os.environ.get("MAPOVAC_ORTOFOTO_CIR_LAYER", "0")
