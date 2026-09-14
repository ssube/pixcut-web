from copy import deepcopy
from io import BytesIO

from PIL import Image, ImageDraw
import pytest

from pixcut import db, imaging
from pixcut.cli import migrate
from pixcut.extraction import extract
from pixcut.models import LayoutInput, ProjectCutPolicyInput, ProjectEdit, RenderInput
from pixcut.service import Problem, Service


def encoded(image):
    data = BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


def test_alpha_and_edge_connected_white_split_distinct_stickers():
    transparent = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
    draw = ImageDraw.Draw(transparent)
    draw.ellipse((15, 20, 125, 155), fill=(240, 70, 40, 255))
    draw.polygon(
        [(205, 15), (225, 65), (285, 70), (240, 105), (255, 165), (200, 130)],
        fill=(40, 90, 220, 255),
    )
    regions, method = extract(transparent)
    assert method == "alpha" and len(regions) == 2
    assert all(len(region.outline) > 4 for region in regions)

    white = Image.new("RGB", (320, 180), "white")
    draw = ImageDraw.Draw(white)
    draw.ellipse((15, 20, 125, 155), fill="red")
    draw.ellipse((45, 55, 90, 100), fill="white")
    draw.rounded_rectangle((190, 20, 300, 160), radius=25, fill="blue")
    regions, method = extract(white)
    assert method == "edge-connected-white" and len(regions) == 2
    # An enclosed white detail belongs to its sticker rather than the background.
    assert regions[0].image.getpixel((52, 57))[3] == 255


