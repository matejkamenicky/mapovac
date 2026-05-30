"""Wrapper kolem `pullauta` (Karttapullautin) — Rust binárka.

Použití:
    from backend.lidar.karttapullautin import run_pullauta
    outputs = run_pullauta(work_dir, ["tile1.laz", "tile2.laz"], scale=10000)

Pullauta produkuje 600 DPI PNG mapy (s + bez depresí) a DXF (vrstevnice, srázy,
dotknolls) v adresáři `temp/` pracovního adresáře. Měřítko je nativně 1:10 000;
pro 1:7 500 výstup resamplujeme v `crop.py`.

Závislost: binárka `pullauta`. Cesta přes ENV `MAPOVAC_PULLAUTA_BIN`,
default `pullauta` (musí být v PATH).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_S = int(os.environ.get("MAPOVAC_PULLAUTA_TIMEOUT", "1800"))

# Repo-local fallback: `bin/pullauta` (3 úrovně nad tímto souborem:
# backend/lidar/karttapullautin.py → repo root).
_REPO_BIN = Path(__file__).resolve().parents[2] / "bin" / "pullauta"


def _default_bin() -> str:
    env = os.environ.get("MAPOVAC_PULLAUTA_BIN")
    if env:
        return env
    if _REPO_BIN.exists():
        return str(_REPO_BIN)
    return "pullauta"


PULLAUTA_BIN = _default_bin()


class PullautaNotInstalled(RuntimeError):
    pass


class PullautaFailed(RuntimeError):
    pass


@dataclass
class PullautaTileOutput:
    laz: Path
    png: Path | None
    png_depr: Path | None  # varianta s purple depressions
    pgw: Path | None       # world file pro georef
    contours_dxf: Path | None
    cliffs_small_dxf: Path | None
    cliffs_big_dxf: Path | None
    dot_knolls_dxf: Path | None
    vegetation_png: Path | None


def ensure_installed() -> str:
    """Vrátí cestu k binárce nebo vyhodí PullautaNotInstalled."""
    path = shutil.which(PULLAUTA_BIN)
    if not path:
        raise PullautaNotInstalled(
            f"Binárka `{PULLAUTA_BIN}` nenalezena v PATH. "
            "Stáhni z https://github.com/karttapullautin/karttapullautin/releases/latest "
            "a buď ji umísti do PATH, nebo nastav ENV MAPOVAC_PULLAUTA_BIN=/cesta/k/pullauta."
        )
    return path


_TEMPLATE_INI = Path(__file__).parent / "pullauta_template.ini"


def write_default_ini(work_dir: Path, scale: int = 10000, experimental: bool = False,
                      dense: bool = False) -> Path:
    """Vyrobí pullauta.ini z šablony (zachycený default v2.12.1) s úpravami
    pro ČR — méně agresivní detekce kamenů a kupek.

    Pullauta defaults jsou laděné na finské skandinávské mapování s velmi
    kamenitým terénem. Pro typický ČR (smíšený les, méně skal) snížíme
    citlivost cliff a knoll detektorů, jinak vznikne "konfety kamenů všude".
    """
    ini_path = work_dir / "pullauta.ini"
    if not _TEMPLATE_INI.exists():
        return ini_path  # fallback: nech pullauta vytvořit svoji
    content = _TEMPLATE_INI.read_text(encoding="utf-8")

    # Parametry laděné pro ČR klasifikovaný DMP1G (~0,15–0,35 b/m², třídy
    # 2=zem, 5=vegetace, 6=budovy). Pullauta z něj generuje vrstevnice,
    # vegetaci i srázy nativně.
    overrides = {
        # --- VEGETACE: víc zeleně (data jsou řídká → defaulty dělají skoro
        #     samou žlutou). Nižší prahy = víc/hustší zeleň. ---
        "yellowthresold": "0.97",            # méně agresivní žlutá (otevřeno)
        "firstandlastreturnasground": "2",   # default 3 — mírně víc vegetace
        "greenground": "0.7",                # default 0.9
        "thresold1": "0.20|3|0.06",          # poměr pro spuštění zeleně
        "thresold2": "3|4|0.06",
        "thresold3": "4|7|0.06",
        "thresold4": "7|20|0.06",
        "thresold5": "20|99|0.06",
        # Rozprostřené prahy → zeleň se rozdělí do více stupňů hustoty (ne vše
        # v nejtmavší). Recolor je pak sbalí do 3 ISOM stupňů. DMPOK je ~10×
        # hustší než DMP1G → vyšší greenness → vyšší prahy, jinak vše v nejtmavší.
        "greenshades": (
            "0.9|1.8|3.4|6.0|10.0|99|99|99|99|99|99" if dense
            else "0.35|0.6|1.0|1.8|3.0|99|99|99|99|99|99"
        ),
        # --- SRÁZY: výrazně méně. DMPOK je hustý → odhalí mikro-reliéf a
        #     pullauta z něj nadělá spoustu falešných srázů → pro dense ještě
        #     konzervativnější prahy (cliff1 2.2→4.0, cliffnosmallciffs 11→22).
        "cliff1": "4.0" if dense else "2.2",            # default 1.15
        "cliff2": "7.0" if dense else "4.0",            # default 2.0 (impassable)
        "cliffthin": "1.0",
        "cliffsteepfactor": "0.9" if dense else "0.7",  # default 0.38
        "cliffflatplace": "7.0" if dense else "5.5",    # default 3.5
        "cliffnosmallciffs": "22" if dense else "11",   # default 5.5
        "knolls": "0.25" if dense else "0.4",           # default 0.6
    }
    for key, value in overrides.items():
        # Patch line (s i bez mezery kolem =)
        import re
        pattern = rf"^{re.escape(key)}\s*=.*$"
        content, n = re.subn(pattern, f"{key}={value}", content, count=1, flags=re.MULTILINE)
        if n == 0:
            # klíč v šabloně nenalezen — přidej na konec
            content += f"\n{key}={value}\n"

    ini_path.write_text(content, encoding="utf-8")
    return ini_path


def run_for_tile(
    work_dir: Path,
    laz_path: Path,
    scale: int = 10000,
    experimental: bool = False,
    timeout_s: int | None = None,
    dense: bool = False,
) -> PullautaTileOutput:
    """Spustí pullauta nad jedním .laz souborem ve `work_dir`.

    Pullauta očekává být spuštěn v adresáři, kde má pullauta.ini a kam píše výstupy.
    `dense=True` → vyšší greenshades prahy pro hustý DMPOK.
    """
    ensure_installed()
    # Pullauta v2.12+ píše fixní jména výstupů („pullautus.png“) — proto
    # pro každou dlaždici vlastní subdir, ať se výstupy nepřepisují.
    work_dir = work_dir / laz_path.stem
    work_dir.mkdir(parents=True, exist_ok=True)
    write_default_ini(work_dir, scale=scale, experimental=experimental, dense=dense)

    # Symlink LAZ do working dir (pullauta píše do CWD)
    local_laz = work_dir / laz_path.name
    if not local_laz.exists():
        try:
            local_laz.symlink_to(laz_path.resolve())
        except OSError:
            shutil.copy2(laz_path, local_laz)

    env = {**os.environ, "RUST_LOG": os.environ.get("RUST_LOG", "info")}
    try:
        result = subprocess.run(
            [PULLAUTA_BIN, local_laz.name],
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s or DEFAULT_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise PullautaNotInstalled(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise PullautaFailed(f"pullauta timeout po {exc.timeout}s") from exc

    if result.returncode != 0:
        raise PullautaFailed(
            f"pullauta exit {result.returncode}\nSTDERR:\n{result.stderr[-2000:]}"
        )

    temp = work_dir / "temp"

    def _existing(p: Path) -> Path | None:
        return p if p.exists() else None

    # Pullauta v2.12+ píše fixní jména `pullautus.png` (a `_depr.png`) ve `work_dir`.
    # Pokud nějaký starší build piše `<stem>.png`, padneme na fallback.
    stem = local_laz.stem
    png = _existing(work_dir / "pullautus.png") or _existing(work_dir / f"{stem}.png")
    png_depr = _existing(work_dir / "pullautus_depr.png") or _existing(work_dir / f"{stem}_depr.png")
    pgw = _existing(work_dir / "pullautus.pgw") or _existing(work_dir / f"{stem}.pgw")

    return PullautaTileOutput(
        laz=local_laz,
        png=png,
        png_depr=png_depr,
        pgw=pgw,
        # v2.12 přejmenování:
        # out2.dxf = finální klasifikované vrstevnice (vrstvy contour /
        # contour_index / contour_intermed + depression*). contours03.dxf je
        # jen surový hustý meziprodukt — pro .omap nepoužitelný.
        contours_dxf=_existing(temp / "out2.dxf") or _existing(temp / "contours03.dxf"),
        cliffs_small_dxf=_existing(temp / "c2g.dxf") or _existing(temp / "c1g.dxf"),
        cliffs_big_dxf=_existing(temp / "c3g.dxf") or _existing(temp / "c2g.dxf"),
        dot_knolls_dxf=_existing(temp / "dotknolls.dxf"),
        vegetation_png=_existing(work_dir / "vegetation.png"),
    )


def merge_pngs(work_dir: Path, scale_divisor: int = 1) -> Path | None:
    """Pokud bylo více LAZ → spojí PNG dlaždice (`pullauta pngmerge`).

    scale_divisor=1 → plné rozlišení; vyšší hodnota zmenšuje výstup.
    """
    ensure_installed()
    try:
        result = subprocess.run(
            [PULLAUTA_BIN, "pngmerge", str(scale_divisor)],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise PullautaFailed(f"pngmerge timeout po {exc.timeout}s") from exc
    if result.returncode != 0:
        raise PullautaFailed(f"pngmerge exit {result.returncode}: {result.stderr[-500:]}")
    # Pullauta výstup `merged.png` (jméno se může lišit napříč verzemi)
    for cand in ("merged.png", "pullauta_merged.png", "merge.png"):
        p = work_dir / cand
        if p.exists():
            return p
    return None
