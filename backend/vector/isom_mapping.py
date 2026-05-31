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
    # Skupinové čárkování (ISOM 507 dvojitá čárka): N čárek `dash_mm[0]` po
    # `dash_mm[1]`, mezi skupinami větší mezera `dash_group_gap_mm`.
    dash_group: int = 1
    dash_group_gap_mm: float = 0.0
    # Tečkovaná linie (ISOM 416 distinct vegetation boundary) — plné kruhy
    # `dot_radius_mm` po `dot_spacing_mm` podél linie.
    dot_line: bool = False
    dot_radius_mm: float = 0.0
    dot_spacing_mm: float = 0.0
    outline: tuple[int, int, int, int] | None = None  # barva obrysu pro polygon
    outline_width_mm: float = 0.0
    z: int = 0              # pořadí kreslení (nižší dřív)
    # ISOM "casing" — dvojitá linie u širokých silnic (502/503): pod barvou
    # `color` (výplň vozovky) se nejdřív nakreslí širší linie `casing_color`
    # (černé lemovky). Výsledek = dvě paralelní černé linie s výplní uvnitř.
    casing_color: tuple[int, int, int, int] | None = None
    casing_width_mm: float = 0.0
    # ISOM značky s kolmými „zoubky" podél linie — plot 516, vedení 510. Plná
    # linie `color` + kolmé čárky délky `tick_len_mm` po `tick_spacing_mm`.
    # `tick_both_sides` = příčka na OBĚ strany (vedení 510 = pylon), jinak na
    # jednu stranu (plot 516). `tick_width_mm` 0 → použije se `width_mm`.
    tick_spacing_mm: float = 0.0
    tick_len_mm: float = 0.0
    tick_both_sides: bool = False
    tick_width_mm: float = 0.0
    tick_angle_deg: float = 90.0   # úhel zoubku od směru linie (plot 516 = 60°)
    # Bodový vzor přes výplň plochy — ISOM 412 pole (černé tečky v mřížce).
    pattern: str | None = None          # "dots" | None
    pattern_spacing_mm: float = 0.0
    pattern_color: tuple[int, int, int, int] | None = None
    # ISOM 509 železnice — plná černá linie s bílými příčnými pražci.
    railway: bool = False
    tie_spacing_mm: float = 0.0
    tie_len_mm: float = 0.0
    # Bodové symboly (strom 418, balvan 206): kruh — plný (fill) nebo obrys (ring).
    point_radius_mm: float = 0.0
    point_outline_color: tuple[int, int, int, int] | None = None
    point_outline_mm: float = 0.0


# Barvy podle ISOM 2017-2 (RGB z oficiálního OO Mapper symbol setu).
BLACK = (0, 0, 0, 255)
BROWN = (209, 92, 0, 255)        # Brown 100%
BLUE = (0, 200, 235, 255)        # Blue 100% (≈ cyan)
BLUE_DARK = (0, 160, 200, 255)
BLUE_50 = (128, 228, 245, 255)   # Blue 50% (ISOM 302 brodelná voda)
GREEN_DARK = (0, 158, 80, 255)
YELLOW = (255, 190, 55, 255)        # Yellow 100% (ISOM 401 open land)
YELLOW_50 = (255, 220, 154, 255)    # Yellow 50% (ISOM 403 rough open land)
YELLOW_LIGHT = (255, 235, 160, 255)
WHITE = (255, 255, 255, 255)
PURPLE = (170, 0, 170, 255)
GRAY = (180, 180, 180, 255)
# ISOM 520 „zakázaná oblast" — Yellow 100 % + Green 50 % (olivová).
# RGB z oficiálního OO Mapper setu (barva „Yellow 100%/Green 50%").
OLIVE_520 = (158, 186, 29, 255)


def _line(isom: str, color, width_mm, dash=None, z=10) -> IsomStyle:
    return IsomStyle(isom_code=isom, kind="line", color=color,
                     width_mm=width_mm, dash_mm=dash, z=z)


def _poly(isom: str, color, outline=None, outline_w=0.0, z=5) -> IsomStyle:
    return IsomStyle(isom_code=isom, kind="polygon", color=color,
                     outline=outline, outline_width_mm=outline_w, z=z)


