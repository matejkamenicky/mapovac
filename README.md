# Mapovač — generátor OB map pro ČR

Lokální webová aplikace, která pro vybraný výřez území ČR vygeneruje orientační mapu
podle klíče **ISOM 2017** v měřítku **1:7 500** nebo **1:10 000**, výstup **PDF** + **.omap**
(Open Orienteering Mapper).

Inspirace: [mapant.fi](https://www.mapant.fi/about.php). Generace probíhá on-demand z LiDAR
ČÚZK + ZABAGED / OSM.

> **Stav**: M1 (skeleton). Pipeline zatím produkuje placeholder soubory.
> Viz `plán` v `/Users/matejkamenicky/.claude/plans/`.

## Spuštění jedním klikem (macOS, doporučeno)

```bash
# jednorázově: závislosti + sestavení frontendu
python3.12 -m venv .venv && source .venv/bin/activate && pip install -e .
bash scripts/build-frontend.sh
```

Pak stačí **dvojklik na `Mapovač.app`** (lze přetáhnout do Docku). Spustí server
a otevře prohlížeč na `http://127.0.0.1:8000`. Zavřením appky (Quit) se server
ukončí. FastAPI servíruje API i frontend jako **jeden proces** (žádný Node za běhu).

> Po změně frontendu (`frontend/src`) spusť znovu `bash scripts/build-frontend.sh`.

## Vývojový režim (dva procesy)

### Backend (Python 3.12)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn backend.api.main:app --reload --port 8000
```

API běží na `http://127.0.0.1:8000` pod prefixem `/api`, docs na `/docs`.

### Frontend (Node 20+)

```bash
cd frontend
npm install
npm run dev
```

App otevři na `http://127.0.0.1:5173`. Vite proxuje `/api/*` na backend.

### Použití

1. V mapě **Shift + tažení myší** vykreslí obdélník výřezu (nebo zadej BBox ručně).
2. Vyber měřítko (1:10 000 nebo 1:7 500).
3. Volitelně zaškrtni experimentální detekce / ZABAGED.
4. **Generovat mapu** → progress bar → odkazy na PDF / .omap.

## Roadmap

- [x] **M1**: skeleton FastAPI + MapLibre frontend + dummy pipeline.
- [x] **M2**: ČÚZK WCS (DMR 5G + DMP 1G), OSM Overpass, disková cache, projekce S-JTSK.
- [x] **M3**: integrace [Karttapullautin](https://github.com/karttapullautin/karttapullautin)
      (Rust binárka `pullauta`) — vrstevnice, srázy, kameny. Crop výstupu
      na uživatelův bbox.
- [x] **M4**: OSM vektorový overlay (cesty, vodstvo, budovy, ploty, vedení).
- [x] **M5**: Vegetační klasifikace z CHM (DMP 1G − DMR 5G LAZ, laspy).
      ISOM 406/408/410 polygony.
- [ ] **M4**: vegetace (CHM) + srázy.
- [ ] **M5**: vektory (ZABAGED/OSM → ISOM 502–524).
- [ ] **M6**: symbolová knihovna ISOM 2017 (Mapnik/QGIS).
- [ ] **M7**: `.omap` export (OOM XML šablona).
- [ ] **M8**: experimentální body (kameny, kupky, výrazné stromy).
- [ ] **M9**: polish, dokumentace, Docker.

## ENV proměnné

| Proměnná | Účel | Default |
|---|---|---|
| `MAPOVAC_LAZ_ATOM_URL` | Master ATOM feed pro LAZ. Default = DMR 5G (terén z LiDAR ground returns). Alternativa = DMPOK (povrch z fotogrammetrie). | `atom.cuzk.gov.cz/DMR5G-SJTSK/DMR5G-SJTSK.xml` |
| `MAPOVAC_DMR5G_WCS` | INSPIRE WCS pro DMR | `ags.cuzk.gov.cz/.../INSPIRE_Nadmorska_vyska/...` |
| `MAPOVAC_DMP1G_WCS` | INSPIRE WCS pro DMP | totéž |
| `MAPOVAC_DMR_COVERAGE` | CoverageID v WCS | `1` |
| `MAPOVAC_OVERPASS_URL` | Overpass endpoint | `overpass-api.de` |
| `MAPOVAC_LAZ_URL_TEMPLATE` | Legacy: vlastní šablona URL pro LAZ dlaždice | — |
| `MAPOVAC_LAZ_DIR` | Lokální adresář s LAZ stáhnutými ručně z ČÚZK | `cache/laz_user` |
| `MAPOVAC_PULLAUTA_BIN` | Cesta k binárce `pullauta` | `pullauta` (musí být v PATH) |
| `MAPOVAC_PULLAUTA_TIMEOUT` | Timeout jedné pullauta dlaždice (s) | `1800` |

### Pokročilé generování (checkbox „Pokročilé generování")

Zapne další podklady. Self-contained části fungují rovnou (ortofoto RGB, NDVI/ExG
korekce vegetace, DMPOK point cloud, magnetická deklinace, ortofoto jako template
v `.omap`). Autoritativní WFS zdroje vyžadují nastavení endpointu (nelze je
univerzálně ověřit) — bez konfigurace se přeskočí.

| Proměnná | Účel | Default |
|---|---|---|
| `MAPOVAC_ORTOFOTO_WMS` | WMS GetMap ortofota | ČÚZK ArcGIS ORTOFOTO |
| `MAPOVAC_ORTOFOTO_LAYER` | vrstva ortofota | `0` |
| `MAPOVAC_ORTOFOTO_CIR_WMS` | WMS CIR (infračervené) → pravé NDVI | — (jinak RGB ExG) |
| `MAPOVAC_DECLINATION_DEG` | pevná magnetická deklinace (°) | dopočítaná pro ČR |
| `MAPOVAC_RUIAN_WFS` + `MAPOVAC_RUIAN_BUILDINGS_TYPENAME` | RÚIAN budovy (521) | — |
| `MAPOVAC_DIBAVOD_WFS` + `MAPOVAC_DIBAVOD_WATER_TYPENAME` / `_STREAM_TYPENAME` | DIBAVOD voda (301/304) | — |

Pokročilé generování přepíná LiDAR vstup z DMP1G na **DMPOK** (hustší → lepší
3 stupně zeleně); když DMPOK pro oblast není, spadne zpět na DMP1G.

### ZABAGED® (autoritativní vektory ČÚZK)

Zaškrtávátko „Použít ZABAGED" zapne přesné cesty/železnice/vodu/budovy z ČÚZK
místo OSM. ZABAGED GPKG je **jeden soubor pro celý stát (~6 GB)**, takže model je
stáhnout jednou → app čte jen výřez (přes prostorový index pyogrio).

```bash
# stáhne ~6 GB ZIP z ATOM a rozbalí (jednorázově)
.venv/bin/python -m backend.data_sources.zabaged
# vypíše cestu k .gpkg → nastav:
export MAPOVAC_ZABAGED_GPKG=/cesta/k/ZABAGED-5514.gpkg
# (ladění) výpis vrstev:
.venv/bin/python -m backend.data_sources.zabaged layers
```

| Proměnná | Účel | Default |
|---|---|---|
| `MAPOVAC_ZABAGED_GPKG` | cesta k lokálnímu ZABAGED GPKG (EPSG:5514) | — (bez něj se ZABAGED přeskočí) |

Mapování vrstev → ISOM je v `backend/data_sources/zabaged.py` (`_RULES`,
klíčová slova v názvu vrstvy). Když se některá vrstva nenamapuje správně,
uprav pravidla podle výpisu `… zabaged layers`.

**Známé ATOM feedy ČÚZK (S-JTSK, LAZ):**

| Dataset | Co obsahuje | URL |
|---|---|---|
| **DMR 5G** (default) | LiDAR terén (ground returns) — pro vrstevnice + srázy | `atom.cuzk.gov.cz/DMR5G-SJTSK/DMR5G-SJTSK.xml` |
| **DMP 1G** | LiDAR povrch (first returns) — spolu s DMR5G dává CHM pro vegetaci | `atom.cuzk.gov.cz/DMP1G-SJTSK/DMP1G-SJTSK.xml` |
| DMP OK | Povrch z fotogrammetrie (méně přesný než 1G) | `atom.cuzk.gov.cz/DMPOK-SJTSK-LAZ/DMPOK-SJTSK-LAZ.xml` |

ČÚZK aktuálně **neposkytuje veřejně klasifikovaný point cloud** (s ground/vegetation/
building klasifikací). Pro mapant-style vegetační odstíny je nutné stáhnout
DMR 5G + DMP 1G a klasifikaci dopočítat z rozdílu (Canopy Height Model). Plánováno na M5.

> Pokud WCS endpointy vrátí 404, ČÚZK je pravděpodobně přesunul — ověř na
> [geoportal.cuzk.cz](https://geoportal.cuzk.cz/) a přepiš ENV.

## Testy

```bash
pip install -e ".[dev]"
pytest backend/tests -v
```

## Systémové závislosti

### Karttapullautin (M3+) — **vyžadováno**

[karttapullautin/karttapullautin](https://github.com/karttapullautin/karttapullautin)
je Rust binárka `pullauta`, která z `.laz` generuje vrstevnice, vegetaci, srázy
a kameny v ISOM-podobném stylu.

```bash
# 1) Stáhni release binárku pro macOS z
#    https://github.com/karttapullautin/karttapullautin/releases/latest
#    a rozbal na vhodnou cestu, např. /usr/local/bin/pullauta
chmod +x /usr/local/bin/pullauta
pullauta --version  # ověření
```

Alternativně z source: `cargo install --git https://github.com/karttapullautin/karttapullautin`.

### LAZ data pro testování

ČÚZK distribuuje klasifikovaný point cloud přes ATOM service. Pro M3:

1. Otevři [atom.cuzk.cz](https://atom.cuzk.cz/) → vyber **Klasifikované laserové
   skenování** (KLAS).
2. Stáhni 1–2 dlaždice z oblasti, kterou chceš testovat.
3. Ulož do `cache/laz_user/` (nebo nastav `MAPOVAC_LAZ_DIR`).
4. V UI vyber bbox, který protíná staženou dlaždici → Generovat.

Aplikace si LAZ vybere podle BBox v LAS hlavičce.

### Pozdější milníky (M5+)

- **GDAL** ≥ 3.8 (`brew install gdal`) — pro vektorové operace v M5
- **Mapnik** nebo **QGIS** pro finální rendering (M6/M7)

## Struktura

```
backend/
  api/          # FastAPI endpointy
  jobs/         # fronta jobů
  pipeline/     # orchestrace
  data_sources/ # ČÚZK / OSM klienti  (M2)
  lidar/        # DTM, vrstevnice, vegetace, srázy  (M3–M4)
  vector/       # mapování ZABAGED/OSM → ISOM       (M5)
  compose/      # symbolizace                       (M6)
  export/       # PDF, .omap                        (M7)
frontend/       # Vite + MapLibre
cache/          # LiDAR + vektorová cache + výstupy (gitignored)
```
