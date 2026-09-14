import pytest
from shapely.geometry import Point, Polygon

from pixcut import imaging


def project(placements):
    return {
        "id": "project",
        "revision": 1,
        "pages": [{"id": "page", "placements": placements}],
    }


def placement(id, x, y, width, height, sticker_id="sticker"):
    return {
        "id": id,
        "sticker_id": sticker_id,
        "x_mm": x,
        "y_mm": y,
        "width_mm": width,
        "height_mm": height,
        "rotation": 0,
        "pinned": False,
    }


def definition(outline, padding=0, minimum_width=0):
    return {
        "id": "sticker",
        "revision": 1,
        "outline": outline,
        "cut_padding_mm": padding,
        "minimum_cut_width_mm": minimum_width,
    }


def test_padding_is_physical_and_does_not_scale_with_artwork():
    placements = [
        placement("small", 10, 10, 10, 10),
        placement("large", 40, 10, 20, 20),
    ]
    definitions = {
        "sticker": definition([(0, 0), (1, 0), (1, 1), (0, 1)], padding=1)
    }
    shapes = imaging.resolve(project(placements), definitions)[0]["shapes"]
    bounds = [Polygon(shape["outer"]["points"]).bounds for shape in shapes]
    assert bounds[0] == pytest.approx((9, 9, 21, 21))
    assert bounds[1] == pytest.approx((39, 9, 61, 31))
    assert all(shape["cut_policy"]["cut_padding_mm"] == 1 for shape in shapes)


def test_minimum_width_softens_shallow_notches_but_preserves_deep_openings():
    deep_outline = [
        (0, 0),
        (1, 0),
        (1, 1),
        (0.7, 1),
        (0.7, 0.2),
        (0.5, 0.2),
        (0.5, 1),
        (0, 1),
    ]
    shallow_outline = [
        (0, 0),
        (1, 0),
        (1, 1),
        (0.6, 1),
        (0.6, 0.97),
        (0.5, 0.97),
        (0.5, 1),
        (0, 1),
    ]
    placed = [placement("notched", 10, 10, 10, 10)]
    deep = imaging.resolve(
        project(placed),
        {"sticker": definition(deep_outline, minimum_width=1)},
    )
    shallow = imaging.resolve(
        project(placed),
        {"sticker": definition(shallow_outline, minimum_width=1)},
    )
    deep_polygon = Polygon(deep[0]["shapes"][0]["outer"]["points"])
    shallow_polygon = Polygon(shallow[0]["shapes"][0]["outer"]["points"])
    assert not deep_polygon.covers(Point(16, 18))
    assert shallow_polygon.covers(Point(15.5, 19.8))
    assert deep[0]["shapes"][0]["cut_policy"]["minimum_cut_width_mm"] == 1


def test_minimum_width_never_pulls_inside_uniform_padding_envelope():
    outline = [
        (0, 0),
        (1, 0),
        (1, 1),
        (0.7, 1),
        (0.7, 0.2),
        (0.5, 0.2),
        (0.5, 1),
        (0, 1),
    ]
    placed = [placement("notched", 10, 10, 10, 10)]
    resolved = imaging.resolve(
        project(placed),
        {"sticker": definition(outline, padding=0.5, minimum_width=1.5)},
    )
    cut = Polygon(resolved[0]["shapes"][0]["outer"]["points"])
    base = Polygon([(10 + u * 10, 10 + v * 10) for u, v in outline])
    uniform_padding = base.buffer(0.5, quad_segs=8)
    assert cut.buffer(1e-9).covers(uniform_padding)
    assert not cut.covers(Point(16, 17))
    # Rounded padding remains curved rather than collapsing to four corner chords.
    assert len(cut.exterior.coords) > 20


def test_minimum_width_caps_a_hairpin_without_sealing_its_wider_opening():
    tapered_notch = [
        (0, 0),
        (1, 0),
        (1, 1),
        (0.7, 1),
        (0.61, 0.2),
        (0.59, 0.2),
        (0.5, 1),
        (0, 1),
    ]
    resolved = imaging.resolve(
        project([placement("tapered", 10, 10, 10, 10)]),
        {
            "sticker": definition(
                tapered_notch, padding=0.1, minimum_width=1.5
            )
        },
    )
    cut = Polygon(resolved[0]["shapes"][0]["outer"]["points"])
    # The sub-millimeter return at the deep end is removed.
    assert cut.covers(Point(16, 15))
    # The same opening remains available where it becomes wider.
    assert not cut.covers(Point(16, 18))


def test_adjusted_cuts_revalidate_collisions_and_sheet_guard():
    definitions = {
        "sticker": definition(
            [(0, 0), (1, 0), (1, 1), (0, 1)], padding=1
        )
    }
    with pytest.raises(ValueError, match="CUT_COLLISION"):
        imaging.resolve(
            project(
                [
                    placement("left", 5, 10, 10, 10),
                    placement("right", 16, 10, 10, 10),
                ]
            ),
            definitions,
        )
    with pytest.raises(ValueError, match="cut padding.*crosses"):
        imaging.resolve(project([placement("edge", 2.5, 10, 10, 10)]), definitions)
