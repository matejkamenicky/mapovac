"""Sdílený disk cache.

Klíč = hash(zdroj + parametry). Hodnota = soubor na disku v ``cache/<source>/<key><ext>``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CACHE_ROOT = Path("cache")


def cache_path(source: str, params: dict, ext: str) -> Path:
    key = hashlib.sha1(
        json.dumps(params, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    out_dir = CACHE_ROOT / source
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{key}{ext}"
