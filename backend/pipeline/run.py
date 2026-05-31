from __future__ import annotations

import json
import os
import shutil
import traceback
from pathlib import Path

from backend.data_sources import cuzk_dem, osm
from backend.data_sources.cuzk_atom import FEED_DMP1G, FEED_DMR5G
from backend.data_sources import ortofoto as ortofoto_mod
from backend.enhance import advanced as advanced_mod
from backend.enhance.declination import declination_deg
from backend.enhance.ndvi import correct_vegetation
from backend.data_sources.cuzk_lidar import CuzkLidarNotConfigured
from backend.data_sources.projection import bbox_wgs_to_sjtsk, sjtsk_to_wgs
from backend.jobs.queue import Job
from backend.lidar import karttapullautin
from backend.lidar.chm import compute_chm_and_forest
from backend.lidar.crop import crop_png_to_bbox_wgs
from backend.lidar.densify import densify_laz
from backend.lidar.laz_input import NoLazAvailable, laz_for_bbox, laz_from_feed
from backend.lidar.laz_merge import merge_laz
from backend.lidar.recolor import recolor_to_isom
from backend.lidar.openland import differentiate_open, mark_rough_open
from backend.lidar.smooth import smooth_landcover
from backend.lidar.mosaic import TileInput, mosaic_and_crop
from backend.lidar.vegetation import features_from_chm, forest_feature
from backend.vector.blank_canvas import make_blank
from backend.vector.overlay import render_overlay

CACHE_DIR = Path("cache")
OUTPUT_DIR = CACHE_DIR / "outputs"
PULLAUTA_WORK_ROOT = CACHE_DIR / "pullauta"