# Výplň vozovky u širokých silnic (502). ISOM 2017-2 = „Upper brown 50 %"
# (hnědý rastr) mezi černými lemovkami.
ROAD_FILL = (232, 167, 116, 255)


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
    # ISOM 502 Wide road — hnědá výplň + černé lemovky. Zpevněné silnice.
    "motorway":   _road("502", 0.95, 0.55, z=40),
    "trunk":      _road("502", 0.85, 0.50, z=40),
    "primary":    _road("502", 0.72, 0.45, z=40),
    "secondary":  _road("502", 0.66, 0.45, z=39),
    "tertiary":   _road("502", 0.62, 0.42, z=39),
    "unclassified": _road("502", 0.60, 0.42, z=38),
    # ISOM 503 — Road: plná černá linie 0.35 mm (menší zpevněné komunikace,
    # údržovaná silnice < 5 m sjízdná za každého počasí; spec 2024).
    "residential":  _line("503", BLACK, 0.35, z=38),
    "living_street": _line("503", BLACK, 0.35, z=38),
    "service":      _line("503", BLACK, 0.35, z=37),
    # ISOM 504 — Vehicle track: čárkovaná 0.35 mm, čárka 3.0 / mezera 0.25 (spec 2024)
    "track":        _line("504", BLACK, 0.35, dash=(3.0, 0.25), z=36),
    # Závodní/motokrosová dráha (highway=raceway) = vozová cesta → 504
    "raceway":      _line("504", BLACK, 0.35, dash=(3.0, 0.25), z=36),
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

# ISOM 507 — Less distinct small footpath: méně zřetelná pěšinka / přibližovací
# linka. Černá DVOJITÁ čárka 0.18 mm: čárka 0.8 / vnitřní mezera 0.25 (skupina
# po 2) / mezera mezi skupinami 1.0 mm.
LESS_DISTINCT_PATH = IsomStyle(
    isom_code="507", kind="line", color=BLACK, width_mm=0.18,
    dash_mm=(0.8, 0.25), dash_group=2, dash_group_gap_mm=1.0, z=34,
)

# ISOM 416 Distinct vegetation boundary — ZELENÁ ČÁRKOVANÁ varianta (tmavě
# zelená, šířka 0.14 mm, čárka 0.3 / mezera 0.2 mm). Jen pro zřetelné hranice
# porostů (experimentální).
VEG_BOUNDARY_416 = IsomStyle(
    isom_code="416", kind="line", color=GREEN_DARK, width_mm=0.14,
    dash_mm=(0.3, 0.2), z=33,
)

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
# ISOM 301 Uncrossable body of water — plná modrá (100 %) + ČERNÁ břehová
# linie 0.12 mm (zdůrazňuje, že je nepřekonatelná / nebezpečná).
WATER_AREA = _poly("301", BLUE, outline=BLACK, outline_w=0.12, z=22)
# ISOM 302 Shallow body of water — brodelná: modrá 50 % + modrý obrys 0.10 mm
# (bez černé břehové linie — odlišuje ji od nepřekonatelné 301).
SHALLOW_WATER = _poly("302", BLUE_50, outline=BLUE, outline_w=0.10, z=22)
BUILDING = _poly("521", BLACK, outline=BLACK, outline_w=0.0, z=45)
# ISOM 501 Paved area — zpevněná plocha (asfalt, dlažba, beton, zpevněný štěrk):
# hnědá 50 % výplň + tenká černá linie 0.1 mm tam, kde je zřetelná hranice.
PAVED_AREA = _poly("501", ROAD_FILL, outline=BLACK, outline_w=0.1, z=6)

# ISOM 308 Marsh — vodorovné modré čárky (0.15 mm, rozteč 0.45 mm), bez výplně.
MARSH = IsomStyle(
    isom_code="308", kind="polygon", color=BLUE, z=15,
    pattern="hlines", pattern_spacing_mm=0.45, pattern_color=BLUE,
)

# ISOM 405 Forest — semi-transparent bílá. Pullauta dělá podklad žlutý
# (otevřený terén default). Les by měl být ISOM bílý, ale plná bílá by
# zakryla vrstevnice. Alpha ~140 dělá žlutou → krémovou, vrstevnice zůstávají
# viditelné.
FOREST_WHITE = (255, 255, 255, 140)

# ISOM 401 louka, 403 paseka (rough open), 412 pole (cultivated, žlutá + tečky).
OPEN_LAND = _poly("401", YELLOW, z=3)
ROUGH_OPEN = _poly("403", YELLOW_50, z=3)
CULTIVATED = IsomStyle(
    isom_code="412", kind="polygon", color=YELLOW, z=3,
    pattern="dots", pattern_spacing_mm=1.2, pattern_color=BLACK,
)

# ISOM 520 Area that shall not be entered — olivová výplň (Yellow 100 % +
# Green 50 %) + černý obrys 0.18 mm (kde je hranice zřetelná). Výplň řeší
# rastr (zachová vrstevnice), overlay/.omap kreslí jen obrys.
OUT_OF_BOUNDS = _poly("520", OLIVE_520, outline=BLACK, outline_w=0.18, z=4)

