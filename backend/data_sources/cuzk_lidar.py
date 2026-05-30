"""Klasifikovaný point cloud ČÚZK (LAZ).

Distribuováno přes ATOM service po mapových listech SM5 (2,5 × 2 km, S-JTSK).
Potřebné pro Karttapullautin spike (M3) a pro detailní vegetaci / detekci bodových
prvků nad rámec toho, co dává DMP 1G − DMR 5G.

Implementace:
1. Z bboxu (WGS84) → bbox v S-JTSK.
2. Spočti pokrývající listy SM5 (anchor + spacing).
3. Pro každý list sestav URL z ATOM šablony (konfigurovatelné, ČÚZK může měnit).
4. Stáhni .laz do cache.

POZOR — ATOM URL šablona a anchor SM5 mřížky musí odpovídat aktuálnímu stavu ČÚZK
Geoportálu. Ověř na https://atom.cuzk.cz/. ENV proměnné níže umožní override bez
úpravy kódu.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from backend.data_sources.cache import cache_path
from backend.data_sources.projection import bbox_wgs_to_sjtsk

# Mapový list SM5 = 2500 m (E) × 2000 m (N), kladová síť kotvená v rohu
# souřadnicového systému S-JTSK. Konkrétní anchor a šablonu názvu listu
# ověř proti aktuální dokumentaci ČÚZK.
TILE_WIDTH = 2500.0
TILE_HEIGHT = 2000.0
# Praktický anchor: použijeme nejbližší násobky (no zero-based offset). Skutečné
# pojmenování listů (např. "11-32-14") k mapování bude doplněno v M3.
ANCHOR_E = 0.0
ANCHOR_N = 0.0

# URL šablona — placeholder, doplnit po ověření aktuální ATOM struktury ČÚZK.
LAZ_URL_TEMPLATE = os.environ.get(
    "MAPOVAC_LAZ_URL_TEMPLATE",
    # NEPLATNÁ defaultní URL — slouží jen pro signalizaci, že je třeba nastavit ENV.
    "",
)


class CuzkLidarNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class Sm5Tile:
    col: int  # index v ose E
    row: int  # index v ose N
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def name(self) -> str:
        # Pracovní označení; skutečné ČÚZK ID bude mapováno v M3.
        return f"sm5_{self.col}_{self.row}"


def tiles_for_bbox(bbox_wgs: tuple[float, float, float, float]) -> list[Sm5Tile]:
    xmin, ymin, xmax, ymax = bbox_wgs_to_sjtsk(bbox_wgs)
    col_min = int((xmin - ANCHOR_E) // TILE_WIDTH)
    col_max = int((xmax - ANCHOR_E) // TILE_WIDTH)
    row_min = int((ymin - ANCHOR_N) // TILE_HEIGHT)
    row_max = int((ymax - ANCHOR_N) // TILE_HEIGHT)
    tiles: list[Sm5Tile] = []
    for col in range(col_min, col_max + 1):
        for row in range(row_min, row_max + 1):
            x0 = ANCHOR_E + col * TILE_WIDTH
            y0 = ANCHOR_N + row * TILE_HEIGHT
            tiles.append(
                Sm5Tile(col, row, x0, y0, x0 + TILE_WIDTH, y0 + TILE_HEIGHT)
            )
    return tiles


def download_tile(tile: Sm5Tile) -> Path:
    if not LAZ_URL_TEMPLATE:
        raise CuzkLidarNotConfigured(
            "Nastav MAPOVAC_LAZ_URL_TEMPLATE — viz docstring. "
            "LiDAR LAZ stahování bude napojeno v M3 (Karttapullautin spike)."
        )
    out = cache_path("laz", {"name": tile.name}, ".laz")
    if out.exists() and out.stat().st_size > 0:
        return out
    url = LAZ_URL_TEMPLATE.format(name=tile.name, col=tile.col, row=tile.row)
    with httpx.Client(timeout=300.0) as client:
        r = client.get(url)
        r.raise_for_status()
    out.write_bytes(r.content)
    return out