# Pullauta work dirs jsou meziprodukty (combined.laz + DXF + pullautus.png),
# po dokončení jobu nepotřebné → mažou se. Pro ladění: MAPOVAC_KEEP_PULLAUTA=1.
KEEP_PULLAUTA = os.environ.get("MAPOVAC_KEEP_PULLAUTA", "0") == "1"
# Strop velikosti cache/pullauta (GB) — pojistka; nejstarší se promažou.
PULLAUTA_CACHE_GB = float(os.environ.get("MAPOVAC_PULLAUTA_CACHE_GB", "8"))


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _cleanup_pullauta_work(work_dir: Path | None) -> None:
    """Smaže work dir aktuálního jobu (po dokončení) + LRU strop celé cache."""
    try:
        if work_dir and not KEEP_PULLAUTA and work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
    except Exception:
        pass
    # LRU strop: pokud cache/pullauta přeroste limit, maž nejstarší job diry.
    try:
        if not PULLAUTA_WORK_ROOT.exists():
            return
        cap = PULLAUTA_CACHE_GB * 1e9
        dirs = sorted(
            (d for d in PULLAUTA_WORK_ROOT.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
        )
        total = sum(_dir_size(d) for d in dirs)
        for d in dirs:
            if total <= cap:
                break
            sz = _dir_size(d)
            shutil.rmtree(d, ignore_errors=True)
            total -= sz
    except Exception:
        pass

# Přesah kolem bboxu (m) pro stažení LiDAR dlaždic — pohltí okrajové artefakty
# pullauty na hranici dat. Crop je pak ořízne zpět na uživatelův výřez.
MARGIN_M = 250.0


def _osm_bbox_for_preview(bbox_wgs: tuple[float, float, float, float]):
    """WGS bbox pokrývající celý S-JTSK preview obdélník (obálku bboxu).

    Preview se ořezává na S-JTSK obálku WGS bboxu; ta je kvůli rotaci Křováku
    větší. Převedeme rohy té obálky zpět do WGS a vezmeme jejich obálku +
    malou rezervu, aby OSM pokryl i okraje/rohy preview.
    """
    xmin, ymin, xmax, ymax = bbox_wgs_to_sjtsk(bbox_wgs)
    corners = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
    lons, lats = zip(*(sjtsk_to_wgs(x, y) for x, y in corners))
    dlon = (max(lons) - min(lons)) * 0.02
    dlat = (max(lats) - min(lats)) * 0.02
    return (min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat)


def _expand_bbox(bbox_wgs: tuple[float, float, float, float], margin_m: float):
    """Rozšíří WGS bbox [w, s, e, n] o `margin_m` na každou stranu."""
    import math
    w, s, e, n = bbox_wgs
    lat = (s + n) / 2.0
    dlat = margin_m / 111_320.0
    dlon = margin_m / (111_320.0 * max(0.1, math.cos(math.radians(lat))))
    return (w - dlon, s - dlat, e + dlon, n + dlat)


def run_pipeline(job: Job) -> None:
    """M3: stáhne data + spustí Karttapullautin → cropped PNG mapa.

    Tok:
      1. (kontext) DMR/DMP přes WCS — pro pozdější vektorové vrstvy.
      2. OSM Overpass — pro vektorové podklady (M5).
      3. LAZ → pullauta → PNG + DXF.
      4. Crop výsledku na uživatelův bbox.
      5. Výstupy: preview.png (cropped), report.json, placeholder PDF/.omap.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    job_dir = OUTPUT_DIR / job.id
    job_dir.mkdir(exist_ok=True)
    report: dict = {
        "bbox_wgs": list(job.bbox),
        "bbox_sjtsk": list(bbox_wgs_to_sjtsk(job.bbox)),
        "scale": job.scale,
        "options": job.options,
    }

    _step(job, 0.05, "Stahuji DMR 5G (terén) z ČÚZK WCS…")
    report["dmr5g"] = _try(lambda: str(cuzk_dem.fetch_dem(job.bbox, surface=False)))

    _step(job, 0.10, "Stahuji DMP 1G (povrch) z ČÚZK WCS…")
    report["dmp1g"] = _try(lambda: str(cuzk_dem.fetch_dem(job.bbox, surface=True)))

    _step(job, 0.20, "Stahuji OSM vektory přes Overpass…")
    # OSM musí pokrýt CELÝ S-JTSK preview obdélník — ten je kvůli rotaci
    # Křováku (~7°) větší než WGS bbox, takže u rohů/okrajů by jinak chyběly
    # cesty. Vezmeme WGS obálku S-JTSK obdélníku (přesné pro libovolnou velikost).
    osm_bbox = _osm_bbox_for_preview(job.bbox)
    osm_result = _try(lambda: osm.fetch_osm(osm_bbox))
    report["osm"] = str(osm_result) if isinstance(osm_result, Path) else osm_result
    if isinstance(osm_result, Path) and osm_result.exists():
        report["osm_summary"] = osm.summarize(osm_result)

    # Klasifikovaný DMP1G (třídy 2=zem, 5=vegetace, 6=budovy). Pullauta z něj
    # generuje vrstevnice + vegetaci + srázy NATIVNĚ — žádná densifikace ani
    # vlastní CHM (DMR5G má zem v třídě 8, kterou pullauta nebere → musel se
    # densifikovat; DMP1G má zem ve třídě 2, funguje rovnou a navíc nese
    # vegetační body pro zeleň).
    advanced = bool(job.options.get("advanced"))
    report["advanced"] = advanced
    feed = advanced_mod.lidar_feed(advanced)  # DMPOK (hustší) když advanced
    feed_name = "DMPOK" if advanced else "DMP1G"
    _step(job, 0.30, f"Stahuji klasifikované LiDAR dlaždice ({feed_name})…")
    # Dlaždice bereme pro bbox rozšířený o margin — pullauta poběží přes větší
    # oblast, takže okrajové artefakty (falešné srázy na hranici dat, „stěna")
    # padnou MIMO výřez a ořez na původní bbox je odřízne.
    fetch_bbox = _expand_bbox(job.bbox, MARGIN_M)
    try:
        laz_files = laz_from_feed(feed, fetch_bbox)
        if not laz_files and advanced:
            # DMPOK nedostupný pro oblast → fallback na DMP1G
            laz_files = laz_from_feed(FEED_DMP1G, fetch_bbox)
            report["dmpok_fallback"] = True
        report["laz_files"] = [str(p) for p in laz_files]
        if not laz_files:
            report["laz_error"] = "ATOM nevrátil žádnou dlaždici pro bbox."
    except Exception:
        report["laz_error"] = traceback.format_exc(limit=2)
        laz_files = []

    # ── Vektorové prvky (OSM nebo ZABAGED + pokročilé autoritativní) ──────────
    # Počítáme JEDNOU a brzy, ať z TÉŽE sady polygonů vychází jak výplň žluté
    # (fill_open_land), tak její obrys (416) v overlay i .omap — jinak by černá
    # hranice neseděla na žlutou plochu.
    vec_feats, osm_path = _collect_vector_features(
        job, osm_result, fetch_bbox, advanced, report
    )

    cropped_preview: Path | None = None
    pullauta_outputs: list[dict] = []
    exp_points: list = []  # experimentální body (418 stromy, 206 balvany)
    exp_line_features: list = []  # experimentální linie (416 hranice porostů)
    if laz_files:
        work_dir = PULLAUTA_WORK_ROOT / job.id
        work_dir.mkdir(parents=True, exist_ok=True)

        # Sloučíme dlaždice do JEDNOHO cloudu a spustíme pullauta JEDNOU přes
        # celou oblast — žádné švy ani okrajové artefakty mezi dlaždicemi.
        _step(job, 0.38, f"Slučuji {len(laz_files)} LiDAR dlaždic do jednoho cloudu…")
        try:
            if advanced:
                # DMPOK je extrémně hustý (~25 b/m²) → ořež na výřez+margin a
                # podvzorkuj na ~1,5 b/m² (jinak OOM / pomalá pullauta).
                merged = merge_laz(
                    laz_files, work_dir / "combined.laz",
                    bbox_sjtsk=bbox_wgs_to_sjtsk(fetch_bbox),
                    target_density=1.5,
                )
            else:
                merged = merge_laz(laz_files, work_dir / "combined.laz")
            report["merged_laz"] = str(merged)
        except Exception:
            report["merge_error"] = traceback.format_exc(limit=2)
            merged = laz_files[0]

        _step(job, 0.42, "Spouštím Karttapullautin nad sloučeným cloudem…")
        try:
            out = karttapullautin.run_for_tile(
                work_dir, merged,
                scale=job.scale,
                experimental=bool(job.options.get("experimental_points")),
                dense=advanced,  # DMPOK → vyšší greenshades prahy
            )
            pullauta_outputs.append({
                "laz": str(out.laz),
                "png": str(out.png) if out.png else None,
                "png_depr": str(out.png_depr) if out.png_depr else None,
                "pgw": str(out.pgw) if out.pgw else None,
                "contours_dxf": str(out.contours_dxf) if out.contours_dxf else None,
                "cliffs_small_dxf": str(out.cliffs_small_dxf) if out.cliffs_small_dxf else None,
                "cliffs_big_dxf": str(out.cliffs_big_dxf) if out.cliffs_big_dxf else None,
                "dot_knolls_dxf": str(out.dot_knolls_dxf) if out.dot_knolls_dxf else None,
            })
        except Exception:
            pullauta_outputs.append({"laz": str(merged), "error": traceback.format_exc(limit=3)})
        report["pullauta"] = pullauta_outputs

        # Experimentální bodová detekce z LiDAR cloudu.
        # Výrazné stromy (418) jsou spolehlivé. Balvany (206) z leteckého LiDARu
        # NEjsou (DTM mikro-reliéf dělá spoustu falešných) → defaultně vypnuté,
        # zapnout lze MAPOVAC_DETECT_BOULDERS=1 (i tak nejistá detekce).
        if job.options.get("experimental_points"):
            try:
                from backend.lidar.points import distinct_trees, boulders
                bx = bbox_wgs_to_sjtsk(job.bbox)
                trees = distinct_trees(Path(merged), bx)
                exp_points = [("418", x, y) for x, y in trees]
                rep = {"trees_418": len(trees)}
                if os.environ.get("MAPOVAC_DETECT_BOULDERS") == "1":
                    rocks = boulders(Path(merged), bx)
                    exp_points += [("206", x, y) for x, y in rocks]
                    rep["boulders_206"] = len(rocks)
                report["experimental"] = rep
            except Exception:
                report["experimental_error"] = traceback.format_exc(limit=2)
            # 416 zřetelné hranice porostů (jen ostré korunové srázy).
            try:
                from backend.lidar.vegboundary import distinct_vegetation_boundaries
                from backend.vector.isom_mapping import VEG_BOUNDARY_416
                from backend.vector.osm_features import Feature as _F
                bnds = distinct_vegetation_boundaries(Path(merged), bbox_wgs_to_sjtsk(job.bbox))
                exp_line_features = [_F(VEG_BOUNDARY_416, [ln], {}) for ln in bnds]
                report["veg_boundaries_416"] = len(bnds)
            except Exception:
                report["veg_boundaries_error"] = traceback.format_exc(limit=2)

        # Jediný výstup → přímý crop na bbox (žádná mozaika).
        usable = [o for o in pullauta_outputs if o.get("png") and o.get("pgw")]
        if usable:
            _step(job, 0.88, "Ořezávám pullauta výstup na bbox…")
            first = usable[0]
            try:
                cropped_preview = crop_png_to_bbox_wgs(
                    Path(first["png"]), Path(first["pgw"]),
                    job.bbox, job_dir / "preview.png",
                    target_scale=job.scale, source_scale=10000,
                )
                # Přemapuj pullauta paletu na přesné ISOM 2017 barvy (3 stupně zeleně).
                try:
                    recolor_to_isom(cropped_preview)
                    _pgw = cropped_preview.with_suffix(".pgw")
                    # Pokročilé: ortofoto + NDVI korekce zastaralé vegetace.
                    if advanced:
                        _advanced_ortofoto_ndvi(job, job_dir, cropped_preview, report)
                    # Paseka z LiDARu: otevřená plocha s nízkou vegetací → 403.
                    # (PŘED výplní polí — pole/louka z vektoru pak paseku přebije.)
                    try:
                        report["rough_open"] = mark_rough_open(
                            cropped_preview, _pgw, Path(merged),
                        )
                    except Exception:
                        report["rough_open_error"] = traceback.format_exc(limit=2)
                    # Louky/pole (401/412) z OSM/ZABAGED: vyplň vnitřek polygonu
                    # žlutě, ať výplň PŘESNĚ sedí na hranici (obrys 416). Tím
                    # zmizí bílý/zelený lem mezi žlutou plochou a černou linií.
                    try:
                        from backend.lidar.openland import fill_open_land
                        report["fill_open"] = fill_open_land(
                            cropped_preview, _pgw, vec_feats,
                        )
                    except Exception:
                        report["fill_open_error"] = traceback.format_exc(limit=2)
                    # Rozlišení žlutých ploch: 403 paseka (světlejší), 412 pole (tečky).
                    try:
                        lu = [f for f in vec_feats
                              if f.style.isom_code in ("403", "412")]
                        if lu:
                            report["openland"] = differentiate_open(
                                cropped_preview, _pgw, lu,
                            )
                    except Exception:
                        report["openland_error"] = traceback.format_exc(limit=2)
                    # Vyhladí blokové okraje ploch (vrstevnice/černá zůstanou ostré).
                    try:
                        smooth_landcover(cropped_preview)
                    except Exception:
                        report["smooth_error"] = traceback.format_exc(limit=2)
                    # Ulož ISOM podklad PO korekci, PŘED OSM overlayem — z něj
                    # vektorizujeme porosty/otevřeno do .omap.
                    (job_dir / "isom_base.png").write_bytes(cropped_preview.read_bytes())
                except Exception:
                    report["recolor_error"] = traceback.format_exc(limit=2)
            except Exception:
                report["crop_error"] = traceback.format_exc(limit=3)

    preview_path = job_dir / "preview.png"
    base_png: Path | None = cropped_preview
    base_pgw: Path | None = (
        cropped_preview.with_suffix(".pgw") if cropped_preview else None
    )

    # Fallback: žádný LAZ → bílé plátno, ať se na něj dají položit OSM vektory
    if base_png is None:
        _step(job, 0.90, "Bez LAZ — vytvářím bílé plátno pro OSM overlay…")
        try:
            base_png, base_pgw = make_blank(job.bbox, job.scale, job_dir / "base.png")
            report["base_blank"] = str(base_png)
        except Exception:
            report["base_blank_error"] = traceback.format_exc(limit=2)

    # Vegetaci/les/otevřeno už vykreslil pullauta nativně z klasifikovaného
    # DMP1G (zeleň + bílý les + žlutá otevřená plocha). Vlastní CHM overlay
    # proto nepoužíváme — overlay už jen pokládá OSM vektory.
    veg_features: list = []

    # Experimentální body (418 stromy, 206 balvany) → Feature pro raster overlay.
    from backend.vector.isom_mapping import DISTINCT_TREE, BOULDER
    from backend.vector.osm_features import Feature as _Feat
    _PT = {"418": DISTINCT_TREE, "206": BOULDER}
    exp_point_features = [_Feat(_PT[c], [[(x, y)]], {}) for c, x, y in exp_points if c in _PT]

    # Vektory (vec_feats) jsme spočetli brzy — viz _collect_vector_features.
    # OSM / ZABAGED overlay
    if base_png and base_pgw:
        _step(job, 0.94, "Renderuji vektory (OSM/ZABAGED)…")
        try:
            render_overlay(
                base_png, base_pgw, None, preview_path,
                target_scale=job.scale,
                extra_features=veg_features + vec_feats + exp_point_features + exp_line_features,
            )
            # PGW pro preview = stejný jako base
            preview_pgw = preview_path.with_suffix(".pgw")
            if base_pgw != preview_pgw:
                preview_pgw.write_text(base_pgw.read_text(), encoding="utf-8")
            report["overlay"] = "ok"
        except Exception:
            report["overlay_error"] = traceback.format_exc(limit=3)
            if base_png != preview_path:
                preview_path.write_bytes(base_png.read_bytes())
    elif base_png and base_png != preview_path:
        preview_path.write_bytes(base_png.read_bytes())

    # ISOM 601 — magnetické severky (modré) přes hotový preview, před PDF.
    grivation = 0.0
    try:
        grivation = declination_deg(job.bbox)
    except Exception:
        grivation = 0.0
    if job.options.get("north_lines", True):
        preview_pgw = preview_path.with_suffix(".pgw")
        if preview_path.exists() and preview_pgw.exists():
            try:
                from backend.vector.northlines import draw_north_lines
                report["north_lines"] = draw_north_lines(
                    preview_path, preview_pgw, grivation,
                )
            except Exception:
                report["north_lines_error"] = traceback.format_exc(limit=2)

    _step(job, 0.98, "Píšu výstupy…")
    pdf_path = job_dir / "map.pdf"
    omap_path = job_dir / "map.omap"
    report_path = job_dir / "report.json"

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not preview_path.exists():
        preview_path.write_bytes(_png_stub())

    # Tisknutelný PDF v pravém měřítku (z preview + jeho world file).
    preview_pgw = preview_path.with_suffix(".pgw")
    if preview_path.exists() and preview_pgw.exists():
        try:
            from backend.export.pdf import export_pdf
            export_pdf(preview_path, preview_pgw, pdf_path, job.scale, job.bbox)
            report["pdf"] = "ok"
        except Exception:
            report["pdf_error"] = traceback.format_exc(limit=3)
            pdf_path.write_bytes(_pdf_stub(report))
    else:
        pdf_path.write_bytes(_pdf_stub(report))

    # M7: .omap export (editovatelná mapa pro OO Mapper). OSM/ZABAGED prvky
    # předáváme jako vec_feats (extra_features), proto osm_path=None — jinak by
    # se OSM nakreslilo dvakrát (parse + extra).
    try:
        _build_omap_file(
            job, omap_path, pullauta_outputs, veg_features, None,
            advanced=advanced, extra_features=vec_feats + exp_line_features, exp_points=exp_points,
        )
        report["omap"] = "ok"
    except Exception:
        report["omap_error"] = traceback.format_exc(limit=3)
        omap_path.write_text(_omap_stub(report), encoding="utf-8")
    # přepíšeme report s finálním stavem .omap
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    job.pdf_path = pdf_path
    job.omap_path = omap_path
    job.preview_path = preview_path
    if cropped_preview and report.get("overlay") == "ok":
        job.message = "Hotovo (Karttapullautin + OSM overlay)."
    elif report.get("overlay") == "ok":
        job.message = "Hotovo (jen OSM overlay — chybí LAZ pro reliéf/vegetaci)."
    elif cropped_preview:
        job.message = "Hotovo (Karttapullautin; OSM se nepodařilo)."
    else:
        job.message = "Hotovo (stub — chybí LAZ i OSM)."

    # Úklid: smaž pullauta meziprodukty tohoto jobu + LRU strop cache.
    _cleanup_pullauta_work(PULLAUTA_WORK_ROOT / job.id)


def _collect_vector_features(job, osm_result, fetch_bbox, advanced, report):
    """Sestaví JEDINOU sadu vektorových prvků (vec_feats) + cestu k OSM JSON.

    Priorita zdroje liniové/plošné kresby:
      1) ZABAGED (když `use_zabaged` a GPKG nakonfigurován) — NAHRADÍ OSM,
      2) jinak OSM (přečteme z JSON jako Feature objekty).
    Navíc se vždy přidají pokročilé autoritativní vektory (RÚIAN budovy,
    DIBAVOD voda), pokud je `advanced`.

    Vrací (vec_feats, osm_path). vec_feats je JEDINÝ zdroj kresby — používá se
    pro výplň žluté (fill_open_land), pro obrys 416 i pro .omap, takže výplň
    přesně sedí na hranici. `osm_path` se vrací jen informativně (overlay/omap
    už OSM načítat nemají — kreslí se z vec_feats).
    """
    feats: list = []
    osm_path = (
        osm_result if isinstance(osm_result, Path) and osm_result.exists() else None
    )
    used_zabaged = False
    if job.options.get("use_zabaged"):
        try:
            from backend.data_sources.zabaged import zabaged_features, _resolve_gpkg
            if _resolve_gpkg():
                zfeats = zabaged_features(bbox_wgs_to_sjtsk(fetch_bbox))
                report["zabaged_features"] = len(zfeats)
                if zfeats:
                    feats = list(zfeats)
                    osm_path = None  # ZABAGED nahrazuje OSM
                    used_zabaged = True
                else:
                    report["zabaged"] = "0 prvků — zkontroluj GPKG / názvy vrstev"
            else:
                report["zabaged"] = "MAPOVAC_ZABAGED_GPKG nenastaven (stáhni GPKG z ATOM)"
        except Exception:
            report["zabaged_error"] = traceback.format_exc(limit=2)

    # Když kreslíme z OSM (ne ZABAGED), potřebujeme prvky i jako objekty
    # (kvůli fill_open_land / differentiate_open) — přečteme je z JSON.
    if not used_zabaged and osm_path is not None:
        try:
            from backend.vector.osm_features import parse as _osmparse
            feats = list(_osmparse(osm_path))
        except Exception:
            report["osm_features_error"] = traceback.format_exc(limit=2)

    if advanced:
        try:
            extra, adv_rep = advanced_mod.extra_vector_features(
                bbox_wgs_to_sjtsk(job.bbox)
            )
            feats = list(feats) + list(extra)
            report["advanced_vectors"] = adv_rep
        except Exception:
            report["advanced_vectors_error"] = traceback.format_exc(limit=2)

    return feats, osm_path


def _advanced_ortofoto_ndvi(job, job_dir, base_png, report) -> None:
    """Stáhne ortofoto pro výřez a opraví zastaralou vegetaci přes NDVI/ExG."""
    bx = bbox_wgs_to_sjtsk(job.bbox)
    pgw = base_png.with_suffix(".pgw")
    orto = job_dir / "ortofoto.png"
    # Zkus CIR (pravé NDVI), jinak RGB.
    cir = ortofoto_mod.fetch_ortofoto(bx, orto, cir=True)
    is_cir = cir is not None
    if not is_cir:
        cir = ortofoto_mod.fetch_ortofoto(bx, orto, cir=False)
    if cir is None:
        report["ortofoto"] = "nedostupné"
        return
    report["ortofoto"] = "CIR" if is_cir else "RGB"
    try:
        stats = correct_vegetation(
            base_png, pgw, orto, orto.with_suffix(".pgw"), cir=is_cir
        )
        report["ndvi_correction"] = stats
    except Exception:
        report["ndvi_error"] = traceback.format_exc(limit=2)


def _polyline_len(pts: list) -> float:
    return sum(
        ((pts[i][0] - pts[i - 1][0]) ** 2 + (pts[i][1] - pts[i - 1][1]) ** 2) ** 0.5
        for i in range(1, len(pts))
    )


def _build_omap_file(job, omap_path, pullauta_outputs, veg_features, osm_path,
                     advanced=False, extra_features=None, exp_points=None):
    """Sestaví OmapData ze všech zdrojů a zapíše .omap."""
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union

    from backend.export import dxf, omap as omap_mod
    from backend.export.omap import OmapData
    from backend.vector.osm_features import parse as osm_parse

    bx = bbox_wgs_to_sjtsk(job.bbox)
    data = OmapData()

    # 1) Pullauta DXF — vrstevnice, form lines, srázy, kupky
    for o in pullauta_outputs:
        cdxf = o.get("contours_dxf")
        if cdxf:
            p = Path(cdxf)  # out2.dxf — klasifikované vrstevnice dle vrstev
            data.contours.extend(
                dxf.read_polylines(p, layers={"contour", "depression"})
            )
            data.index_contours.extend(
                dxf.read_polylines(p, layers={"contour_index", "depression_index"})
            )
            data.formlines.extend(
                dxf.read_polylines(p, layers={"contour_intermed", "depression_intermed"})
            )
            data.formlines.extend(
                dxf.read_polylines(p.parent / "formlines.dxf", layers={"formline"})
            )
        for key in ("cliffs_small_dxf", "cliffs_big_dxf"):
            cf = o.get(key)
            if cf:
                for seg in dxf.read_polylines(Path(cf)):
                    # vyřaď velmi krátké úseky (šum) — délka < 2 m
                    if _polyline_len(seg) >= 2.0:
                        data.cliffs.append(seg)
        dk = o.get("dot_knolls_dxf")
        if dk:
            for x, y, layer in dxf.read_points(Path(dk)):
                if layer in ("dotknoll", "uglydotknoll"):
                    data.points.append(("109", x, y))      # kupka
                elif layer == "udepression":
                    data.points.append(("111", x, y))      # prohlubeň

    # Experimentální body (výrazné stromy 418, balvany 206).
    for code, x, y in (exp_points or []):
        data.points.append((code, x, y))

    # 2) Porosty + otevřená plocha — vektorizace z ISOM rastru (pullauta
    #    vegetaci dělá rastrově; tady ji převedeme na polygony pro .omap).
    from backend.lidar.raster_vectorize import vectorize_areas
    from backend.lidar.recolor import (
        ISOM_GREEN_30, ISOM_GREEN_60, ISOM_GREEN_100, ISOM_YELLOW,
    )

    isom_base = omap_path.parent / "isom_base.png"
    pgw = omap_path.parent / "preview.pgw"
    if isom_base.exists() and pgw.exists():
        color_map = {
            ISOM_GREEN_30: "406",   # slow running
            ISOM_GREEN_60: "408",   # walk
            ISOM_GREEN_100: "410",  # fight
            ISOM_YELLOW: "401",     # open land (louka)
            (255, 220, 154): "403",  # Yellow 50 % → rough open (paseka)
        }
        for code, rings in vectorize_areas(isom_base, pgw, color_map):
            data.areas.append((code, rings))

    # 3) OSM (+ pokročilé autoritativní vektory) — cesty, voda, budovy, ploty
    osm_feats = list(osm_parse(osm_path)) if osm_path else []
    for feat in osm_feats + list(extra_features or []):
        code = feat.style.isom_code
        kind = feat.style.kind
        if kind == "polygon":  # IsomStyle plochy mají kind="polygon" (ne "area")
            rings = [r for r in feat.parts if len(r) >= 3]
            data.areas.append((code, rings))
            # Louka/pole (401/412), zákaz vstupu (520), zpevněná plocha (501)
            # → plný černý obrys 415 (distinct cultivation boundary), jako mapant.
            if code in ("401", "412", "520", "501"):
                data.lines.append(("415", [r + [r[0]] for r in rings]))
        elif kind == "line":
            data.lines.append((code, [ln for ln in feat.parts if len(ln) >= 2]))

    # 4) Pokročilé: magnetická deklinace + ortofoto jako podkladový template
    grivation = 0.0
    try:
        grivation = declination_deg(job.bbox)
    except Exception:
        grivation = 0.0

    # ISOM 601 — magnetické severky do .omap (stejná geometrie jako v rasteru).
    if job.options.get("north_lines", True):
        try:
            from backend.lidar.crop import WorldFile as _WF
            from backend.vector.northlines import north_line_world_segments
            from PIL import Image as _Img
            prev = omap_path.parent / "preview.png"
            ppgw = prev.with_suffix(".pgw")
            if prev.exists() and ppgw.exists():
                with _Img.open(prev) as im:
                    W, H = im.size
                segs = north_line_world_segments(_WF.read(ppgw), W, H, grivation)
                if segs:
                    data.lines.append(("601", segs))
        except Exception:
            pass

    template_image = None
    if advanced:
        orto = omap_path.parent / "ortofoto.png"
        if orto.exists() and orto.with_suffix(".pgw").exists():
            template_image = "ortofoto.png"

    omap_mod.build_omap(
        omap_path, bx, job.scale, data,
        grivation=grivation, template_image=template_image,
    )
    return omap_path


def _step(job: Job, progress: float, message: str) -> None:
    job.progress = progress
    job.message = message


def _try(fn):
    try:
        return fn()
    except Exception:
        return {"error": traceback.format_exc(limit=2)}


def _pdf_stub(report: dict) -> bytes:
    body = json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8")
    return b"%PDF-1.4\n%mapovac-m3\n" + body + b"\n%%EOF\n"


def _omap_stub(report: dict) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<map xmlns="http://openorienteering.org/apps/mapper/xml/v2" version="9">\n'
        f"  <notes>M3 stub. Report keys: {list(report.keys())}</notes>\n"
        "</map>\n"
    )


def _png_stub() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d49444154789c63000100000005000100"
        "0d0a2db40000000049454e44ae426082"
    )


