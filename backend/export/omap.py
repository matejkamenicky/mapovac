"""Export do .omap (OpenOrienteering Mapper XML, verze 9) — editovatelná mapa.

Vstup: vše v S-JTSK (EPSG:5514) — pullauta DXF (vrstevnice, srázy, kupky),
LiDAR vegetační polygony a OSM vektory. Výstup: georeferencovaný .omap se
symboly podle ISOM 2017, který jde otevřít a editovat v OO Mapperu.

Souřadnice objektů v OOM jsou v µm na papíře (1/1000 mm). Převod ze S-JTSK:

    mx = (E − refE) / scale · 1e6
    my = −(N − refN) / scale · 1e6      (papírové y míří dolů)

`refE/refN` = střed bboxu; v georeferencingu je to referenční bod na (0,0).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, box

from backend.data_sources.projection import sjtsk_to_wgs
from backend.export import dxf

# PROJ.4 pro EPSG:5514 (S-JTSK / Krovak East-North)
KROVAK_PROJ4 = (
    "+proj=krovak +lat_0=49.5 +lon_0=24.8333333333333 "
    "+alpha=30.2881397527778 +k=0.9999 +x_0=0 +y_0=0 +ellps=bessel "
    "+towgs84=570.8,85.7,462.8,4.998,1.587,5.261,3.56 +units=m +no_defs"
)

# Souřadnicové vlajky OOM (MapCoord flags)
CLOSE_POINT = 2   # poslední bod uzavřené části (plochy)
HOLE_POINT = 16   # poslední bod části, po které následuje další část (díra)


# ---------------------------------------------------------------------------
# ISOM barvy (CMYK 0..1). Pořadí = priorita kreslení (0 = nahoře).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OmapColor:
    name: str
    c: float
    m: float
    y: float
    k: float


# Přesné ISOM 2017-2 CMYK (z oficiálního OO Mapper symbol setu).
COLORS: list[OmapColor] = [
    OmapColor("Black 100%", 0.0, 0.0, 0.0, 1.0),       # 0
    OmapColor("Blue 100%", 1.0, 0.0, 0.0, 0.0),        # 1
    OmapColor("Brown 100%", 0.0, 0.56, 1.0, 0.18),     # 2
    OmapColor("Green 100%", 0.76, 0.0, 0.91, 0.0),     # 3 fight
    OmapColor("Green 60%", 0.456, 0.0, 0.546, 0.0),    # 4 walk
    OmapColor("Green 30%", 0.228, 0.0, 0.273, 0.0),    # 5 slow run
    OmapColor("Yellow", 0.0, 0.27, 0.79, 0.0),         # 6 open (401)
    OmapColor("Yellow 50%", 0.0, 0.135, 0.395, 0.0),   # 7 rough open (403)
    OmapColor("Brown 50%", 0.0, 0.28, 0.5, 0.09),      # 8 výplň vozovky (502)
    OmapColor("Yellow 100%/Green 50%", 0.38, 0.27, 0.886, 0.0),  # 9 zákaz vstupu (520)
    OmapColor("Blue 50%", 0.5, 0.0, 0.0, 0.0),         # 10 brodelná voda (302)
    OmapColor("Black 30%", 0.0, 0.0, 0.0, 0.30),       # 11 holá skála (213/214)
]
(COL_BLACK, COL_BLUE, COL_BROWN, COL_GREEN, COL_GREEN60, COL_GREEN30,
 COL_YELLOW, COL_YELLOW50, COL_BROWN50, COL_OLIVE, COL_BLUE50, COL_GRAY) = range(12)

# ISOM 502 Wide road — hnědá výplň (Brown 50 %) + černé lemovky (borders).
_WIDE_ROAD_502_BODY = (
    f'<line_symbol color="{COL_BROWN50}" line_width="450" minimum_length="0" '
    'join_style="1" cap_style="0" start_offset="0" end_offset="0" '
    'segment_length="6000" end_length="0" show_at_least_one_symbol="true" '
    'minimum_mid_symbol_count="0" minimum_mid_symbol_count_when_closed="0" '
    'dash_length="6000" break_length="1500" dashes_in_group="1" '
    'in_group_break_length="750" mid_symbols_per_spot="0" mid_symbol_distance="0">'
    f'<borders><border color="{COL_BLACK}" width="210" shift="105"/></borders>'
    '</line_symbol>'
)
# ISOM 509 Railway — plná černá s pražci (zjednodušeno: silná černá přerušovaná).
_RAILWAY_509_BODY = (
    f'<line_symbol color="{COL_BLACK}" line_width="525" minimum_length="0" '
    'join_style="1" cap_style="0" start_offset="0" end_offset="0" dashed="true" '
    'segment_length="6000" end_length="0" show_at_least_one_symbol="true" '
    'minimum_mid_symbol_count="0" minimum_mid_symbol_count_when_closed="0" '
    'dash_length="2250" break_length="1500" dashes_in_group="1" '
    'in_group_break_length="750" mid_symbols_per_spot="0" mid_symbol_distance="0"/>'
)

# ISOM 308 Marsh — vodorovné modré čárky (LinePattern), bez výplně.
_MARSH_308_BODY = (
    '<area_symbol inner_color="-1" min_area="0" patterns="1" rotatable="false">'
    f'<pattern type="1" angle="0" rotatable="false" line_spacing="450" '
    f'line_offset="0" offset_along_line="0" color="{COL_BLUE}" line_width="150"/>'
    '</area_symbol>'
)

# ISOM 412 Cultivated land — žlutá výplň + mřížka černých teček (PointPattern).
_CULTIVATED_412_BODY = (
    f'<area_symbol inner_color="{COL_YELLOW}" min_area="0" patterns="1" '
    'rotatable="false"><pattern type="2" angle="0" rotatable="false" '
    'line_spacing="1200" line_offset="0" offset_along_line="0" '
    'point_distance="1200" no_clipping="0">'
    '<symbol type="1" code="" name="Pattern fill">'
    f'<point_symbol rotatable="true" inner_radius="150" inner_color="{COL_BLACK}" '
    'outer_width="0" outer_color="-1" elements="0"/></symbol>'
    '</pattern></area_symbol>'
)


# ---------------------------------------------------------------------------
# ISOM symboly. type: 1=point, 2=line, 4=area.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OmapSymbol:
    code: str
    name: str
    kind: str               # "line" | "area" | "point"
    color: int
    line_width: int = 0     # µm (line)
    dash: tuple[int, int] | None = None  # (dash µm, gap µm)
    radius: int = 0         # µm (point — poloměr plného kruhu)
    raw_body: str | None = None  # vlastní XML těla symbolu (přesné ISOM symboly)


# ISOM 516 Fence (aktualizace 2024) — plná čára 0.14 mm se zoubkem (mid_symbol)
# na jednu stranu pod 60°, délka 0.4 mm, rozteč 2.0 mm. Zoubek (0,0)→(200,346)
# = 0.4 mm pod úhlem 60° (cos60·400=200, sin60·400=346). Barva 0 = Black.
_FENCE_516_BODY = (
    '<line_symbol color="0" line_width="140" minimum_length="2250" '
    'join_style="1" cap_style="0" segment_length="2000" end_length="1000" '
    'show_at_least_one_symbol="true" dash_length="6000" break_length="1500" '
    'dashes_in_group="1" in_group_break_length="750" mid_symbols_per_spot="1" '
    'mid_symbol_distance="0"><mid_symbol>'
    '<symbol type="1" code="" name="Mid symbol">'
    '<point_symbol rotatable="true" inner_radius="1500" inner_color="-1" '
    'outer_width="0" outer_color="-1" elements="1"><element>'
    '<symbol type="2" code=""><line_symbol color="0" line_width="140" '
    'segment_length="6000" end_length="0" dash_length="6000" break_length="1500" '
    'dashes_in_group="1" in_group_break_length="750" mid_symbols_per_spot="1" '
    'mid_symbol_distance="0"/></symbol>'
    '<object type="1"><coords count="2">0 0;200 346;</coords>'
    '<pattern rotation="0"><coord x="0" y="0"/></pattern></object>'
    '</element></point_symbol></symbol></mid_symbol></line_symbol>'
)


# Přesný ISOM 510 Power line z OO Mapper setu — plná čára 0.21 mm s příčkami
# na obě strany (dash_symbol, ±0.555 mm) v rozteči 6 mm. Barva 0 = Black.
_POWER_510_BODY = (
    '<line_symbol color="0" line_width="210" minimum_length="7500" '
    'join_style="1" cap_style="0" segment_length="6000" end_length="0" '
    'show_at_least_one_symbol="true" dash_length="6000" break_length="1500" '
    'dashes_in_group="1" in_group_break_length="750" mid_symbols_per_spot="1" '
    'mid_symbol_distance="0"><dash_symbol>'
    '<symbol type="1" code="" name="Dash symbol">'
    '<point_symbol rotatable="true" inner_radius="1500" inner_color="-1" '
    'outer_width="0" outer_color="-1" elements="1"><element>'
    '<symbol type="2" code=""><line_symbol color="0" line_width="210" '
    'segment_length="6000" end_length="0" dash_length="6000" break_length="1500" '
    'dashes_in_group="1" in_group_break_length="750" mid_symbols_per_spot="1" '
    'mid_symbol_distance="0"/></symbol>'
    '<object type="1"><coords count="2">0 -555;0 555;</coords>'
    '<pattern rotation="0"><coord x="0" y="0"/></pattern></object>'
    '</element></point_symbol></symbol></dash_symbol></line_symbol>'
)


# Šířky/dash v µm dle ISOM 2017-2 @ 1:10 000 (z oficiálního symbol setu).
SYMBOLS: list[OmapSymbol] = [
    OmapSymbol("101", "Contour", "line", COL_BROWN, line_width=210),
    OmapSymbol("102", "Index contour", "line", COL_BROWN, line_width=375),
    OmapSymbol("103", "Form line", "line", COL_BROWN, line_width=150, dash=(1800, 600)),
    OmapSymbol("201", "Impassable cliff", "line", COL_BLACK, line_width=525),
    OmapSymbol("202", "Cliff", "line", COL_BLACK, line_width=375),
    OmapSymbol("112", "Knoll", "point", COL_BROWN, radius=300),
    OmapSymbol("109", "Small knoll", "point", COL_BROWN, radius=375),
    OmapSymbol("111", "Small depression", "point", COL_BROWN, raw_body=(
        f'<point_symbol inner_radius="0" inner_color="-1" outer_width="180" '
        f'outer_color="{COL_BROWN}" elements="0" rotatable="false"/>'
    )),
    OmapSymbol("206", "Boulder", "point", COL_BLACK, radius=200),
    OmapSymbol("418", "Prominent tree", "point", COL_GREEN, raw_body=(
        f'<point_symbol inner_radius="75" inner_color="-1" outer_width="300" '
        f'outer_color="{COL_GREEN}" elements="0" rotatable="false"/>'
    )),
    OmapSymbol("401", "Open land", "area", COL_YELLOW),
    OmapSymbol("403", "Rough open land", "area", COL_YELLOW50),
    OmapSymbol("412", "Cultivated land", "area", COL_YELLOW, raw_body=_CULTIVATED_412_BODY),
    OmapSymbol("406", "Vegetation: slow running", "area", COL_GREEN30),
    OmapSymbol("408", "Vegetation: walk", "area", COL_GREEN60),
    OmapSymbol("410", "Vegetation: fight", "area", COL_GREEN),
    OmapSymbol("301", "Uncrossable body of water", "area", COL_BLUE),
    OmapSymbol("302", "Shallow body of water", "area", COL_BLUE50),
    OmapSymbol("308", "Marsh", "area", COL_BLUE, raw_body=_MARSH_308_BODY),
    OmapSymbol("304", "Crossable watercourse", "line", COL_BLUE, line_width=450),
    OmapSymbol("305", "Small crossable watercourse", "line", COL_BLUE, line_width=270),
    OmapSymbol("501", "Paved area", "area", COL_BROWN50),
    OmapSymbol("502", "Wide road", "line", COL_BROWN50, raw_body=_WIDE_ROAD_502_BODY),
    OmapSymbol("503", "Road", "line", COL_BLACK, line_width=350),
    OmapSymbol("504", "Vehicle track", "line", COL_BLACK, line_width=350, dash=(3000, 250)),
    OmapSymbol("505", "Footpath", "line", COL_BLACK, line_width=375, dash=(3000, 375)),
    OmapSymbol("506", "Small footpath", "line", COL_BLACK, line_width=270, dash=(1500, 375)),
    OmapSymbol("509", "Railway", "line", COL_BLACK, raw_body=_RAILWAY_509_BODY),
    # 415 Distinct cultivation boundary — plná černá 0.21 mm (hranice polí/luk).
    OmapSymbol("415", "Distinct cultivation boundary", "line", COL_BLACK, line_width=210),
    # 416 Distinct vegetation boundary — ZELENÁ ČÁRKOVANÁ (tmavě zelená 0.14 mm,
    # čárka 0.3 / mezera 0.2 mm). Zřetelná hranice porostů v lese.
    OmapSymbol("416", "Distinct vegetation boundary", "line", COL_GREEN,
               line_width=140, dash=(300, 200)),
    OmapSymbol("521", "Building", "area", COL_BLACK),
    OmapSymbol("516", "Fence", "line", COL_BLACK, line_width=140, raw_body=_FENCE_516_BODY),
    OmapSymbol("507", "Less distinct small footpath", "line", COL_BLACK, raw_body=(
        '<line_symbol color="0" line_width="180" minimum_length="0" '
        'join_style="1" cap_style="0" start_offset="0" end_offset="0" '
        'dashed="true" segment_length="6000" end_length="0" '
        'show_at_least_one_symbol="true" minimum_mid_symbol_count="0" '
        'minimum_mid_symbol_count_when_closed="0" dash_length="800" '
        'break_length="1000" dashes_in_group="2" in_group_break_length="250" '
        'mid_symbols_per_spot="0" mid_symbol_distance="0"/>'
    )),
    OmapSymbol("510", "Power line", "line", COL_BLACK, line_width=210, raw_body=_POWER_510_BODY),
    OmapSymbol("520", "Area that shall not be entered", "area", COL_OLIVE),
    OmapSymbol("213", "Bare rock", "area", COL_GRAY),
    OmapSymbol("601", "Magnetic north line", "line", COL_BLUE, line_width=120),
]
_CODE_TO_INDEX = {s.code: i for i, s in enumerate(SYMBOLS)}

# Mapování OSM/feature ISOM kódů na nejbližší definovaný symbol.
_CODE_ALIASES = {
    "305": "304",   # příkop → crossable watercourse
    "307": "301",   # mokřad → (modrá plocha) lake
    "405": None,    # les = bílá = papír → nekreslíme
}


def _symbol_index(code: str, kind: str) -> int | None:
    code = _CODE_ALIASES.get(code, code)
    if code is None:
        return None
    idx = _CODE_TO_INDEX.get(code)
    if idx is None:
        return None
    # když OSM dá linii s plošným kódem (řeka 301 jako linie) → stream 304
    sym = SYMBOLS[idx]
    if kind == "line" and sym.kind == "area" and code in ("301",):
        return _CODE_TO_INDEX.get("304")
    return idx


# ---------------------------------------------------------------------------
# Geometrie ke vložení
# ---------------------------------------------------------------------------
@dataclass
class OmapData:
    """Sběr geometrie pro export (vše v S-JTSK)."""
    contours: list[list[tuple[float, float]]] = field(default_factory=list)
    index_contours: list[list[tuple[float, float]]] = field(default_factory=list)
    formlines: list[list[tuple[float, float]]] = field(default_factory=list)
    cliffs: list[list[tuple[float, float]]] = field(default_factory=list)
    knolls: list[tuple[float, float]] = field(default_factory=list)
    # obecné bodové prvky: (ISOM kód, x, y) — kupky 109, prohlubně 111,
    # balvany 206, výrazné stromy 418.
    points: list[tuple[str, float, float]] = field(default_factory=list)
    # plochy/linie (code, kind, list rings/lines)
    areas: list[tuple[str, list[list[tuple[float, float]]]]] = field(default_factory=list)
    lines: list[tuple[str, list[list[tuple[float, float]]]]] = field(default_factory=list)
    open_polygons: list = field(default_factory=list)  # list[shapely Polygon]


class _Writer:
    def __init__(self, bbox_sjtsk: tuple[float, float, float, float], scale: int):
        self.xmin, self.ymin, self.xmax, self.ymax = bbox_sjtsk
        self.refE = (self.xmin + self.xmax) / 2.0
        self.refN = (self.ymin + self.ymax) / 2.0
        self.scale = scale
        self.bbox = box(self.xmin, self.ymin, self.xmax, self.ymax)
        self.objects: list[str] = []

    # --- převod souřadnic ---
    def _mx(self, e: float) -> int:
        return int(round((e - self.refE) / self.scale * 1_000_000))

    def _my(self, n: float) -> int:
        return int(round(-(n - self.refN) / self.scale * 1_000_000))

    def _line_coords_str(self, pts: list[tuple[float, float]]) -> str:
        return ";".join(f"{self._mx(e)} {self._my(n)}" for e, n in pts) + ";"

    def _poly_coords_str(self, poly: Polygon) -> tuple[str, int]:
        """Více částí (exterior + díry) v jednom path objektu.

        Každá část je uzavřená (CLOSE_POINT na posledním bodě). Není-li to
        poslední část, přidá se i HOLE_POINT (→ flag 18), což OOM bere jako
        „následuje díra".
        """
        rings = [list(poly.exterior.coords)]
        rings += [list(r.coords) for r in poly.interiors]
        last_ring = len(rings) - 1
        parts: list[str] = []
        count = 0
        for ri, ring in enumerate(rings):
            n = len(ring)
            for i, (e, nn) in enumerate(ring):
                count += 1
                if i == n - 1:
                    flag = CLOSE_POINT | (HOLE_POINT if ri < last_ring else 0)
                    parts.append(f"{self._mx(e)} {self._my(nn)} {flag}")
                else:
                    parts.append(f"{self._mx(e)} {self._my(nn)}")
        return ";".join(parts) + ";", count

    # --- emit objektů ---
    def add_line(self, code: str, coords: list[tuple[float, float]]):
        idx = _symbol_index(code, "line")
        if idx is None or len(coords) < 2:
            return
        for seg in _clip_line(LineString(coords), self.bbox):
            if len(seg) < 2:
                continue
            s = self._line_coords_str(seg)
            self.objects.append(
                f'<object type="1" symbol="{idx}"><coords count="{len(seg)}">{s}</coords></object>'
            )

    def add_polygon(self, code: str, poly: Polygon):
        idx = _symbol_index(code, "area")
        if idx is None:
            return
        for clipped in _clip_polygon(poly, self.bbox):
            if clipped.is_empty or clipped.area <= 0:
                continue
            s, count = self._poly_coords_str(clipped)
            if count < 4:
                continue
            self.objects.append(
                f'<object type="1" symbol="{idx}"><coords count="{count}">{s}</coords></object>'
            )

    def add_area(self, code: str, ring: list[tuple[float, float]]):
        if len(ring) < 3:
            return
        try:
            self.add_polygon(code, Polygon(ring))
        except Exception:
            pass

    def add_point(self, code: str, e: float, n: float):
        idx = _symbol_index(code, "point")
        if idx is None:
            return
        if not (self.xmin <= e <= self.xmax and self.ymin <= n <= self.ymax):
            return
        s = f"{self._mx(e)} {self._my(n)};"
        self.objects.append(
            f'<object type="0" symbol="{idx}"><coords count="1">{s}</coords></object>'
        )


def _clip_line(line: LineString, bbox) -> list[list[tuple[float, float]]]:
    try:
        inter = line.intersection(bbox)
    except Exception:
        return [list(line.coords)]
    if inter.is_empty:
        return []
    if isinstance(inter, LineString):
        return [list(inter.coords)]
    if isinstance(inter, MultiLineString):
        return [list(g.coords) for g in inter.geoms]
    return []


def _clip_polygon(poly: Polygon, bbox) -> list[Polygon]:
    try:
        if not poly.is_valid:
            poly = poly.buffer(0)
        inter = poly.intersection(bbox)
    except Exception:
        return [poly]
    if inter.is_empty:
        return []
    if isinstance(inter, Polygon):
        return [inter]
    if isinstance(inter, MultiPolygon):
        return list(inter.geoms)
    return []


def _georef_xml(w: _Writer, grivation: float = 0.0) -> str:
    lon, lat = sjtsk_to_wgs(w.refE, w.refN)
    return (
        f'<georeferencing scale="{w.scale}" declination="{grivation}" grivation="{grivation}">'
        f'<projected_crs id="EPSG:5514">'
        f'<spec language="PROJ.4">{escape(KROVAK_PROJ4)}</spec>'
        f"<parameter>5514</parameter>"
        f'<ref_point x="{w.refE:.2f}" y="{w.refN:.2f}"/>'
        f"</projected_crs>"
        f'<geographic_crs id="Geographic coordinate reference system">'
        f'<spec language="PROJ.4">+proj=latlong +datum=WGS84</spec>'
        f'<ref_point_deg lat="{lat:.8f}" lon="{lon:.8f}"/>'
        f"</geographic_crs>"
        f"</georeferencing>"
    )


def _colors_xml() -> str:
    out = [f'<colors count="{len(COLORS)}">']
    for i, c in enumerate(COLORS):
        # RGB fallback z CMYK (OOM si přepočítá, ale uvedeme explicitně)
        r = round((1 - c.c) * (1 - c.k) * 255)
        g = round((1 - c.m) * (1 - c.k) * 255)
        b = round((1 - c.y) * (1 - c.k) * 255)
        out.append(
            f'<color priority="{i}" name="{escape(c.name)}" '
            f'c="{c.c}" m="{c.m}" y="{c.y}" k="{c.k}" opacity="1">'
            f"<spotcolors/>"
            f'<cmyk method="custom"/>'
            f'<rgb method="cmyk" r="{r}" g="{g}" b="{b}"/>'
            f"</color>"
        )
    out.append("</colors>")
    return "".join(out)


_SYM_TYPE = {"point": 1, "line": 2, "area": 4}


def _symbols_xml() -> str:
    out = [f'<symbols count="{len(SYMBOLS)}">']
    for i, s in enumerate(SYMBOLS):
        t = _SYM_TYPE[s.kind]
        head = f'<symbol type="{t}" id="{i}" code="{escape(s.code)}" name="{escape(s.name)}">'
        if s.raw_body:
            out.append(head + s.raw_body + "</symbol>")
            continue
        if s.kind == "line":
            if s.dash:
                # Plná sada atributů — OOM bez nich čárkování nevykreslí.
                body = (
                    f'<line_symbol color="{s.color}" line_width="{s.line_width}" '
                    f'minimum_length="0" join_style="1" cap_style="0" '
                    f'start_offset="0" end_offset="0" dashed="true" '
                    f'segment_length="6000" end_length="0" '
                    f'show_at_least_one_symbol="true" '
                    f'minimum_mid_symbol_count="0" minimum_mid_symbol_count_when_closed="0" '
                    f'dash_length="{s.dash[0]}" break_length="{s.dash[1]}" '
                    f'dashes_in_group="1" in_group_break_length="0" '
                    f'mid_symbols_per_spot="0" mid_symbol_distance="0"/>'
                )
            else:
                body = (
                    f'<line_symbol color="{s.color}" line_width="{s.line_width}" '
                    f'minimum_length="0" join_style="1" cap_style="1"/>'
                )
        elif s.kind == "area":
            body = (
                f'<area_symbol inner_color="{s.color}" min_area="0" '
                f'patterns="0" rotatable="false"/>'
            )
        else:  # point — plný kruh
            body = (
                f'<point_symbol inner_radius="{s.radius}" inner_color="{s.color}" '
                f'outer_width="0" outer_color="-1" elements="0" rotatable="false"/>'
            )
        out.append(head + body + "</symbol>")
    out.append("</symbols>")
    return "".join(out)


def build_omap(
    out_path: Path,
    bbox_sjtsk: tuple[float, float, float, float],
    scale: int,
    data: OmapData,
    *,
    grivation: float = 0.0,
    template_image: str | None = None,
) -> Path:
    """Sestaví .omap soubor z nasbírané geometrie.

    `grivation` — magnetická deklinace (°) do georeferencingu.
    `template_image` — název obrázku (vedle .omap) jako georeferencovaný
    podklad (ortofoto); musí mít world file ve stejném CRS (EPSG:5514).
    """
    w = _Writer(bbox_sjtsk, scale)

    # Pořadí emitování = jen pro přehlednost; OOM řadí dle priority barvy/symbolu.
    for poly in data.open_polygons:
        w.add_polygon("401", poly)
    for code, rings in data.areas:
        for ring in rings:
            w.add_area(code, ring)
    for line in data.contours:
        w.add_line("101", line)
    for line in data.index_contours:
        w.add_line("102", line)
    for line in data.formlines:
        w.add_line("103", line)
    for line in data.cliffs:
        w.add_line("201", line)
    for code, lines in data.lines:
        for line in lines:
            w.add_line(code, line)
    for (e, n) in data.knolls:
        w.add_point("112", e, n)
    for (code, e, n) in data.points:
        w.add_point(code, e, n)

    objects_xml = "".join(w.objects)
    if template_image:
        # Ortofoto jako podklad ZA mapou (first_front_template=1 → index 0 vzadu).
        templates_xml = (
            '<templates count="1" first_front_template="1">'
            f'<template type="TemplateImage" open="true" name="{escape(template_image)}" '
            f'path="{escape(template_image)}" relpath="{escape(template_image)}" '
            f'georef="true"/></templates>'
        )
    else:
        templates_xml = '<templates count="0" first_front_template="0"/>'
    doc = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<map xmlns="http://openorienteering.org/apps/mapper/xml/v2" version="9">'
        "<notes>Generated by Mapovač (ISOM 2017, LiDAR + OSM).</notes>"
        + _georef_xml(w, grivation)
        + _colors_xml()
        + _symbols_xml()
        + '<parts count="1" current="0">'
        + '<part name="map">'
        + f'<objects count="{len(w.objects)}">{objects_xml}</objects>'
        + "</part></parts>"
        + templates_xml
        + "</map>"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
    return out_path
