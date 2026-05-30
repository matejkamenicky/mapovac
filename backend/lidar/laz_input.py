"""Získání LAZ souborů pro bbox.

Strategie (v pořadí pokusů):
1. **Lokální adresář** (`MAPOVAC_LAZ_DIR`) — uživatel manuálně stáhl LAZ z ČÚZK
   a uložil je do tohoto adresáře. Vybereme ty, jejichž BBox v hlavičce protíná
   požadovaný bbox.
2. **ATOM šablona** (`MAPOVAC_LAZ_URL_TEMPLATE`) — automatické stažení podle
   SM5 dlaždice (viz `cuzk_lidar.py`).

Pro M3 doporučujeme cestu (1) — stáhnout pár LAZ ručně z
https://atom.cuzk.cz/ → "Klasifikované laserové skenování" a otestovat pipeline.
Plnou automatizaci ATOM stahování necháváme na M3.5.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path

from backend.data_sources.cuzk_atom import (
    AtomError,
    AtomIndex,
    DEFAULT_FEED_URL,
    default_index,
)
from backend.data_sources.cuzk_atom import download_tile_laz as atom_download
from backend.data_sources.cuzk_lidar import (
    CuzkLidarNotConfigured,
    download_tile,
    tiles_for_bbox,
)
from backend.data_sources.projection import bbox_wgs_to_sjtsk


def laz_from_feed(feed_url: str, bbox_wgs: tuple[float, float, float, float]) -> list[Path]:
    """Stáhne LAZ pro bbox z konkrétního ATOM feedu (DMR5G, DMP1G, ...)."""
    idx = AtomIndex(feed_url)
    tiles = idx.tiles_for_bbox(bbox_wgs)
    out: list[Path] = []
    for t in tiles:
        try:
            out.append(atom_download(t))
        except Exception:
            pass
    return out

LAZ_DIR = Path(os.environ.get("MAPOVAC_LAZ_DIR", "cache/laz_user"))


class NoLazAvailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LazInfo:
    path: Path
    xmin: float
    ymin: float
    xmax: float
    ymax: float


def _read_las_header_bbox(path: Path) -> tuple[float, float, float, float] | None:
    """Načte min/max XY z LAS/LAZ hlavičky (Public Header Block).

    Funguje pro LAS 1.0–1.4 i LAZ (laszip umisťuje stejné HEADER bajty na začátek).
    Vrací None, pokud hlavička nesedí.
    """
    try:
        with path.open("rb") as f:
            head = f.read(8)
            if head[:4] != b"LASF":
                return None
            # LAS PHB má fixní offsety pro min/max XYZ od bytu 179 (LAS 1.2–1.4).
            # Pro robustnost čteme offset Header Size (byte 94, uint16) → poté skok.
            f.seek(94)
            header_size = struct.unpack("<H", f.read(2))[0]
            if header_size < 227:
                return None
            f.seek(179)
            maxx, minx, maxy, miny = struct.unpack("<dddd", f.read(32))
            return float(minx), float(miny), float(maxx), float(maxy)
    except OSError:
        return None


def _scan_local_laz() -> list[LazInfo]:
    if not LAZ_DIR.exists():
        return []
    out: list[LazInfo] = []
    for p in LAZ_DIR.glob("*.laz"):
        bbox = _read_las_header_bbox(p)
        if bbox:
            out.append(LazInfo(p, *bbox))
    for p in LAZ_DIR.glob("*.las"):
        bbox = _read_las_header_bbox(p)
        if bbox:
            out.append(LazInfo(p, *bbox))
    return out


def _intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def laz_for_bbox(bbox_wgs: tuple[float, float, float, float]) -> list[Path]:
    """Vrátí seznam LAZ souborů pokrývajících bbox.

    Vyhodí NoLazAvailable, když nic nenajde a stahování není nakonfigurováno.
    """
    bbox_sjtsk = bbox_wgs_to_sjtsk(bbox_wgs)

    # 1) lokální adresář
    local = _scan_local_laz()
    matched = [info.path for info in local if _intersects(
        (info.xmin, info.ymin, info.xmax, info.ymax), bbox_sjtsk
    )]
    if matched:
        return matched

    errors: list[str] = []

    # 2) ČÚZK ATOM (DMPOK / klasifikovaný LiDAR — záleží na ENV MAPOVAC_LAZ_ATOM_URL)
    try:
        index = default_index()
        atom_tiles = index.tiles_for_bbox(bbox_wgs)
        downloaded: list[Path] = []
        for t in atom_tiles:
            try:
                downloaded.append(atom_download(t))
            except Exception as exc:
                errors.append(f"{t.name}: {exc!r}")
        if downloaded:
            return downloaded
        if not atom_tiles:
            errors.append("ATOM index nevrátil žádnou dlaždici pro daný bbox.")
    except AtomError as exc:
        errors.append(f"ATOM: {exc}")

    # 3) Legacy URL-template downloader (zachováno pro custom feedy)
    legacy_tiles = tiles_for_bbox(bbox_wgs)
    legacy_dl: list[Path] = []
    for t in legacy_tiles:
        try:
            legacy_dl.append(download_tile(t))
        except CuzkLidarNotConfigured:
            break
        except Exception as exc:
            errors.append(f"legacy {t.name}: {exc!r}")
    if legacy_dl:
        return legacy_dl

    raise NoLazAvailable(
        "Nepodařilo se získat žádný LAZ pro výřez. Možnosti:\n"
        f"  • Stáhni LAZ ručně z https://atom.cuzk.gov.cz/ a ulož do {LAZ_DIR}/\n"
        "  • Nastav MAPOVAC_LAZ_ATOM_URL na master ATOM feed.\n"
        + ("Detaily:\n  " + "\n  ".join(errors) if errors else "")
    )
