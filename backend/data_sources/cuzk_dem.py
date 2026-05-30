"""Klient pro ČÚZK rastrové výškové modely přes WCS.

- DMR 5G — digitální model reliéfu (terén, GRID 1 m, EPSG:5514)
- DMP 1G — digitální model povrchu (povrch včetně vegetace a budov, GRID 1 m)

Konfigurace URL: ČÚZK občas mění endpointy, ověř aktuální URL na
https://geoportal.cuzk.cz/ (Služby / WCS) a případně přepiš v ENV.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx

from backend.data_sources.cache import cache_path
from backend.data_sources.projection import bbox_wgs_to_sjtsk

# INSPIRE Nadmořská výška WCS (DMR) — nahradilo starší per-dataset endpointy.
# DMR a DMP teď sdílí jeden service; rozlišujeme přes CoverageID.
DMR5G_WCS = os.environ.get(
    "MAPOVAC_DMR5G_WCS",
    "https://ags.cuzk.gov.cz/arcgis2/services/INSPIRE_Nadmorska_vyska/ImageServer/WCSServer",
)
DMP1G_WCS = os.environ.get(
    "MAPOVAC_DMP1G_WCS",
    "https://ags.cuzk.gov.cz/arcgis2/services/INSPIRE_Nadmorska_vyska/ImageServer/WCSServer",
)
COVERAGE_ID = os.environ.get("MAPOVAC_DMR_COVERAGE", "1")

TIMEOUT = httpx.Timeout(120.0, connect=20.0)


class CuzkDemError(RuntimeError):
    pass


def _fetch_wcs_geotiff(
    wcs_url: str, bbox_sjtsk: tuple[float, float, float, float], resolution: float = 1.0
) -> bytes:
    xmin, ymin, xmax, ymax = bbox_sjtsk
    width = max(1, int(round((xmax - xmin) / resolution)))
    height = max(1, int(round((ymax - ymin) / resolution)))
    params = {
        "SERVICE": "WCS",
        "VERSION": "2.0.1",
        "REQUEST": "GetCoverage",
        "COVERAGEID": COVERAGE_ID,
        "FORMAT": "image/tiff",
        "SUBSET": [f"E({xmin},{xmax})", f"N({ymin},{ymax})"],
        "SUBSETTINGCRS": "http://www.opengis.net/def/crs/EPSG/0/5514",
        "OUTPUTCRS": "http://www.opengis.net/def/crs/EPSG/0/5514",
        "SCALESIZE": f"E({width}),N({height})",
    }
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.get(wcs_url, params=params)
    if r.status_code != 200 or not r.content.startswith(b"II") and not r.content.startswith(b"MM"):
        raise CuzkDemError(
            f"WCS GetCoverage selhalo ({r.status_code}). URL={r.request.url}\n"
            f"Body[:200]={r.text[:200]!r}"
        )
    return r.content


def fetch_dem(
    bbox_wgs: tuple[float, float, float, float],
    surface: bool = False,
    resolution: float = 1.0,
) -> Path:
    """Stáhne DMR 5G (surface=False) nebo DMP 1G (surface=True) pro bbox v WGS84.

    Vrací cestu k GeoTIFF v cache (v S-JTSK).
    """
    bbox_sjtsk = bbox_wgs_to_sjtsk(bbox_wgs)
    wcs_url = DMP1G_WCS if surface else DMR5G_WCS
    params = {"bbox_sjtsk": bbox_sjtsk, "resolution": resolution, "url": wcs_url}
    source = "dmp1g" if surface else "dmr5g"
    out = cache_path(source, params, ".tif")
    if out.exists() and out.stat().st_size > 0:
        return out
    data = _fetch_wcs_geotiff(wcs_url, bbox_sjtsk, resolution)
    out.write_bytes(data)
    return out
