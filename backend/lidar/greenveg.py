"""Detekce hustníků (zelená vegetace) přímo z klasifikovaného LiDAR cloudu.

Proč ne pullauta: Karttapullautin počítá zeleň z poměru návratů (first/last
return „hits below green"). To funguje na skandinávském multi-return LiDARu,
ale ČÚZK DMPOK (i DMP1G) jsou prakticky **single-return** mračna s hotovou
klasifikací (třída 5 = vegetace). Na nich pullauta zeleň silně podhodnocuje —
v datech je porostu spousta (~20 % bodů třídy 5), ale return-ratio ho „nevidí",
takže velké hustníky a paseky vyjdou bíle.

Model: u single-return povrchového mračna návrat dopadá na **vrchol koruny**,
ne dovnitř porostu — proto dostupný signál je výška koruny + zápoj (closure),
ne vnitřní hustota. Hustník (mladá hustá výsadba) = uzavřený mladý porost
nízkého vzrůstu:

- `htop`  = výška vrcholu koruny nad terénem (max z vegetačních bodů v buňce),
- `cover` = zápoj = podíl okolních buněk s korunou ≥ 1 m (uzavřenost porostu).

Klasifikace (fyzika smrkové výsadby — mladší = hustší = horší průchodnost):

- htop ≥ 8 m            → vzrostlý les → průchodný → BÍLÁ (žádná zeleň)
- cover < 0,40          → rozvolněný / jednotlivé stromy → BÍLÁ (průběžný les)
- uzavřený mladý porost (htop 1–8 m, cover ≥ 0,40):
    - htop < 3 m + hustý zápoj → ISOM 410 (fight) — klasický neprostupný hustník
    - htop 3–5 m              → ISOM 408 (walk)
    - htop 5–8 m / okraje      → ISOM 406 (slow run)

`htop` i `cover` jsou nezávislé na hustotě mračna → stejné prahy platí pro
řídký DMP1G i hustý DMPOK; rozlišení gridu se volí podle naměřené hustoty
(cíl ~TARGET_PER_CELL bodů/buňku).

Výsledkem jsou tři ISOM stupně zeleně (406/408/410) namalované do ISOM podkladu
**aditivně** — nikdy neubírají pullauta zeleň, jen ji doplní o nedetekované
hustníky. Vrstevnice, černé symboly a vodu zachovají.
"""
from __future__ import annotations

from pathlib import Path

import laspy
import numpy as np
from scipy.ndimage import median_filter, uniform_filter
from skimage.morphology import remove_small_objects

from backend.lidar.chm import _bin_to_grid, fill_nans
from backend.lidar.crop import WorldFile
from backend.lidar.openland import _linework
from backend.lidar.recolor import ISOM_GREEN_30, ISOM_GREEN_60, ISOM_GREEN_100


# Výškové prahy koruny (m nad terénem). Vyladěno na ČÚZK DMPOK/DMP1G ČR
# (Český les, Šumava — husté smrkové výsadby).
MATURE_H = 8.0      # ≥ → vzrostlý průchodný les (bílá)
FIGHT_H = 3.0       # < → nejhorší hustník (s dostatečným zápojem)
WALK_H = 5.0        # < → walk; mezi WALK_H a MATURE_H → slow run

# Zápoj (podíl okolních buněk s korunou): pod COVER_MIN je porost rozvolněný
# (průběžný les) → bílá. FIGHT vyžaduje vyšší uzavřenost než walk/slow.
COVER_MIN = 0.40
COVER_FIGHT = 0.60
COVER_WINDOW_M = 14.0   # okno pro výpočet zápoje (~14 m)

# Cílový počet bodů na buňku → z něj se odvodí rozlišení gridu.
TARGET_PER_CELL = 12.0
# Minimální plocha souvislého hustníku (m²) — odfiltruje izolovaný šum.
MIN_AREA_M2 = 60.0


def _auto_res(n_pts: int, bbox: tuple[float, float, float, float]) -> float:
    """Zvolí rozlišení gridu tak, aby na buňku vyšlo ~TARGET_PER_CELL bodů."""
    xmin, ymin, xmax, ymax = bbox
    area = max(1.0, (xmax - xmin) * (ymax - ymin))
    density = n_pts / area  # body/m²
    if density <= 0:
        return 4.0
    res = (TARGET_PER_CELL / density) ** 0.5
    return float(min(6.0, max(1.5, res)))


