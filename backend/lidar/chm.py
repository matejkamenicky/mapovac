"""Canopy Height Model z ČÚZK DMR 5G (terén) + DMP 1G (povrch).

DMR 5G a DMP 1G jsou v LAZ formátu jako body v S-JTSK (EPSG:5514). Tady se
rasterizují do 1m gridu, spočte se rozdíl = CHM, a vrátí jako numpy 2D array
spolu s afinní transformací (pixel → S-JTSK).

Strategy:
- Pro DTM (DMR): pro každou buňku bereme MIN Z (nejnižší bod) — robustní vůči
  šumu. U DMR 5G už ČÚZK ground filter aplikoval, takže prakticky každá buňka
  obsahuje jeden bod.
- Pro DSM (DMP): MAX Z (nejvyšší vrchní bod = strom/budova).
- CHM = DSM - DTM, clipping záporných hodnot na 0.
- Buňky bez dat → NaN, později interpolováno z okolí.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import laspy
import numpy as np


@dataclass(frozen=True)
class Raster:
    array: np.ndarray  # shape (height, width), dtype float32, NaN = nodata
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    resolution: float  # m/pixel

    @property
    def height(self) -> int:
        return self.array.shape[0]

    @property
    def width(self) -> int:
        return self.array.shape[1]


def _read_laz_xyz(paths: list[Path]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    zs: list[np.ndarray] = []
    for p in paths:
        las = laspy.read(str(p))
        xs.append(np.asarray(las.x, dtype=np.float64))
        ys.append(np.asarray(las.y, dtype=np.float64))
        zs.append(np.asarray(las.z, dtype=np.float32))
    if not xs:
        return np.array([]), np.array([]), np.array([], dtype=np.float32)
    return np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)


def _bin_to_grid(
    x: np.ndarray, y: np.ndarray, z: np.ndarray,
    bbox_sjtsk: tuple[float, float, float, float],
    resolution: float,
    reducer: str,  # "min" | "max" | "mean"
) -> Raster:
    xmin, ymin, xmax, ymax = bbox_sjtsk
    width = max(1, int(np.ceil((xmax - xmin) / resolution)))
    height = max(1, int(np.ceil((ymax - ymin) / resolution)))

    col = np.floor((x - xmin) / resolution).astype(np.int64)
    row = np.floor((ymax - y) / resolution).astype(np.int64)
    mask = (col >= 0) & (col < width) & (row >= 0) & (row < height)
    col, row, zz = col[mask], row[mask], z[mask]
    flat_idx = row * width + col
    size = width * height

    grid = np.full(size, np.nan, dtype=np.float32)
    if zz.size == 0:
        return Raster(grid.reshape(height, width), xmin, ymin, xmax, ymax, resolution)

    if reducer == "mean":
        counts = np.bincount(flat_idx, minlength=size)
        sums = np.bincount(flat_idx, weights=zz.astype(np.float64), minlength=size)
        with np.errstate(invalid="ignore", divide="ignore"):
            grid_vals = np.where(counts > 0, sums / np.where(counts == 0, 1, counts), np.nan)
        grid = grid_vals.astype(np.float32)
    else:
        # min / max přes np.minimum.at / np.maximum.at (in-place)
        if reducer == "min":
            grid_arr = np.full(size, np.inf, dtype=np.float32)
            np.minimum.at(grid_arr, flat_idx, zz)
            grid = np.where(np.isfinite(grid_arr), grid_arr, np.nan)
        elif reducer == "max":
            grid_arr = np.full(size, -np.inf, dtype=np.float32)
            np.maximum.at(grid_arr, flat_idx, zz)
            grid = np.where(np.isfinite(grid_arr), grid_arr, np.nan)
        else:
            raise ValueError(f"reducer={reducer!r}")
    return Raster(grid.reshape(height, width), xmin, ymin, xmax, ymax, resolution)


def rasterize(
    laz_paths: list[Path],
    bbox_sjtsk: tuple[float, float, float, float],
    resolution: float = 1.0,
    reducer: str = "min",
) -> Raster:
    """LAZ body → 2D grid v S-JTSK."""
    x, y, z = _read_laz_xyz(laz_paths)
    return _bin_to_grid(x, y, z, bbox_sjtsk, resolution, reducer)


def fill_nans(raster: Raster, max_kernel: int = 5) -> Raster:
    """Jednoduchá interpolace NaN buněk průměrem okolí (post-binning)."""
    arr = raster.array.copy()
    nan_mask = np.isnan(arr)
    if not nan_mask.any():
        return raster
    # Iterativní fill: až `max_kernel` průchodů, každý průchod 3x3 mean ignorující NaN
    for _ in range(max_kernel):
        if not np.isnan(arr).any():
            break
        padded = np.pad(arr, 1, constant_values=np.nan)
        neighbors = np.stack([
            padded[0:-2, 0:-2], padded[0:-2, 1:-1], padded[0:-2, 2:],
            padded[1:-1, 0:-2],                     padded[1:-1, 2:],
            padded[2:, 0:-2],   padded[2:, 1:-1],   padded[2:, 2:],
        ])
        with np.errstate(invalid="ignore"):
            mean = np.nanmean(neighbors, axis=0)
        arr = np.where(np.isnan(arr), mean, arr)
    return Raster(arr, raster.xmin, raster.ymin, raster.xmax, raster.ymax, raster.resolution)


def compute_chm(dmr_paths: list[Path], dmp_paths: list[Path],
                bbox_sjtsk: tuple[float, float, float, float],
                resolution: float = 1.0) -> Raster:
    """CHM = DSM(max DMP) − DTM(min DMR), platné jen kde **oba mají** originální data.

    NeFILL_NANS — propagace by mimo joint coverage vyrobila falešné výšky
    (viditelné jako velké rovné polygony "veg_dense" na okrajích).
    Buňky bez dat → 0 (interpretováno jako otevřená plocha, neoverlay).

    Drobné jednobuňkové díry uvnitř pokrytí dorovnáme po klasifikaci přes
    morfologické closing — viz `vegetation.classify_smooth`.
    """
    dtm = rasterize(dmr_paths, bbox_sjtsk, resolution, "min")
    dsm = rasterize(dmp_paths, bbox_sjtsk, resolution, "max")
    valid = ~(np.isnan(dtm.array) | np.isnan(dsm.array))
    chm = np.where(valid, dsm.array - dtm.array, 0.0)
    chm = np.clip(chm, 0, None).astype(np.float32)
    return Raster(chm, dtm.xmin, dtm.ymin, dtm.xmax, dtm.ymax, resolution)


def compute_chm_and_forest(
    dmr_paths: list[Path], dmp_paths: list[Path],
    bbox_sjtsk: tuple[float, float, float, float],
    resolution: float = 1.0,
    canopy_min_m: float = 2.0,
) -> tuple[Raster, np.ndarray]:
    """Spočte CHM **a** boolean masku lesa (zápoj) odvozenou z LiDAR návratů.

    Les/otevřeno odvozujeme přímo z LiDAR (jako mapant), ne z hrubých OSM
    katastrálních polygonů — ty dělaly ostré rovné řezy. Signál:

    Les = **výška zápoje ≥ canopy_min_m**. Klíčové: DMR5G (terén) je řídký
    (~50–60 % buněk má pozemní bod), takže nelze brát „chybějící terén = les" —
    na otevřené louce taky chybí spousta pozemních bodů a vyšlo by les všude.
    Místo toho řídký terén **interpolujeme** (zem je hladká) a výšku zápoje
    spočteme proti vyplněnému terénu:

    - `DTM_filled` = interpolovaný hladký terén (fill_nans),
    - `CHM = DSM − DTM_filled` tam, kde má povrch (DMP) návrat,
    - **les = CHM ≥ canopy_min_m**, jinak otevřeno (žlutý pullauta podklad).

    Hranice jsou organické (kopírují reálný porost), ne katastrální OSM řezy.
    """
    dtm = rasterize(dmr_paths, bbox_sjtsk, resolution, "min")
    dsm = rasterize(dmp_paths, bbox_sjtsk, resolution, "max")
    # Vyplníme řídký terén — zem je hladká, interpolace je věrná. Bez toho by
    # každá buňka bez pozemního bodu (i na louce) vyšla jako vysoký zápoj.
    dtm_f = fill_nans(dtm, max_kernel=10)
    vdsm = ~np.isnan(dsm.array)
    vdtm = ~np.isnan(dtm_f.array)
    valid = vdsm & vdtm
    chm = np.where(valid, dsm.array - dtm_f.array, 0.0)
    chm = np.clip(chm, 0, None).astype(np.float32)
    forest = valid & (chm >= canopy_min_m)
    chm_r = Raster(chm, dtm.xmin, dtm.ymin, dtm.xmax, dtm.ymax, resolution)
    return chm_r, forest