LANDUSE_STYLES: dict[str, IsomStyle] = {
    # Průmyslové / zastavěné / soukromé areály → 520 (zákaz vstupu).
    "industrial": OUT_OF_BOUNDS,
    "commercial": OUT_OF_BOUNDS,
    "retail":     OUT_OF_BOUNDS,
    "garages":    OUT_OF_BOUNDS,
    "construction": OUT_OF_BOUNDS,
    "landfill":   OUT_OF_BOUNDS,
    "quarry":     OUT_OF_BOUNDS,
    "railway":    OUT_OF_BOUNDS,
    "meadow":    OPEN_LAND,
    "grass":     OPEN_LAND,
    "recreation_ground": OPEN_LAND,
    "village_green": OPEN_LAND,
    "farmland":  CULTIVATED,
    "orchard":   CULTIVATED,
    "vineyard":  CULTIVATED,
    "farmyard":  CULTIVATED,
    "greenhouse_horticulture": CULTIVATED,
    "allotments": CULTIVATED,
}

NATURAL_STYLES: dict[str, IsomStyle] = {
    "wood":      _poly("405", FOREST_WHITE, z=2),
    "scrub":     _poly("406", (180, 220, 150, 255), z=4),
    "heath":     _poly("403", (255, 245, 180, 255), z=3),
    "water":     WATER_AREA,
    "wetland":   MARSH,
    "bare_rock": _poly("213", GRAY, z=18),
}

# --- LINIE NEKLASIFIKOVANÉ JINAK ---
# ISOM 516 Fence (aktualizace 2024) — plná černá linie 0.14 mm se zoubky na
# JEDNU stranu pod úhlem 60°, délka 0.4 mm, šířka 0.14 mm, rozteč 2.0 mm (CC).
FENCE = IsomStyle(
    isom_code="516", kind="line", color=BLACK, width_mm=0.14,
    tick_spacing_mm=2.0, tick_len_mm=0.4, tick_width_mm=0.14,
    tick_angle_deg=60.0, z=50,
)
# ISOM 510 Power line — plná černá linie 0.21 mm s příčkami na OBĚ strany
# (pylon), poloviční délka 0.555 mm (celkem 1.11 mm), rozteč 6.0 mm.
POWER_LINE = IsomStyle(
    isom_code="510", kind="line", color=BLACK, width_mm=0.21,
    tick_spacing_mm=6.0, tick_len_mm=0.555, tick_both_sides=True, z=48,
)

# ISOM 509 Railway — kombinovaný: bílý podklad + černé podélné čárky přes něj
# (šířka 0.525 mm, čárka 2.25 / mezera 1.5 mm) → střídání černých a bílých bloků.
RAILWAY = IsomStyle(
    isom_code="509", kind="line", color=BLACK, width_mm=0.525,
    dash_mm=(2.25, 1.5), railway=True, z=46,
)
_RAILWAY_VALUES = {
    "rail", "light_rail", "narrow_gauge", "tram", "subway",
    "funicular", "monorail", "preserved", "miniature",
}


# OSM `surface=*` hodnoty značící PEVNÝ/zpevněný povrch → ISOM 503 Road.
_HARD_SURFACES = {
    "paved", "asphalt", "chipseal", "concrete", "concrete:lanes",
    "concrete:plates", "paving_stones", "sett", "unhewn_cobblestone",
    "cobblestone", "cobblestone:flattened", "bricks", "metal", "tartan",
}
ROAD_503 = _line("503", BLACK, 0.35, z=38)


def _is_hard_surface(tags: dict) -> bool:
    return tags.get("surface") in _HARD_SURFACES


def _is_less_distinct(tags: dict) -> bool:
    """Neformální / málo zřetelná pěšina → ISOM 507."""
    if tags.get("informal") == "yes":
        return True
    return tags.get("trail_visibility") in ("bad", "horrible", "no", "intermediate")


def _highway_style(tags: dict) -> IsomStyle | None:
    """Klasifikace cesty s ohledem na povrch a `tracktype` (kvalita/šířka).

    OSM `highway=track` pokrývá od sjízdné lesní cesty po sotva znatelnou
    stopu. Pravidla:
      - PEVNÝ povrch (surface=paved/asphalt/…) → 503 Road (plná černá 0.35 mm),
        protože je to udržovaná zpevněná komunikace,
      - jinak podle `tracktype`:
          grade1/2 → 504 vozová cesta (širší čárkovaná, sjízdná)
          grade5   → 506 málo zřetelná pěšinka (nejjemnější)
          grade3/4 nebo neuvedeno → 505 pěšina (konzervativně užší)
    """
    hw = tags["highway"]
    # Zpevněná cesta/silnice bez vlastní třídy → 503 (plná čára).
    if hw in ("track", "road", "service") and _is_hard_surface(tags):
        return ROAD_503
    # Neformální / málo zřetelná pěšina → 507 (dvojitá čárka).
    if hw in ("path", "footway", "bridleway", "track") and _is_less_distinct(tags):
        return LESS_DISTINCT_PATH
    if hw == "track":
        tt = tags.get("tracktype")
        if tt in ("grade1", "grade2"):
            return HIGHWAY_STYLES["track"]      # 504
        if tt == "grade5":
            return HIGHWAY_STYLES["trail"]      # 506
        # grade3, grade4, nebo neuvedeno → 505 pěšina
        return HIGHWAY_STYLES["path"]
    return HIGHWAY_STYLES.get(hw)


