"""ZABAGED® (ČÚZK) — autoritativní vektorová data pro ČR z lokálního GPKG.

ZABAGED GPKG je jeden soubor pro celý stát (~6 GB ZIP, EPSG:5514). Stáhne se
jednou z ATOM feedu a rozbalí; cestu k .gpkg nastaví ENV `MAPOVAC_ZABAGED_GPKG`.
Tady z něj přes prostorový index (pyogrio bbox filtr) vytáhneme jen vrstvy a
prvky uvnitř výřezu a namapujeme na ISOM symboly (Feature jako z OSM).

ATOM (EPSG:5514): https://atom.cuzk.gov.cz/ZABAGED-GPKG/ZABAGED-GPKG.xml
Přímý ZIP: https://openzu.cuzk.gov.cz/opendata/ZABAGED-GPKG/epsg-5514/ZABAGED-5514-gpkg-*.zip
"""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from backend.vector.isom_mapping import (
    BUILDING,
    CULTIVATED,
    HIGHWAY_STYLES,
    MARSH,
    OPEN_LAND,
    POWER_LINE,
    RAILWAY,
    WATER_AREA,
    WATERWAY_STYLES,
)
from backend.vector.osm_features import Feature

def _resolve_gpkg() -> str:
    """Cesta k ZABAGED GPKG: ENV, jinak auto-detekce v cache/zabaged/*.gpkg."""
    env = os.environ.get("MAPOVAC_ZABAGED_GPKG", "")
    if env:
        return env
    cand = sorted(Path("cache/zabaged").glob("*.gpkg")) if Path("cache/zabaged").exists() else []
    return str(cand[0]) if cand else ""


ZABAGED_GPKG = _resolve_gpkg()

# Přímý ZIP s celostátním GPKG (EPSG:5514). Datum v názvu se mění — bere se
# nejnovější z ATOM feedu, tady default pro pohodlí.
ZABAGED_ATOM = "https://atom.cuzk.gov.cz/ZABAGED-GPKG/ZABAGED-GPKG.xml"
ZABAGED_ZIP_FALLBACK = (
    "https://openzu.cuzk.gov.cz/opendata/ZABAGED-GPKG/epsg-5514/"
    "ZABAGED-5514-gpkg-20260412.zip"
)

# (klíčová slova v názvu vrstvy, požadovaný typ geometrie, ISOM styl).
# Kontroluje se v pořadí; název vrstvy se normalizuje (lowercase, bez diakritiky).
_RULES: list[tuple[tuple[str, ...], str, object]] = [
    (("dalnic", "silnic"), "line", HIGHWAY_STYLES["primary"]),      # 502
    (("ulice",), "line", HIGHWAY_STYLES["residential"]),            # 503
    (("cesta", "polnicest", "lesnicest"), "line", HIGHWAY_STYLES["track"]),  # 504
    (("pesin", "stezk", "chodnik", "turist"), "line", HIGHWAY_STYLES["path"]),  # 505
    (("zeleznic", "draha", "vlecka", "kolej", "tramv"), "line", RAILWAY),  # 509
    (("vodnitok", "tok", "potok", "reka"), "line", WATERWAY_STYLES["stream"]),  # 304
    (("vodniplocha", "nadrz", "rybnik", "jezero", "vodninadr"), "area", WATER_AREA),  # 301
    (("bazin", "mocal", "raselin", "mokrad"), "area", MARSH),       # 308
    (("budova", "stavba"), "area", BUILDING),                       # 521
    (("ornapud", "chmelnic", "vinic", "ovocny", "sad", "zahrad"), "area", CULTIVATED),  # 412
    (("travni", "louka", "luka"), "area", OPEN_LAND),               # 401
    (("vedeni", "elektrickevedeni"), "line", POWER_LINE),           # 510
]


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return s.replace("_", "").replace(" ", "")


def _style_for_layer(layer: str):
    n = _norm(layer)
    for keys, kind, style in _RULES:
        if any(k in n for k in keys):
            return kind, style
    return None, None


def _geom_to_parts(geom) -> list[list[tuple[float, float]]]:
    t = geom.geom_type
    if t == "Polygon":
        return [list(geom.exterior.coords)]
    if t == "MultiPolygon":
        return [list(g.exterior.coords) for g in geom.geoms]
    if t == "LineString":
        return [list(geom.coords)]
    if t == "MultiLineString":
        return [list(g.coords) for g in geom.geoms]
    return []


def _kind_ok(geom_type: str, want: str) -> bool:
    if want == "line":
        return "Line" in geom_type
    return "Polygon" in geom_type


