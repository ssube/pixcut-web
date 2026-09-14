"""Deterministic, non-destructive 4x6 photo adjustment and framing pipeline."""

from io import BytesIO
import hashlib
import json

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

PIPELINE_VERSION = "pixcut-photo-balance-v3"
PRESET_ADJUSTMENTS = {
    "original": {},
    "auto_balance": {},
    "vivid": {"contrast": 15, "saturation": 20, "sharpening": 15},
    "warm": {"temperature": 18, "saturation": 5},
    "cool": {"temperature": -18},
    "black_and_white": {"saturation": -100, "contrast": 8},
}
FILM_LOOKS = {
    "warm_negative": {
        "curve": 0.16,
        "black": 0.008,
        "white": 0.99,
        "saturation": 1.06,
        "shadows": (-0.008, 0.0, 0.016),
        "highlights": (0.022, 0.008, -0.014),
    },
    "faded_print": {
        "curve": 0.04,
        "black": 0.055,
        "white": 0.94,
        "saturation": 0.86,
        "shadows": (0.012, 0.004, -0.004),
        "highlights": (0.018, 0.008, -0.012),
    },
    "vivid_slide": {
        "curve": 0.31,
        "black": 0.0,
        "white": 1.0,
        "saturation": 1.18,
        "shadows": (-0.008, 0.0, 0.012),
        "highlights": (0.006, 0.004, -0.006),
    },
    "cool_chrome": {
        "curve": 0.14,
        "black": 0.014,
        "white": 0.985,
        "saturation": 0.96,
        "shadows": (-0.018, 0.002, 0.025),
        "highlights": (-0.006, 0.002, 0.012),
    },
}


def resolved_orientation(project):
    if project.get("layout", "single") != "single":
        return "portrait"
    requested = project["orientation"]
    if requested != "auto":
        return requested
    crop = project["crop"]
    return "landscape" if crop["width"] >= crop["height"] else "portrait"


def output_size(project, preview=False):
    if project.get("layout", "single") != "single":
        return (480, 720) if preview else (1200, 1800)
    landscape = resolved_orientation(project) == "landscape"
    if preview:
        return (720, 480) if landscape else (480, 720)
    return (1200, 1800)


def _crop(image, crop):
    left = round(crop["x"] * image.width)
    top = round(crop["y"] * image.height)
    right = round((crop["x"] + crop["width"]) * image.width)
    bottom = round((crop["y"] + crop["height"]) * image.height)
    right = max(left + 1, min(image.width, right))
    bottom = max(top + 1, min(image.height, bottom))
    return image.crop((left, top, right, bottom))


def _auto_balance(image):
    # Stable percentile stretching followed by a restrained gray-world correction.
    balanced = ImageOps.autocontrast(image, cutoff=0.5)
    values = np.asarray(balanced, dtype=np.float32)
    means = values.reshape(-1, 3).mean(axis=0)
    target = float(means.mean())
    scales = np.clip(target / np.maximum(means, 1), 0.85, 1.15)
    return Image.fromarray(np.clip(values * scales, 0, 255).astype(np.uint8), "RGB")


def _effective(project):
    values = dict(PRESET_ADJUSTMENTS[project["preset"]])
    for key, value in project["adjustments"].items():
        values[key] = values.get(key, 0) + value
    return values


