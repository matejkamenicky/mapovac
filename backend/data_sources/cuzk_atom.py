"""Generický ATOM downloader pro ČÚZK LAZ datasety.

Vstupní bod: URL master ATOM feedu, např.:
- `https://atom.cuzk.gov.cz/DMPOK-SJTSK-LAZ/DMPOK-SJTSK-LAZ.xml`
  (DMP z obrazové korelace; ne klasifikováno — pro pullauta horší než LiDAR)
- `https://atom.cuzk.gov.cz/<DATASET>-SJTSK-LAZ/<DATASET>-SJTSK-LAZ.xml`
  pro jiné LAZ datasety v ATOM

Struktura:
  master feed → N entries (jedna SM5 dlaždice) → každá entry odkazuje na detail
  feed → ten obsahuje `<link rel="alternate" type="application/vnd.laszip">`
  → ZIP s `.laz` uvnitř.

Cache:
- master feed se cachuje jako .json index (bbox + detail URL per dlaždice)
- ZIPy a rozbalené LAZ se cachují v `cache/laz_atom/`
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

CACHE_ROOT = Path("cache/laz_atom")
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "georss": "http://www.georss.org/georss",
}
USER_AGENT = "mapovac/0.1 (orienteering map generator)"
TIMEOUT_INDEX = httpx.Timeout(60.0, connect=20.0)
TIMEOUT_DL = httpx.Timeout(1800.0, connect=20.0)


class AtomError(RuntimeError):
    pass


@dataclass(frozen=True)
class AtomTile:
    name: str            # např. "BENE09"
    title: str
    detail_url: str
    # BBox v WGS84 z georss:polygon (min/max přes vrcholy)
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


def _parse_polygon(text: str) -> tuple[float, float, float, float]:
    """`lat lon lat lon ...` → (lat_min, lat_max, lon_min, lon_max)."""
    nums = [float(x) for x in text.split()]
    lats = nums[0::2]
    lons = nums[1::2]
    return min(lats), max(lats), min(lons), max(lons)


def _short_name_from_id(id_text: str) -> str:
    """Z '...CUZK_DMPOK-SJTSK-LAZ_BENE09.xml' (nebo bez .xml) vykutej 'BENE09'."""
    if not id_text:
        return ""
    stem = id_text.rsplit("/", 1)[-1]
    if stem.endswith(".xml"):
        stem = stem[:-4]
    m = re.search(r"_([A-Z0-9]+)$", stem)
    return m.group(1) if m else stem


def _fetch(url: str, timeout: httpx.Timeout = TIMEOUT_INDEX) -> bytes:
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
        r = c.get(url)
        if r.status_code != 200:
            raise AtomError(f"HTTP {r.status_code} při stažení {url}")
        return r.content


class AtomIndex:
    """Cached spatial index nad master ATOM feedem."""

    def __init__(self, feed_url: str) -> None:
        self.feed_url = feed_url
        # Bezpečné jméno cache souboru z URL
        slug = re.sub(r"[^A-Za-z0-9._-]", "_", feed_url)[-120:]
        self._index_path = CACHE_ROOT / f"index_{slug}.json"

    def load(self, refresh: bool = False) -> list[AtomTile]:
        if not refresh and self._index_path.exists():
            data = json.loads(self._index_path.read_text("utf-8"))
            return [AtomTile(**t) for t in data]
        tiles = self._fetch_and_parse()
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(
            json.dumps([asdict(t) for t in tiles], ensure_ascii=False),
            encoding="utf-8",
        )
        return tiles

    def _fetch_and_parse(self) -> list[AtomTile]:
        xml = _fetch(self.feed_url)
        root = ET.fromstring(xml)
        tiles: list[AtomTile] = []
        for entry in root.findall("atom:entry", NS):
            id_el = entry.find("atom:id", NS)
            title_el = entry.find("atom:title", NS)
            poly_el = entry.find("georss:polygon", NS)
            alt_link = None
            for link in entry.findall("atom:link", NS):
                if link.attrib.get("rel") == "alternate":
                    alt_link = link.attrib.get("href")
                    break
            if id_el is None or poly_el is None or alt_link is None:
                continue
            lat_min, lat_max, lon_min, lon_max = _parse_polygon(poly_el.text or "")
            tiles.append(AtomTile(
                name=_short_name_from_id(id_el.text or ""),
                title=(title_el.text if title_el is not None else "") or "",
                detail_url=alt_link,
                lat_min=lat_min, lat_max=lat_max,
                lon_min=lon_min, lon_max=lon_max,
            ))
        if not tiles:
            raise AtomError(f"Master feed neobsahuje žádné dlaždice: {self.feed_url}")
        return tiles

    def tiles_for_bbox(self, bbox_wgs: tuple[float, float, float, float]) -> list[AtomTile]:
        w, s, e, n = bbox_wgs
        return [
            t for t in self.load()
            if not (t.lon_max < w or t.lon_min > e or t.lat_max < s or t.lat_min > n)
        ]


def _find_laz_url_in_detail(detail_xml: bytes) -> str:
    """V detail feedu najdi `<link rel="alternate" type="application/vnd.laszip">`."""
    root = ET.fromstring(detail_xml)
    for link in root.iter(f"{{{NS['atom']}}}link"):
        if (link.attrib.get("rel") == "alternate"
                and "laszip" in (link.attrib.get("type") or "")):
            return link.attrib["href"]
    # Fallback: jakýkoliv alternate link
    for entry in root.findall("atom:entry", NS):
        for link in entry.findall("atom:link", NS):
            if link.attrib.get("rel") == "alternate":
                return link.attrib["href"]
    raise AtomError("V detail feedu se nepodařilo najít LAZ download link.")


def download_tile_laz(tile: AtomTile) -> Path:
    """Stáhne a (pokud je ZIP) rozbalí LAZ pro dlaždici. Vrátí cestu k `.laz`.

    Cache klíč obsahuje hash detail_url — to zajistí, že DMR5G a DMP1G dlaždice
    se stejným SM5 jménem (např. PRAH60) se uloží do různých souborů.
    """
    import hashlib
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    feed_hash = hashlib.sha1(tile.detail_url.encode()).hexdigest()[:8]
    laz_path = CACHE_ROOT / f"{tile.name}_{feed_hash}.laz"
    if laz_path.exists() and laz_path.stat().st_size > 0:
        return laz_path

    detail = _fetch(tile.detail_url, timeout=TIMEOUT_INDEX)
    download_url = _find_laz_url_in_detail(detail)

    # Stáhni
    raw = _fetch(download_url, timeout=TIMEOUT_DL)
    tmp_path = CACHE_ROOT / f"{tile.name}.download"
    tmp_path.write_bytes(raw)

    # ZIP? — rozbal a najdi první .laz/.las uvnitř
    if zipfile.is_zipfile(tmp_path):
        with zipfile.ZipFile(tmp_path) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith((".laz", ".las"))]
            if not members:
                tmp_path.unlink(missing_ok=True)
                raise AtomError(f"ZIP {download_url} neobsahuje .laz/.las.")
            with zf.open(members[0]) as src, laz_path.open("wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
        tmp_path.unlink(missing_ok=True)
    else:
        # Buď přímý LAZ, nebo něco jiného — přejmenujeme
        tmp_path.rename(laz_path)
    return laz_path


# --- Convenience: instance používaná pipeline ---

DEFAULT_FEED_URL = os.environ.get(
    "MAPOVAC_LAZ_ATOM_URL",
    # DMR 5G = LiDAR terénní body (ground returns) po klasifikaci.
    # Dává správné vrstevnice; vegetační odstíny pullauta bez vrchních
    # vegetačních bodů nedělá. Pro CHM by bylo třeba zkombinovat s DMP.
    "https://atom.cuzk.gov.cz/DMR5G-SJTSK/DMR5G-SJTSK.xml",
)

# Alternativní feedy (ČÚZK ZABAGED Altimetry, S-JTSK varianty):
FEED_DMR5G = "https://atom.cuzk.gov.cz/DMR5G-SJTSK/DMR5G-SJTSK.xml"
"""DMR 5G — terén z LiDAR ground returns. Pro vrstevnice a srázy."""

FEED_DMP1G = "https://atom.cuzk.gov.cz/DMP1G-SJTSK/DMP1G-SJTSK.xml"
"""DMP 1G — povrch z LiDAR first returns. Spolu s DMR5G dává Canopy Height
Model → vegetační odstíny ISOM 405–410. Implementace v M5."""

FEED_DMPOK = "https://atom.cuzk.gov.cz/DMPOK-SJTSK-LAZ/DMPOK-SJTSK-LAZ.xml"
"""DMP OK — povrch z fotogrammetrické korelace. Méně přesný než DMP 1G."""


def default_index() -> AtomIndex:
    return AtomIndex(DEFAULT_FEED_URL)