def zabaged_features(bbox_sjtsk: tuple[float, float, float, float]) -> list[Feature]:
    """Vrátí ISOM Feature z lokálního ZABAGED GPKG pro výřez. Prázdné při chybě."""
    gpkg = _resolve_gpkg()
    if not gpkg or not Path(gpkg).exists():
        return []
    try:
        import geopandas as gpd
        import pyogrio
    except Exception:
        return []

    try:
        layers = [row[0] for row in pyogrio.list_layers(gpkg)]
    except Exception:
        return []

    out: list[Feature] = []
    for layer in layers:
        kind, style = _style_for_layer(layer)
        if style is None:
            continue
        try:
            gdf = gpd.read_file(gpkg, layer=layer, bbox=bbox_sjtsk)
        except Exception:
            continue
        for geom in gdf.geometry:
            if geom is None or geom.is_empty or not _kind_ok(geom.geom_type, kind):
                continue
            parts = _geom_to_parts(geom)
            if parts:
                out.append(Feature(style=style, parts=parts,
                                   tags={"src": "zabaged", "layer": layer}))
    return out


def _latest_zip_url() -> str:
    """Najde nejnovější ZIP (EPSG:5514) z ATOM feedu, jinak fallback."""
    try:
        import re
        import httpx
        xml = httpx.get(ZABAGED_ATOM, timeout=30).text
        # dataset feed → konkrétní ZIP
        feed = re.search(r'href="([^"]*datasetFeeds[^"]*\.xml)"', xml)
        if feed:
            sub = httpx.get(feed.group(1), timeout=30).text
            m = re.search(r'href="([^"]*epsg-5514[^"]*\.zip)"', sub)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ZABAGED_ZIP_FALLBACK


def _expected_size(url: str) -> int | None:
    import httpx
    try:
        r = httpx.head(url, follow_redirects=True, timeout=30)
        cl = r.headers.get("Content-Length")
        return int(cl) if cl else None
    except Exception:
        return None


def _download_resumable(url: str, zip_path: Path, expected: int | None,
                        max_attempts: int = 200) -> int:
    """Stáhne ZIP s podporou navázání (HTTP Range) a opakování při přerušení."""
    import httpx
    # read timeout (stall → retry); celkový timeout vypnut
    timeout = httpx.Timeout(120.0, connect=30.0)
    for attempt in range(max_attempts):
        got = zip_path.stat().st_size if zip_path.exists() else 0
        if expected and got >= expected:
            return got
        headers = {"Range": f"bytes={got}-"} if got else {}
        try:
            with httpx.stream("GET", url, headers=headers, timeout=timeout,
                              follow_redirects=True) as r:
                # server ignoroval Range (200 místo 206) → začni od nuly
                mode = "ab"
                if got and r.status_code == 200:
                    got = 0
                    mode = "wb"
                if r.status_code not in (200, 206):
                    r.raise_for_status()
                with zip_path.open(mode) as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        got += len(chunk)
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError,
                httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            got = zip_path.stat().st_size if zip_path.exists() else 0
            pct = f" ({100*got/expected:.1f}%)" if expected else ""
            print(f"  …přerušeno na {got:,} B{pct}, pokus {attempt + 2}, navazuji…")
            continue
        if not expected or got >= expected:
            return got
    return zip_path.stat().st_size if zip_path.exists() else 0


def download_and_extract(dest_dir: str = "cache/zabaged") -> Path | None:
    """Stáhne celostátní ZABAGED GPKG ZIP (~6 GB) a rozbalí. Vrátí cestu k .gpkg.

    Stahování je resumovatelné — když se přeruší, spusť znovu a naváže.
    Spustit jednou: `python -m backend.data_sources.zabaged`. Poté nastav
    MAPOVAC_ZABAGED_GPKG na vrácenou cestu.
    """
    import zipfile

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    url = _latest_zip_url()
    zip_path = dest / url.rsplit("/", 1)[-1]
    expected = _expected_size(url)
    exp_txt = f" (~{expected/1e9:.1f} GB)" if expected else ""
    print(f"Stahuji {url}\n→ {zip_path}{exp_txt} — resumovatelné, vydrž…")
    got = _download_resumable(url, zip_path, expected)
    if expected and got < expected:
        print(f"\nNEúplné: {got:,}/{expected:,} B. Spusť příkaz znovu pro navázání.")
        return None
    print(f"Staženo {got:,} B. Rozbaluji…")
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(dest)
    except zipfile.BadZipFile:
        print("Archiv je poškozený/neúplný — smaž ho a stáhni znovu:")
        print(f"  rm {zip_path}")
        return None
    gpkgs = list(dest.rglob("*.gpkg"))
    if gpkgs:
        print(f"\nHotovo. Nastav:\n  export MAPOVAC_ZABAGED_GPKG={gpkgs[0]}")
        return gpkgs[0]
    print("Nenalezen .gpkg v archivu.")
    return None


def list_layers(path: str | None = None) -> list[str]:
    """Vypíše vrstvy GPKG (ladění mapování)."""
    import pyogrio
    p = path or ZABAGED_GPKG
    return [row[0] for row in pyogrio.list_layers(p)]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "layers":
        for ly in list_layers():
            print(ly)
    else:
        download_and_extract()