def adjust(image, project):
    image = image.convert("RGB")
    if project["preset"] == "auto_balance":
        image = _auto_balance(image)
    values = _effective(project)
    exposure = values.get("exposure", 0)
    if exposure:
        image = ImageEnhance.Brightness(image).enhance(2**exposure)
    contrast = values.get("contrast", 0)
    if contrast:
        image = ImageEnhance.Contrast(image).enhance(max(0, 1 + contrast / 100))

    highlights, shadows = values.get("highlights", 0), values.get("shadows", 0)
    temperature, tint = values.get("temperature", 0), values.get("tint", 0)
    if highlights or shadows or temperature or tint:
        data = np.asarray(image, dtype=np.float32)
        luminance = (
            data[:, :, 0] * 0.2126
            + data[:, :, 1] * 0.7152
            + data[:, :, 2] * 0.0722
        ) / 255
        delta = (
            highlights / 100 * (luminance**2)
            + shadows / 100 * ((1 - luminance) ** 2)
        ) * 96
        data += delta[:, :, None]
        data[:, :, 0] += temperature * 0.35 + tint * 0.12
        data[:, :, 1] -= tint * 0.25
        data[:, :, 2] -= temperature * 0.35 + tint * 0.12
        image = Image.fromarray(np.clip(data, 0, 255).astype(np.uint8), "RGB")

    saturation = values.get("saturation", 0)
    if saturation:
        image = ImageEnhance.Color(image).enhance(max(0, 1 + saturation / 100))
    sharpening = values.get("sharpening", 0)
    if sharpening > 0:
        image = image.filter(
            ImageFilter.UnsharpMask(
                radius=1.5, percent=round(sharpening * 2), threshold=3
            )
        )
    return image


def film_color(image, look):
    if look == "none":
        return image
    config = FILM_LOOKS[look]
    data = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
    luminance = (
        data[:, :, 0] * 0.2126
        + data[:, :, 1] * 0.7152
        + data[:, :, 2] * 0.0722
    )
    smooth = luminance * luminance * (3 - 2 * luminance)
    curved = luminance + config["curve"] * (smooth - luminance)
    ratio = curved / np.maximum(luminance, 1 / 255)
    data *= ratio[:, :, None]
    shadows = (1 - luminance)[:, :, None]
    highlights = luminance[:, :, None]
    data += shadows * np.asarray(config["shadows"], dtype=np.float32)
    data += highlights * np.asarray(config["highlights"], dtype=np.float32)
    data = config["black"] + data * (config["white"] - config["black"])
    output = Image.fromarray(np.clip(data * 255, 0, 255).astype(np.uint8), "RGB")
    return ImageEnhance.Color(output).enhance(config["saturation"])


