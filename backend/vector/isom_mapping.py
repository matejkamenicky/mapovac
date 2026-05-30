"""OSM tagy → ISOM 2017 symbol + render style.

ISOM 2017 šířky symbolů jsou specifikované v mm na mapě. Pro raster overlay
převedeme mm na pixely podle DPI (pullauta = 600 DPI = 23.622 px/mm) a měřítka.

Styl je zjednodušený rendering pro raster overlay; přesné ISOM symboly (např.
přerušování černé linie u silnic, lemování) přijdou v M6.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DPI = 600  # pullauta default
PX_PER_MM_AT_NATIVE = DPI / 25.4  # ~23.622

Kind = Literal["line", "polygon", "point"]


@dataclass(frozen=True)
class IsomStyle:
    isom_code: str          # např. "502"
    kind: Kind              # "line" | "polygon" | "point"
    color: tuple[int, int, int, int]  # RGBA
    width_mm: float = 0.0   # tloušťka linie (mm na mapě), 0 pro výplň
    dash_mm: tuple[float, float] | None = None  # (čárka, mezera) v mm
    outline: tuple[int, int, int, int] | None = None  # barva obrysu pro polygon
    outline_width_mm: float = 0.0
    z: int = 0              # pořadí kreslení (nižší dřív)
    # ISOM "casing" — dvojitá linie u širokých silnic (502/503): pod barvou
    # `color` (výplň vozovky) se nejdřív nakreslí širší linie `casing_color`
    # (černé lemovky). Výsledek = dvě paralelní černé linie s výplní uvnitř.
    casing_color: tuple[int, int, int, int] | None = None
    casing_width_mm: float = 0.0
    # ISOM značky s kolmými „zoubky" podél linie — plot (524), zeď. Plná linie
    # `color` + kolmé čárky délky `tick_len_mm` po `tick_spacing_mm`.
    tick_spacing_mm: float = 0.0
    tick_len_mm: float = 0.0


# Barvy podle ISOM 2017-2 (RGB z oficiálního OO Mapper symbol setu).
BLACK = (0, 0, 0, 255)
BROWN = (209, 92, 0, 255)        # Brown 100%
BLUE = (0, 200, 235, 255)        # Blue 100% (≈ cyan)
BLUE_DARK = (0, 160, 200, 255)
GREEN_DARK = (0, 158, 80, 255)
YELLOW = (255, 220, 95, 255)
YELLOW_LIGHT = (255, 235, 160, 255)
WHITE = (255, 255, 255, 255)
PURPLE = (170, 0, 170, 255)
GRAY = (180, 180, 180, 255)


def _line(isom: str, color, width_mm, dash=None, z=10) -> IsomStyle:
    return IsomStyle(isom_code=isom, kind="line", color=color,
                     width_mm=width_mm, dash_mm=dash, z=z)


def _poly(isom: str, color, outline=None, outline_w=0.0, z=5) -> IsomStyle:
    return IsomStyle(isom_code=isom, kind="polygon", color=color,
                     outline=outline, outline_width_mm=outline_w, z=z)


# Výplň vozovky u širokých silnic (502/503). ISOM 2017 nechává plochu silnice
# hnědou (50% rastr) mezi černými lemovkami; pro raster overlay použijeme
# světle hnědou neprůhlednou výplň.
ROAD_FILL = (240, 209, 170, 255)


def _road(isom: str, casing_total_mm: float, fill_mm: float, z=40) -> IsomStyle:
    """Široká silnice s lemovkou: černá `casing_total_mm` + výplň `fill_mm`.

    Vykreslí se nejdřív černá linie šířky `casing_total_mm`, pak přes ni výplň
    `fill_mm` → vzniknou dvě paralelní černé linie tloušťky
    (casing_total_mm − fill_mm)/2.
    """
    return IsomStyle(isom_code=isom, kind="line", color=ROAD_FILL,
                     width_mm=fill_mm, casing_color=BLACK,
                     casing_width_mm=casing_total_mm, z=z)


# --- HIGHWAY (cesty a silnice) ---
# ISOM 2017 tloušťky linií jsou udané v mm na mapě (základ 1:15 000). Hodnoty
# níže jsou laděné pro nativní raster pullauta (1:10 000); pro jiné měřítko se
# přeškálují přes `symbol_scale_factor()` v overlay.py.
HIGHWAY_STYLES: dict[str, IsomStyle] = {
    # ISOM 502 — Wide road: černá lemovka 0.65mm + hnědá výplň 0.35mm →
    # dvě paralelní černé linie ~0.15mm.
    "motorway":   _road("502", 0.90, 0.50, z=40),
    "trunk":      _road("502", 0.80, 0.44, z=40),
    "primary":    _road("502", 0.72, 0.40, z=40),
    "secondary":  _road("502", 0.64, 0.34, z=39),
    # ISOM 503 — Road: plná černá linie 0.525 mm (@1:10 000)
    "tertiary":   _line("503", BLACK, 0.525, z=39),
    "unclassified": _line("503", BLACK, 0.525, z=38),
    "residential":  _line("503", BLACK, 0.525, z=38),
    "service":      _line("503", BLACK, 0.45, z=37),
    # ISOM 504 — Vehicle track: čárkovaná 0.525 mm, čárka 4.5 / mezera 0.375
    "track":        _line("504", BLACK, 0.525, dash=(4.5, 0.375), z=36),
    # ISOM 505 — Footpath: čárkovaná 0.375 mm, čárka 3.0 / mezera 0.375
    "path":       _line("505", BLACK, 0.375, dash=(3.0, 0.375), z=35),
    "footway":    _line("505", BLACK, 0.375, dash=(3.0, 0.375), z=35),
    "bridleway":  _line("505", BLACK, 0.375, dash=(3.0, 0.375), z=35),
    "cycleway":   _line("505", BLACK, 0.375, dash=(3.0, 0.375), z=35),
    "steps":      _line("505", BLACK, 0.375, dash=(0.6, 0.375), z=35),
    # ISOM 506 — Small footpath: čárkovaná 0.27 mm, čárka 1.5 / mezera 0.375
    "trail":      _line("506", BLACK, 0.27, dash=(1.5, 0.375), z=34),
    "footpath":   _line("506", BLACK, 0.27, dash=(1.5, 0.375), z=34),
}

# --- WATERWAY (vodní toky) ---
WATERWAY_STYLES: dict[str, IsomStyle] = {
    # ISOM 304 — Crossable watercourse (potok)
    "stream":   _line("304", BLUE, 0.25, z=20),
    "ditch":    _line("305", BLUE, 0.18, dash=(0.8, 0.4), z=19),
    "drain":    _line("305", BLUE, 0.18, dash=(0.8, 0.4), z=19),
    # ISOM 301 / 302 — Uncrossable water body / Crossable water
    "river":    _line("301", BLUE, 0.5, z=21),
    "canal":    _line("301", BLUE, 0.5, z=21),
}

# --- POLYGONOVÉ FEATURES ---
WATER_AREA = _poly("301", BLUE, outline=BLUE_DARK, outline_w=0.18, z=22)
BUILDING = _poly("521", BLACK, outline=BLACK, outline_w=0.0, z=45)

# ISOM 405 Forest — semi-transparent bílá. Pullauta dělá podklad žlutý
# (otevřený terén default). Les by měl být ISOM bílý, ale plná bílá by
# zakryla vrstevnice. Alpha ~140 dělá žlutou → krémovou, vrstevnice zůstávají
# viditelné.
FOREST_WHITE = (255, 255, 255, 140)

LANDUSE_STYLES: dict[str, IsomStyle] = {
    "meadow":    _poly("401", YELLOW, z=3),       # otevřená plocha
    "grass":     _poly("401", YELLOW, z=3),
    "farmland":  _poly("412", YELLOW_LIGHT, z=2), # obhospodařovaná půda
    "orchard":   _poly("412", YELLOW_LIGHT, z=2),
    "vineyard":  _poly("412", YELLOW_LIGHT, z=2),
    "forest":    _poly("405", FOREST_WHITE, z=2),  # semi-transparent
    "scrub":     _poly("406", (180, 220, 150, 255), z=4),
}

NATURAL_STYLES: dict[str, IsomStyle] = {
    "wood":      _poly("405", FOREST_WHITE, z=2),
    "scrub":     _poly("406", (180, 220, 150, 255), z=4),
    "heath":     _poly("403", (255, 245, 180, 255), z=3),
    "water":     WATER_AREA,
    "wetland":   _poly("307", (180, 220, 240, 255), z=15),
    "bare_rock": _poly("213", GRAY, z=18),
}

# --- LINIE NEKLASIFIKOVANÉ JINAK ---
# ISOM 516 Fence — plná tenká černá linie s krátkými kolmými zoubky (na jednu
# stranu). Tím se vizuálně odliší od přerušované pěšiny (505/506).
FENCE = IsomStyle(
    isom_code="516", kind="line", color=BLACK, width_mm=0.14,
    tick_spacing_mm=1.5, tick_len_mm=0.5, z=50,
)
POWER_LINE = _line("510", BLACK, 0.21, z=48)                      # ISOM 510 power line


def _highway_style(tags: dict) -> IsomStyle | None:
    """Klasifikace cesty s ohledem na `tracktype` (kvalita/šířka).

    OSM `highway=track` pokrývá od sjízdné lesní cesty po sotva znatelnou
    stopu. ISOM to rozlišuje šířkou symbolu, proto použijeme `tracktype`:
      grade1/2 → 504 vozová cesta (širší čárkovaná, sjízdná)
      grade3/4 → 505 pěšina (užší)
      grade5   → 506 málo zřetelná pěšinka (nejjemnější)
      bez tracktype → 505 (konzervativně užší)
    """
    hw = tags["highway"]
    if hw == "track":
        tt = tags.get("tracktype")
        if tt in ("grade1", "grade2"):
            return HIGHWAY_STYLES["track"]      # 504
        if tt == "grade5":
            return HIGHWAY_STYLES["trail"]      # 506
        # grade3, grade4, nebo neuvedeno → 505 pěšina
        return HIGHWAY_STYLES["path"]
    return HIGHWAY_STYLES.get(hw)


def style_for(tags: dict) -> IsomStyle | None:
    """Pro OSM tagy vrátí ISOM styl, nebo None pokud feature ignorujeme."""
    if "building" in tags:
        return BUILDING
    if tags.get("natural") == "water" or tags.get("water"):
        return WATER_AREA
    if "waterway" in tags:
        return WATERWAY_STYLES.get(tags["waterway"])
    if "highway" in tags:
        return _highway_style(tags)
    # Pozn.: OSM landuse (les/pole/louka) a natural vegetace (wood/scrub/heath)
    # ZÁMĚRNĚ ignorujeme — les/otevřeno se odvozuje z LiDAR (CHM zápoj), který
    # má organické okraje. Hrubé katastrální OSM polygony dělaly ostré rovné
    # řezy. Z `natural` ponecháme jen ne-vegetační prvky (mokřad, holá skála).
    if tags.get("natural") in ("wetland", "bare_rock"):
        return NATURAL_STYLES.get(tags["natural"])
    if tags.get("barrier") in ("fence", "wall", "hedge"):
        return FENCE
    if tags.get("power") in ("line", "minor_line"):
        return POWER_LINE
    return None


def px_per_mm(target_scale: int) -> float:
    """Pixelů na mm na výstupním rasteru.

    Pullauta píše 600 DPI v měřítku 1:10 000. Když crop přeškálujeme na jiné
    měřítko (např. 1:7 500), 1 mm na mapě = stejný počet pixelů, protože
    `crop.py` mění velikost pixelu úměrně.
    """
    _ = target_scale
    return PX_PER_MM_AT_NATIVE


NATIVE_SCALE = 10000


def symbol_scale_factor(target_scale: int) -> float:
    """Zvětšení ISOM symbolů podle měřítka.

    ISOM 2017 definuje rozměry pro 1:15 000; mapy ve větším měřítku se zvětšují
    (1:10 000 → 150 %, 1:7 500 → 200 %). Naše uložené `width_mm`/`dash_mm` jsou
    laděné pro nativní raster pullauta (1:10 000), takže faktor počítáme
    relativně k němu: 1:10 000 → 1.0, 1:7 500 → 1.333.

    Tím zůstává 1:10 000 výstup beze změny a 1:7 500 dostane úměrně silnější
    linie a větší tečky (vzhledem k tomu, že raster má při 1:7 500 více pixelů
    na metr terénu).
    """
    if target_scale <= 0:
        return 1.0
    return NATIVE_SCALE / target_scale