def classify_green(
    cloud_path: Path,
    bbox_sjtsk: tuple[float, float, float, float],
) -> tuple[np.ndarray, tuple[float, float, float, float], float]:
    """Z cloudu spočte rastr stupně zeleně (0=žádná, 1=406, 2=408, 3=410).

    Vrací (level_grid, bbox, res). Grid je v S-JTSK orientaci (řádek 0 = sever).
    """
    las = laspy.read(str(cloud_path))
    x = np.asarray(las.x)
    y = np.asarray(las.y)
    z = np.asarray(las.z, dtype=np.float32)
    cls = np.asarray(las.classification)

    xmin, ymin, xmax, ymax = bbox_sjtsk
    res = _auto_res(len(x), bbox_sjtsk)

    # Terén z pozemních bodů (třída 2), interpolovaný — hladká zem.
    g = cls == 2
    dtm = fill_nans(
        _bin_to_grid(x[g], y[g], z[g], bbox_sjtsk, res, "min"), max_kernel=12
    ).array
    H, W = dtm.shape

    # Výška vrcholu koruny nad terénem = max z vegetačních bodů (třída 5).
    veg = cls == 5
    vx, vy, vz = x[veg], y[veg], z[veg]
    vcol = np.clip(((vx - xmin) / res).astype(int), 0, W - 1)
    vrow = np.clip(((ymax - vy) / res).astype(int), 0, H - 1)
    vh = vz - dtm[vrow, vcol]
    htop = np.zeros(H * W, np.float32)
    np.maximum.at(htop, vrow * W + vcol, vh)
    htop = np.clip(htop.reshape(H, W), 0, None)
    htop = median_filter(htop, size=3)  # potlačí jednobuňkový šum

    # Zápoj: podíl okolních buněk s korunou ≥ 1 m (klouzavý průměr).
    win = max(3, int(round(COVER_WINDOW_M / res)) | 1)  # liché okno
    cover = uniform_filter((htop >= 1.0).astype(np.float32), size=win)

    mature = htop >= MATURE_H
    closed = (cover >= COVER_MIN) & ~mature
    young = (htop >= 1.0) & closed

    level = np.zeros((H, W), np.uint8)
    level[young & (htop < WALK_H)] = 2                         # walk (408)
    level[young & (htop >= WALK_H)] = 1                        # slow run (406)
    # mladý porost <3 m s hustým zápojem = nejhorší hustník (fight, 410).
    level[young & (htop < FIGHT_H) & (cover >= COVER_FIGHT)] = 3
    # zbytek mladého porostu (htop ≥ WALK_H už je 406) — slow run jako výplň
    level[young & (level == 0)] = 1

    # Odfiltruj izolovaný šum (souvislé hustníky zůstanou).
    min_px = max(1, int(MIN_AREA_M2 / (res * res)))
    anyg = level > 0
    keep = remove_small_objects(anyg, min_size=min_px, connectivity=2)
    level[~keep] = 0
    return level, bbox_sjtsk, res


def mark_dense_vegetation(
    isom_base_png: Path,
    pgw: Path,
    cloud_path: Path,
) -> dict:
    """Aditivně domaluje hustníky (ISOM 406/408/410) do ISOM podkladu.

    Zeleň z klasifikovaných vegetačních bodů (viz `classify_green`) namaluje
    přes bílou/žlutou/světlejší zeleň. Tmavší stupeň smí přebít světlejší
    (i pullauta zeleň), ale NIKDY neubírá — výsledek má vždy aspoň tolik
    zeleně co dřív. Vrstevnice, černé symboly a vodu zachová.
    """
    if not Path(cloud_path).exists():
        return {"dense_veg_px": 0, "skipped": "no cloud"}

    from PIL import Image

    wf = WorldFile.read(pgw)
    base = Image.open(isom_base_png).convert("RGB")
    arr = np.asarray(base).copy()
    H, W = arr.shape[:2]
    xmin = wf.origin_x
    ymax = wf.origin_y
    xmax = xmin + W * wf.pixel_size_x
    ymin = ymax + H * wf.pixel_size_y  # pixel_size_y < 0

    level, _, res = classify_green(cloud_path, (xmin, ymin, xmax, ymax))
    GH, GW = level.shape

    # Upsample level gridu na pixely podkladu (nearest).
    cols = np.arange(W)
    rows = np.arange(H)
    X = xmin + (cols + 0.5) * wf.pixel_size_x
    Y = ymax + (rows + 0.5) * wf.pixel_size_y
    ccol = np.clip(((X - xmin) / res).astype(int), 0, GW - 1)
    crow = np.clip(((ymax - Y) / res).astype(int), 0, GH - 1)
    lvl_px = level[np.ix_(crow, ccol)]

    # Zachovej vrstevnice (hnědá), černé symboly a modrou vodu.
    line = _linework(arr)
    b = arr[..., 2].astype(np.int16)
    rr = arr[..., 0].astype(np.int16)
    gg = arr[..., 1].astype(np.int16)
    water = (b > 180) & (rr < 160) & (gg > 140)
    protected = line | water

    colors = {1: ISOM_GREEN_30, 2: ISOM_GREEN_60, 3: ISOM_GREEN_100}
    rep = {"res_m": round(res, 2)}
    for lvl, col in colors.items():
        m = (lvl_px == lvl) & ~protected
        arr[m] = col
        rep[f"green_{lvl}_px"] = int(m.sum())
    Image.fromarray(arr, "RGB").save(isom_base_png)
    rep["dense_veg_px"] = sum(
        rep.get(f"green_{l}_px", 0) for l in (1, 2, 3)
    )
    return rep
