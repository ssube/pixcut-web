import hashlib

import pytest

from pixcut.printcut import PROFILE, combo_declaration, geometry_to_plt, overcut_ramp


def rectangle_snapshot(points=None, holes=None):
    return {
        "page_mm": [101.6, 177.8],
        "coordinate_space": "sheet_design",
        "origin": "top_left",
        "units": "mm",
        "shapes": [
            {
                "outer": {
                    "points": points
                    or [[5.0, 5.0], [30.0, 5.0], [30.0, 41.45], [5.0, 41.45]]
                },
                "holes": holes or [],
            }
        ],
    }


def test_native_rectangle_plt_snapshot():
    plot = geometry_to_plt(rectangle_snapshot())
    assert hashlib.sha256(plot).hexdigest() == (
        "4fbb222092bdcc3653f3ec0613caa024b7f27b212a525327698a14e66fab45b1"
    )
    assert plot.startswith(b"IN VER0.1.0 KP42 ")
    assert plot.endswith(b"U6476,0  @ ")


def test_overcut_ramps_follow_each_contour_seam():
    contour = [(0, 10), (0, 0), (10, 0), (10, 10)]
    points = overcut_ramp(contour)
    assert points[:3] == [(10, 20), (8, 15), (4, 11)]
    assert points[3:8] == [*contour, contour[0]]
    assert points[-3:] == [(-5, 11), (-9, 15), (-11, 20)]
    rotated = overcut_ramp([(10, 10), (0, 10), (0, 0), (10, 0)])
    assert rotated[:3] != points[:3]
    assert rotated[3:8] == [
        (10, 10),
        (0, 10),
        (0, 0),
        (10, 0),
        (10, 10),
    ]


def test_arbitrary_outer_contours_and_hole_rejection():
    with pytest.raises(ValueError, match="does not support holes"):
        geometry_to_plt(rectangle_snapshot(holes=[[[1, 1], [2, 1], [2, 2]]]))
    plot = geometry_to_plt(
        rectangle_snapshot(points=[[5, 5], [30, 6], [27, 25], [30, 41], [5, 38]])
    )
    assert plot.startswith(b"IN VER0.1.0 KP42 ")
    assert plot.count(b"D") == 12


def test_exact_minimal_combo_declaration_and_order():
    request = combo_declaration(b"JPEG", b"PLT")
    assert request == {
        "method": "combo-job",
        "params": [
            {
                "method": "print-job",
                "params": {
                    "channel": 2,
                    "copies": 1,
                    "media-size": 5013,
                    "media-type": 2030,
                    "job-type": 600,
                    "file-size": 4,
                },
            },
            {"method": "cut-job", "params": {"file-size": 3, "job-type": 600}},
        ],
    }
    assert PROFILE["data_order"] == "plt_then_jpeg"
    assert PROFILE["hardware_validated"] is False
    assert PROFILE["validation"]["rectangular_baseline"]["device_job_id"] == 17
