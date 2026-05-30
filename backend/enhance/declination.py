"""Přibližná magnetická deklinace pro ČR (stupně, kladně = východně).

Přesný IGRF/WMM model je složitý; pro podkladovou mapu stačí lineární odhad
kalibrovaný pro ČR k epoše 2025 (deklinace v ČR roste cca ze 4° na JZ na ~6,5°
na SV a přibývá ~0,13°/rok). Mapér si přesnou hodnotu doladí v OO Mapperu.

Override přes ENV MAPOVAC_DECLINATION_DEG (pevná hodnota).
"""
from __future__ import annotations

import datetime
import os


def declination_deg(bbox_wgs: tuple[float, float, float, float]) -> float:
    env = os.environ.get("MAPOVAC_DECLINATION_DEG")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    w, s, e, n = bbox_wgs
    lon = (w + e) / 2.0
    lat = (s + n) / 2.0
    # Lineární model (epocha 2025) — kalibrováno na ČR.
    base = 4.0 + 0.35 * (lon - 12.0) + 0.5 * (lat - 48.5)
    year = datetime.date.today().year
    drift = 0.13 * (year - 2025)
    return round(base + drift, 2)
