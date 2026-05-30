from backend.data_sources.cuzk_lidar import TILE_HEIGHT, TILE_WIDTH, tiles_for_bbox


def test_tiny_bbox_yields_at_least_one_tile():
    bbox = (16.60, 49.19, 16.61, 49.20)  # ~750×1100 m
    tiles = tiles_for_bbox(bbox)
    assert len(tiles) >= 1
    for t in tiles:
        assert t.xmax - t.xmin == TILE_WIDTH
        assert t.ymax - t.ymin == TILE_HEIGHT


def test_bbox_spans_multiple_tiles():
    # ~10×10 km výřez → minimálně několik dlaždic
    bbox = (16.50, 49.15, 16.65, 49.25)
    tiles = tiles_for_bbox(bbox)
    assert len(tiles) >= 4
