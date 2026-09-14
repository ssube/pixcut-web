#!/usr/bin/env python3
"""Build a reproducible photo payload matrix; optionally print one point."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

from PIL import Image, ImageOps

from pixcut import db, imaging
from pixcut.commissioning import (
    PROFILE,
    PhotoSession,
    execute_photo,
    jpeg,
    lossless_png,
    photo_test_profile,
    prepare_photo,
)
from pixcut.service import Service
from pixcut.worker import worker_lock


DEFAULT_QUALITIES = "75,85,90,95"
DEFAULT_SIZES = "600x900,900x1350,1200x1800"
DEFAULT_FORMATS = "jpeg"
DEFAULT_PNG_STRATEGIES = "filtered,huffman,rle"
MIB = 1024 * 1024


def parse_qualities(value):
    try:
        qualities = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("qualities must be comma-separated integers") from exc
    if not qualities or len(qualities) != len(set(qualities)):
        raise argparse.ArgumentTypeError("provide one or more distinct quality values")
    for quality in qualities:
        try:
            photo_test_profile(PROFILE["raster"], quality)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
    return qualities


def parse_size(value):
    try:
        width, height = (int(part) for part in value.lower().split("x", 1))
        photo_test_profile([width, height], PROFILE["jpeg_quality"])
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return width, height


def parse_sizes(value):
    sizes = [parse_size(item.strip()) for item in value.split(",") if item.strip()]
    if not sizes or len(sizes) != len(set(sizes)):
        raise argparse.ArgumentTypeError("provide one or more distinct raster sizes")
    return sizes


def parse_formats(value):
    formats = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not formats or len(formats) != len(set(formats)):
        raise argparse.ArgumentTypeError("provide one or more distinct formats")
    if any(item not in ("jpeg", "png") for item in formats):
        raise argparse.ArgumentTypeError("formats must be jpeg and/or png")
    return formats


def parse_png_strategies(value):
    strategies = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not strategies or len(strategies) != len(set(strategies)):
        raise argparse.ArgumentTypeError("provide one or more distinct PNG strategies")
    for strategy in strategies:
        try:
            photo_test_profile(PROFILE["raster"], None, "png", strategy)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
    return strategies


def parse_point(value):
    try:
        size, setting = value.rsplit("@", 1)
        if setting.lower().startswith("png-"):
            return parse_size(size), "png", None, setting.lower()[4:]
        return parse_size(size), "jpeg", int(setting), None
    except (ValueError, argparse.ArgumentTypeError) as exc:
        raise argparse.ArgumentTypeError(
            "point must look like 1200x1800@95 or 1200x1800@png-huffman"
        ) from exc


def master(source, size):
    art = ImageOps.contain(source, size, Image.Resampling.LANCZOS)
    output = Image.new("RGB", size, "white")
    if art.mode == "RGBA":
        output.paste(
            art,
            ((size[0] - art.width) // 2, (size[1] - art.height) // 2),
            art.getchannel("A"),
        )
    else:
        output.paste(art, ((size[0] - art.width) // 2, (size[1] - art.height) // 2))
    return output


def build_matrix(
    image_path,
    output_dir,
    sizes,
    qualities,
    formats=("jpeg",),
    png_strategies=("filtered", "huffman", "rle"),
):
    raw = image_path.read_bytes()
    normalized, _, _, normalization = imaging.normalize(raw)
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for width, height in sizes:
        raster = master(normalized, (width, height))
        settings = [
            ("jpeg", quality, None) for quality in qualities if "jpeg" in formats
        ]
        if "png" in formats and [width, height] == PROFILE["raster"]:
            settings.extend(("png", None, item) for item in png_strategies)
        for image_format, quality, png_strategy in settings:
            profile = photo_test_profile(
                [width, height],
                quality,
                image_format=image_format,
                png_strategy=png_strategy,
            )
            name = (
                f"{image_path.stem}-{width}x{height}-q{quality}.jpg"
                if image_format == "jpeg"
                else (
                    f"{image_path.stem}-{width}x{height}-{png_strategy}.png"
                )
            )
            record = {
                "format": image_format,
                "png_strategy": png_strategy,
                "width": width,
                "height": height,
                "megapixels": round(width * height / 1_000_000, 4),
                "quality": quality,
                "filename": name,
            }
            try:
                payload = (
                    jpeg(raster, quality=quality, subsampling=0)
                    if image_format == "jpeg"
                    else lossless_png(raster, png_strategy)
                )
            except OSError as exc:
                record.update(
                    {
                        "bytes": None,
                        "mib": None,
                        "limit_bytes": profile["max_image_bytes"],
                        "limit_percent": None,
                        "sha256": None,
                        "status": "encoder_buffer_exceeded",
                        "error": str(exc),
                    }
                )
            else:
                (output_dir / name).write_bytes(payload)
                eligible = len(payload) <= profile["max_image_bytes"]
                if image_format == "png":
                    status = (
                        "over_4_mib"
                        if not eligible
                        else (
                            "within_2_to_4_mib"
                            if len(payload) >= 2 * MIB
                            else "under_2_mib"
                        )
                    )
                else:
                    status = (
                        "within_1_mib"
                        if len(payload) <= MIB
                        else "within_4_mib" if eligible else "over_4_mib"
                    )
                record.update(
                    {
                        "bytes": len(payload),
                        "mib": round(len(payload) / MIB, 4),
                        "limit_bytes": profile["max_image_bytes"],
                        "limit_percent": round(
                            len(payload) / profile["max_image_bytes"] * 100, 2
                        ),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "status": status,
                        "error": None,
                    }
                )
            records.append(record)
    records.sort(
        key=lambda record: record["bytes"] if record["bytes"] is not None else -1,
        reverse=True,
    )
    manifest = {
        "schema_version": "1.0",
        "source": {
            "path": str(image_path.resolve()),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "normalization": normalization,
        },
        "encoders": {
            "jpeg": {
                "subsampling": 0,
                "optimize": True,
                "dpi": [300, 300],
                "production_max_bytes": PROFILE["max_image_bytes"],
                "test_max_bytes": 4 * MIB,
            },
            "png": {
                "lossless": True,
                "max_bytes": 4 * MIB,
                "target_min_bytes": 2 * MIB,
            },
        },
        "order": "payload_bytes_descending",
        "points": records,
    }
    (output_dir / "matrix.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with (output_dir / "matrix.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    return manifest


def execute_point(image_path, point, key):
    size, image_format, quality, png_strategy = point
    if os.environ.get("PIXCUT_EXECUTION_MODE") != "hardware":
        raise RuntimeError("Set PIXCUT_EXECUTION_MODE=hardware for physical execution")
    service = Service(db.data_dir())
    try:
        with worker_lock(db.data_dir()):
            job = prepare_photo(
                service,
                image_path,
                key,
                raster=size,
                quality=quality,
                image_format=image_format,
                png_strategy=png_strategy,
            )
            import usb.core
            import usb.util

            from pixcut.discovery import scan_usb

            candidates = scan_usb()["devices"]
            if len(candidates) != 1:
                raise RuntimeError("Physical execution requires exactly one connected PixCut")
            selected = candidates[0]
            device = usb.core.find(
                idVendor=0x302C,
                idProduct=0x3101,
                bus=selected["bus"],
                address=selected["address"],
            )
            if device is None:
                raise RuntimeError("PixCut is not visible to libusb")
            with PhotoSession(device, usb.core, usb.util) as session:
                return execute_photo(service, job, session)
    finally:
        service.engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate a JPEG/PNG payload matrix. Physical mode can execute exactly "
            "one explicitly selected test point and never retries it."
        )
    )
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--qualities", type=parse_qualities, default=parse_qualities(DEFAULT_QUALITIES)
    )
    parser.add_argument("--sizes", type=parse_sizes, default=parse_sizes(DEFAULT_SIZES))
    parser.add_argument(
        "--formats", type=parse_formats, default=parse_formats(DEFAULT_FORMATS)
    )
    parser.add_argument(
        "--png-strategies",
        type=parse_png_strategies,
        default=parse_png_strategies(DEFAULT_PNG_STRATEGIES),
    )
    parser.add_argument(
        "--execute-point",
        type=parse_point,
        metavar="WIDTHxHEIGHT@QUALITY|png-STRATEGY",
        help="physically print one point after generating the matrix",
    )
    parser.add_argument("--idempotency-key")
    parser.add_argument("--confirm-4x6-photo-paper", action="store_true")
    parser.add_argument("--confirm-payload-test", action="store_true")
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error("image does not exist or is not a regular file")
    if "png" in args.formats and tuple(PROFILE["raster"]) not in args.sizes:
        parser.error("PNG payload tests require --sizes to include 1200x1800")
    manifest = build_matrix(
        args.image,
        args.output,
        args.sizes,
        args.qualities,
        args.formats,
        args.png_strategies,
    )
    print(json.dumps(manifest, indent=2), flush=True)
    if args.execute_point is None:
        return
    point_size, point_format, point_quality, point_png_strategy = args.execute_point
    if point_size not in args.sizes or point_format not in args.formats:
        parser.error("--execute-point must be one of the generated matrix points")
    if point_format == "jpeg" and point_quality not in args.qualities:
        parser.error("--execute-point must be one of the generated matrix points")
    if point_format == "png" and point_png_strategy not in args.png_strategies:
        parser.error("--execute-point must be one of the generated matrix points")
    selected = next(
        item
        for item in manifest["points"]
        if (item["width"], item["height"]) == point_size
        and item["format"] == point_format
        and item["quality"] == point_quality
        and item["png_strategy"] == point_png_strategy
    )
    allowed_statuses = (
        {"within_2_to_4_mib"}
        if point_format == "png"
        else {"within_1_mib", "within_4_mib"}
    )
    if selected["status"] not in allowed_statuses:
        parser.error(
            "selected PNG must be 2–4 MiB; selected JPEG must be within 1 MiB"
        )
    if not args.idempotency_key or len(args.idempotency_key) < 8:
        parser.error("--execute-point requires an --idempotency-key of at least 8 characters")
    if not args.confirm_4x6_photo_paper or not args.confirm_payload_test:
        parser.error(
            "--execute-point requires both paper and payload-test confirmations"
        )
    setting = (
        selected["quality"]
        if point_format == "jpeg"
        else f"png-{selected['png_strategy']}"
    )
    label = f"{selected['width']}x{selected['height']}@{setting}"
    answer = input(f"Type PRINT {label} to send exactly one physical photo: ")
    if answer != f"PRINT {label}":
        raise SystemExit("Physical test cancelled; no device intent was created")
    result = execute_point(args.image, args.execute_point, args.idempotency_key)
    print(json.dumps({"job_id": result["id"], "state": result["state"]}, indent=2))


if __name__ == "__main__":
    main()
