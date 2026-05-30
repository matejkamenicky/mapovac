from backend.data_sources.projection import (
    bbox_wgs_to_sjtsk,
    sjtsk_to_wgs,
    wgs_to_sjtsk,
)


def test_prague_roundtrip():
    # Praha — Staroměstské náměstí (přibližně)
    lon, lat = 14.4205, 50.0875
    x, y = wgs_to_sjtsk(lon, lat)
    # S-JTSK pro ČR má v ose X (East) typicky -700_000 až -900_000 (Krovak EN)
    assert -900_000 < x < -700_000
    assert -1_100_000 < y < -900_000
    lon2, lat2 = sjtsk_to_wgs(x, y)
    assert abs(lon - lon2) < 1e-6
    assert abs(lat - lat2) < 1e-6


def test_bbox_envelope_grows():
    # 1×1 km výřez kolem Brna
    bbox = (16.60, 49.19, 16.62, 49.20)
    xmin, ymin, xmax, ymax = bbox_wgs_to_sjtsk(bbox)
    assert xmax > xmin
    assert ymax > ymin
    # ~1.5 km × ~1.1 km přibližně — řád ověřujeme
    assert 800 < (xmax - xmin) < 3000
    assert 800 < (ymax - ymin) < 3000