def test_extracted_project_has_independent_assets_contours_and_deletion(tmp_path):
    source = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.ellipse((15, 20, 125, 155), fill=(240, 70, 40, 255))
    draw.rounded_rectangle((190, 20, 300, 160), radius=25, fill=(40, 90, 220, 255))
    migrate(tmp_path)
    service = Service(tmp_path)
    asset = service.upload(encoded(source))
    result = service.extracted_project(asset["id"], "Two stickers")
    project = result["project"]

    assert result["component_count"] == 2
    assert len(project["bindings"]) == 2
    assert len(project["pages"][0]["placements"]) == 2
    assert project["pages"][0]["source_layout"] is True
    assert all(p["pinned"] for p in project["pages"][0]["placements"])
    source_scale = min(
        (imaging.PAGE_MM[0] - 4) / source.width,
        (imaging.PAGE_MM[1] - 4) / source.height,
    )
    for placement in project["pages"][0]["placements"]:
        definition = service.get(
            db.stickers,
            placement["sticker_id"],
            project["bindings"][placement["sticker_id"]],
        )["data"]
        crop = service.get(db.assets, definition["asset_id"])["data"]
        x0, y0, x1, y1 = crop["normalization"]["bbox_px"]
        assert placement["width_mm"] == pytest.approx((x1 - x0) * source_scale)
        assert placement["height_mm"] == pytest.approx((y1 - y0) * source_scale)

    shrunk_pages = deepcopy(project["pages"])
    for placement in shrunk_pages[0]["placements"]:
        center_x = placement["x_mm"] + placement["width_mm"] / 2
        center_y = placement["y_mm"] + placement["height_mm"] / 2
        placement["width_mm"] *= 0.55
        placement["height_mm"] *= 0.55
        placement["x_mm"] = center_x - placement["width_mm"] / 2
        placement["y_mm"] = center_y - placement["height_mm"] / 2
    project = service.project(
        ProjectEdit(
            name=project["name"],
            bindings=project["bindings"],
            pages=shrunk_pages,
            expected_revision=project["revision"],
        ),
        project["id"],
    )
    project = service.restore_source_layout(project["id"], project["revision"])
    for placement in project["pages"][0]["placements"]:
        definition = service.get(
            db.stickers,
            placement["sticker_id"],
            project["bindings"][placement["sticker_id"]],
        )["data"]
        crop = service.get(db.assets, definition["asset_id"])["data"]
        x0, y0, x1, y1 = crop["normalization"]["bbox_px"]
        assert placement["width_mm"] == pytest.approx((x1 - x0) * source_scale)
        assert placement["height_mm"] == pytest.approx((y1 - y0) * source_scale)
    geometry = imaging.resolve(project, service.definitions(project))
    assert len(geometry[0]["shapes"]) == 2
    assert all(len(shape["outer"]["points"]) > 4 for shape in geometry[0]["shapes"])

    unlocked_pages = deepcopy(project["pages"])
    for page in unlocked_pages:
        for placement in page["placements"]:
            placement["pinned"] = False
    project = service.project(
        ProjectEdit(
            name=project["name"],
            bindings=project["bindings"],
            pages=unlocked_pages,
            expected_revision=project["revision"],
        ),
        project["id"],
    )
    assert all(
        not placement["pinned"]
        for page in project["pages"]
        for placement in page["placements"]
    )
    proposal = service.propose_layout(
        project["id"],
        LayoutInput(
            revision=project["revision"],
            sticker_id=next(iter(project["bindings"])),
        ),
    )
    assert proposal["pages"][0]["placements"]

    legacy_pages = project["pages"]
    left, right = legacy_pages[0]["placements"]
    left["x_mm"], left["y_mm"] = 10, 20
    right["x_mm"] = left["x_mm"] + left["width_mm"] + 1
    right["y_mm"] = 20
    project = service.project(
        ProjectEdit(
            name=project["name"],
            bindings=project["bindings"],
            pages=legacy_pages,
            expected_revision=project["revision"],
        ),
        project["id"],
    )
    policy_result = service.apply_cut_policy(
        project["id"],
        ProjectCutPolicyInput(
            expected_revision=project["revision"],
            cut_padding_mm=1,
            minimum_cut_width_mm=1.5,
        ),
    )
    project = policy_result["project"]
    assert policy_result["layout_scale"] < 1
    definitions = service.definitions(project)
    assert all(
        definition["cut_padding_mm"] == 1
        and definition["minimum_cut_width_mm"] == 1.5
        for definition in definitions.values()
    )
    adjusted = imaging.resolve(project, definitions)
    assert all(
        shape["cut_policy"]
        == {"cut_padding_mm": 1, "minimum_cut_width_mm": 1.5}
        for shape in adjusted[0]["shapes"]
    )

    render = service.render(project["id"], RenderInput(revision=project["revision"]))
    service.delete_project(project["id"])
    assert service.get(db.renders, render["id"])["data"]["id"] == render["id"]
    try:
        service.get(db.projects, project["id"])
    except Problem as error:
        assert error.status == 404
    else:
        raise AssertionError("deleted project remained visible")
    service.engine.dispose()


def test_source_layout_keeps_interlocking_transparent_crops_at_full_scale(tmp_path):
    source = Image.new("RGBA", (240, 240), (0, 0, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.polygon(
        [
            (10, 10),
            (160, 10),
            (160, 50),
            (50, 50),
            (50, 190),
            (160, 190),
            (160, 230),
            (10, 230),
        ],
        fill=(240, 70, 40, 255),
    )
    draw.rectangle((90, 80, 220, 160), fill=(40, 90, 220, 255))
    migrate(tmp_path)
    service = Service(tmp_path)
    asset = service.upload(encoded(source))
    result = service.extracted_project(asset["id"], "Interlocking stickers")
    project = result["project"]

    assert result["component_count"] == 2
    assert project["pages"][0]["source_layout"] is True
    scale = (imaging.PAGE_MM[0] - 4) / source.width
    placements = project["pages"][0]["placements"]
    assert max(placement["width_mm"] for placement in placements) > 60
    assert max(placement["width_mm"] for placement in placements) == pytest.approx(
        151 * scale
    )
    assert len(imaging.resolve(project, service.definitions(project))[0]["shapes"]) == 2
    service.engine.dispose()