# --- EXPERIMENTÁLNÍ BODOVÉ SYMBOLY ---
# ISOM 418 výrazný strom — zelený kroužek (obrys), bílý/prázdný střed.
DISTINCT_TREE = IsomStyle(
    isom_code="418", kind="point", color=GREEN_DARK, z=44,
    point_radius_mm=0.3, point_outline_color=GREEN_DARK, point_outline_mm=0.25,
)
# ISOM 206 balvan — plný černý bod.
BOULDER = IsomStyle(
    isom_code="206", kind="point", color=BLACK, z=44, point_radius_mm=0.22,
)
# ISOM 109 kupka / 111 prohlubeň — hnědé body (z pullauta DXF, jen do .omap).
KNOLL_PT = IsomStyle(isom_code="109", kind="point", color=BROWN, z=43, point_radius_mm=0.3)
DEPRESSION_PT = IsomStyle(
    isom_code="111", kind="point", color=BROWN, z=43,
    point_radius_mm=0.3, point_outline_color=BROWN, point_outline_mm=0.18,
)


# Hodnoty `water=*`, které značí MĚLKOU/brodelnou vodu → ISOM 302.
_SHALLOW_WATER_VALUES = {
    "wetland", "marsh", "fishpond", "reflecting_pool", "salt_pool",
    "stream_pool", "lock",
}


def _water_style(tags: dict) -> IsomStyle:
    """Rozliší nepřekonatelnou (301) vs. brodelnou/mělkou (302) vodní plochu.

    OSM hloubku přímo nenese, použijeme dostupné signály:
      - `intermittent=yes` / `seasonal` (vyjma „no") → mělká/periodická → 302,
      - `water` ∈ mělké hodnoty (rybníček, mokřad…) → 302,
      - jinak (jezero, přehrada, řeka, běžná plocha) → nepřekonatelná 301.
    301 je bezpečný default (hlubokou vodu nelze brodit).
    """
    seasonal = tags.get("seasonal")
    if (tags.get("intermittent") == "yes"
            or (seasonal and seasonal != "no")
            or tags.get("water") in _SHALLOW_WATER_VALUES):
        return SHALLOW_WATER
    return WATER_AREA


def style_for(tags: dict) -> IsomStyle | None:
    """Pro OSM tagy vrátí ISOM styl, nebo None pokud feature ignorujeme."""
    if "building" in tags:
        return BUILDING
    # Zpevněná účelová plocha → 501. Parkoviště, plochy silnic (area:highway)
    # a zpevněné pěší/obslužné PLOCHY (area=yes + pevný povrch).
    if tags.get("amenity") == "parking" or "area:highway" in tags:
        return PAVED_AREA
    if (tags.get("area") == "yes"
            and tags.get("highway") in ("pedestrian", "footway", "service", "living_street")
            and (_is_hard_surface(tags) or tags.get("highway") == "pedestrian")):
        return PAVED_AREA
    if tags.get("natural") == "water" or tags.get("water"):
        return _water_style(tags)
    if "waterway" in tags:
        return WATERWAY_STYLES.get(tags["waterway"])
    if "highway" in tags:
        return _highway_style(tags)
    # Motokrosová/závodní dráha bez highway tagu → vozová cesta (504).
    if tags.get("sport") in ("motocross", "karting", "motor"):
        return HIGHWAY_STYLES["raceway"]
    if tags.get("railway") in _RAILWAY_VALUES:
        return RAILWAY
    # Les se odvozuje z LiDAR (organické okraje), proto OSM landuse=forest
    # ignorujeme. Ale ŽLUTÉ otevřené plochy rozlišujeme z OSM landuse, protože
    # pole/sady mají reálně rovné (katastrální) hrany: 401 louka / 403 paseka /
    # 412 pole.
    if "landuse" in tags:
        st = LANDUSE_STYLES.get(tags["landuse"])
        if st is not None:
            return st
    nat = tags.get("natural")
    if nat in ("wetland", "bare_rock"):
        return NATURAL_STYLES.get(nat)
    if nat in ("heath", "scrub", "fell", "shrubbery"):
        return ROUGH_OPEN          # 403 rough open land (paseka/vřesoviště)
    if nat in ("grassland",):
        return OPEN_LAND           # 401
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