def film_grain(image, amount, project):
    if amount <= 0:
        return image
    # One correlated Gaussian field is applied to all channels, changing
    # luminance without creating colored digital-noise speckles.
    seed_material = json.dumps(
        {
            "settings": {
                key: project[key]
                for key in (
                    "crop",
                    "layout",
                    "orientation",
                    "framing",
                    "preset",
                    "film_look",
                    "adjustments",
                )
                if key in project
            },
            "size": list(image.size),
            "pipeline": PIPELINE_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    coarseness = 1 + round(amount / 34)
    noise_size = (
        max(1, image.width // coarseness),
        max(1, image.height // coarseness),
    )
    noise = rng.normal(127.5, 35, (noise_size[1], noise_size[0])).clip(0, 255)
    field = Image.fromarray(noise.astype(np.uint8), "L").resize(
        image.size, Image.Resampling.BICUBIC
    )
    centered = (np.asarray(field, dtype=np.float32) - 127.5) / 35
    data = np.asarray(image, dtype=np.float32)
    luminance = (
        data[:, :, 0] * 0.2126
        + data[:, :, 1] * 0.7152
        + data[:, :, 2] * 0.0722
    ) / 255
    response = 0.35 + 0.65 * np.sqrt(np.clip(4 * luminance * (1 - luminance), 0, 1))
    delta = centered * response * (amount / 100) * 14
    return Image.fromarray(
        np.clip(data + delta[:, :, None], 0, 255).astype(np.uint8), "RGB"
    )


def _frame(image, target, framing):
    if framing == "contain":
        fitted = ImageOps.contain(image, target, Image.Resampling.LANCZOS)
        output = Image.new("RGB", target, "white")
        output.paste(
            fitted,
            ((target[0] - fitted.width) // 2, (target[1] - fitted.height) // 2),
        )
        return output
    return ImageOps.fit(
        image, target, Image.Resampling.LANCZOS, centering=(0.5, 0.5)
    )


def layout_spec(project):
    layout = project.get("layout", "single")
    return {
        "single": {
            "id": "single",
            "copies": 1,
            "photo_size_in": [4, 6],
        },
        "yearbook_2x3": {
            "id": "yearbook_2x3",
            "copies": 4,
            "columns": 2,
            "rows": 2,
            "photo_size_in": [2, 3],
        },
        "yearbook_1_5x2": {
            "id": "yearbook_1_5x2",
            "copies": 6,
            "columns": 2,
            "rows": 3,
            "photo_size_in": [1.5, 2],
        },
        "photobooth_2x6": {
            "id": "photobooth_2x6",
            "copies": 2,
            "strip_size_in": [2, 6],
            "frames_per_strip": 4,
            "frame_size_in": [2, 1.5],
        },
    }[layout]


def _repeat_layout(selected, project, target):
    layout = project.get("layout", "single")
    grain = project["adjustments"].get("grain", 0)
    output = Image.new("RGB", target, "white")
    if layout == "yearbook_2x3":
        cell_size = (target[0] // 2, target[1] // 2)
        cell = film_grain(
            _frame(selected, cell_size, project["framing"]), grain, project
        )
        for row in range(2):
            for column in range(2):
                output.paste(cell, (column * cell_size[0], row * cell_size[1]))
        return output
    if layout == "yearbook_1_5x2":
        cell_size = (round(target[0] * 3 / 8), target[1] // 3)
        cell = film_grain(
            _frame(selected, cell_size, project["framing"]), grain, project
        )
        left = (target[0] - cell_size[0] * 2) // 2
        for row in range(3):
            for column in range(2):
                output.paste(cell, (left + column * cell_size[0], row * cell_size[1]))
        return output
    if layout == "photobooth_2x6":
        frame_size = (target[0] // 2, target[1] // 4)
        frame = film_grain(
            _frame(selected, frame_size, project["framing"]), grain, project
        )
        strip = Image.new("RGB", (frame_size[0], target[1]), "white")
        for row in range(4):
            strip.paste(frame, (0, row * frame_size[1]))
        output.paste(strip, (0, 0))
        output.paste(strip, (frame_size[0], 0))
        return output
    raise ValueError(f"Unsupported photo layout: {layout}")


def render(source, project, preview=False):
    selected = _crop(source.convert("RGB"), project["crop"])
    selected = adjust(selected, project)
    selected = film_color(selected, project.get("film_look", "none"))
    layout = project.get("layout", "single")
    target = output_size(project, preview)
    if layout != "single":
        return _repeat_layout(selected, project, target)
    landscape = resolved_orientation(project) == "landscape"
    working_target = target
    if landscape and not preview:
        working_target = (1800, 1200)
    output = _frame(selected, working_target, project["framing"])
    if landscape and not preview:
        output = output.transpose(Image.Transpose.ROTATE_90)
    return film_grain(output, project["adjustments"].get("grain", 0), project)


def preview_png(source, project):
    output = BytesIO()
    render(source, project, preview=True).save(output, "PNG", optimize=True)
    return output.getvalue()


def exact_jpeg(source, project):
    from .commissioning import PROFILE, jpeg

    quality = int(project.get("print_quality", 95))
    try:
        output = jpeg(render(source, project), quality=quality, subsampling=0)
    except OSError as exc:
        # Pillow's optimized encoder can exhaust its whole-image buffer for
        # high-entropy photos; such output necessarily exceeds this profile.
        raise ValueError(
            "PHOTO_PAYLOAD_TOO_LARGE: This photo is too large for the printer. Lower Print quality and review it again."
        ) from exc
    if len(output) > PROFILE["max_image_bytes"]:
        raise ValueError(
            "PHOTO_PAYLOAD_TOO_LARGE: This photo is too large for the printer. Lower Print quality and review it again."
        )
    return output


def profile():
    from .commissioning import PROFILE

    return PROFILE
