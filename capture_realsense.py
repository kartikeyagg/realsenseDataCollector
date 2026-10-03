#!/usr/bin/env python3
"""Capture synchronized RGB, stereo infrared, and depth from a RealSense D415."""

from __future__ import annotations

import argparse
import csv
import json
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple


STREAM_NAMES = ("color", "depth", "infrared_left", "infrared_right")
CSV_FIELDS = (
    "saved_index",
    "host_time_utc_ns",
    "elapsed_seconds",
    "frameset_number",
    "color_frame_number",
    "color_timestamp_ms",
    "depth_frame_number",
    "depth_timestamp_ms",
    "infrared_left_frame_number",
    "infrared_left_timestamp_ms",
    "infrared_right_frame_number",
    "infrared_right_timestamp_ms",
    "timestamp_domain",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Save synchronized D415 color, depth, and left/right infrared frames "
            "as OpenCV-readable files, with calibration and timestamps."
        )
    )
    parser.add_argument("--output", type=Path, default=Path("data"), help="dataset root")
    parser.add_argument("--session-name", help="directory name (default: UTC timestamp)")
    parser.add_argument("--serial", help="camera serial when more than one is attached")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--frames", type=int, default=0, help="saved frames; 0 means unlimited")
    parser.add_argument("--duration", type=float, default=0, help="seconds; 0 means unlimited")
    parser.add_argument("--warmup", type=int, default=30, help="frames discarded before recording")
    parser.add_argument(
        "--save-every",
        type=int,
        default=1,
        help="export every Nth frameset; the BAG still stores every frameset",
    )
    parser.add_argument("--color-format", choices=("png", "jpg"), default="png")
    parser.add_argument("--jpeg-quality", type=int, default=95)
    parser.add_argument("--emitter", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--no-preview", action="store_true", help="disable the OpenCV window")
    parser.add_argument("--no-bag", action="store_true", help="do not save the raw SDK recording")
    parser.add_argument(
        "--no-aligned-depth",
        action="store_true",
        help="do not save depth geometrically aligned to the color image",
    )
    parser.add_argument("--list-devices", action="store_true", help="show cameras and exit")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    for name in ("width", "height", "fps", "save_every"):
        if getattr(args, name) <= 0:
            raise ValueError("--{} must be greater than zero".format(name.replace("_", "-")))
    if args.frames < 0 or args.duration < 0 or args.warmup < 0:
        raise ValueError("--frames, --duration, and --warmup cannot be negative")
    if not 1 <= args.jpeg_quality <= 100:
        raise ValueError("--jpeg-quality must be between 1 and 100")
    if args.session_name and not re.fullmatch(r"[A-Za-z0-9_.-]+", args.session_name):
        raise ValueError("--session-name may contain only letters, digits, '.', '_' and '-'")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_session_name() -> str:
    return datetime.now(timezone.utc).strftime("d415_%Y%m%dT%H%M%S_%fZ")


def intrinsics_dict(intrinsics: Any) -> Dict[str, Any]:
    return {
        "width": int(intrinsics.width),
        "height": int(intrinsics.height),
        "fx": float(intrinsics.fx),
        "fy": float(intrinsics.fy),
        "ppx": float(intrinsics.ppx),
        "ppy": float(intrinsics.ppy),
        "distortion_model": str(intrinsics.model),
        "coefficients": [float(value) for value in intrinsics.coeffs],
    }


def extrinsics_dict(extrinsics: Any) -> Dict[str, Any]:
    return {
        "rotation": [float(value) for value in extrinsics.rotation],
        "translation_metres": [float(value) for value in extrinsics.translation],
    }


def profile_dict(profile: Any) -> Dict[str, Any]:
    video = profile.as_video_stream_profile()
    return {
        "stream": str(profile.stream_type()),
        "index": int(profile.stream_index()),
        "format": str(profile.format()),
        "fps": int(profile.fps()),
        "intrinsics": intrinsics_dict(video.get_intrinsics()),
    }


def make_directories(session_dir: Path, aligned_depth: bool) -> Dict[str, Path]:
    paths = {
        "color": session_dir / "color",
        "depth": session_dir / "depth",
        "infrared_left": session_dir / "infrared_left",
        "infrared_right": session_dir / "infrared_right",
    }
    if aligned_depth:
        paths["depth_aligned_to_color"] = session_dir / "depth_aligned_to_color"
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=False)
    return paths


def device_value(device: Any, rs: Any, field: Any) -> Optional[str]:
    try:
        return str(device.get_info(field)) if device.supports(field) else None
    except RuntimeError:
        return None


def device_dict(device: Any, rs: Any) -> Dict[str, Optional[str]]:
    return {
        "name": device_value(device, rs, rs.camera_info.name),
        "serial_number": device_value(device, rs, rs.camera_info.serial_number),
        "firmware_version": device_value(device, rs, rs.camera_info.firmware_version),
        "recommended_firmware_version": device_value(
            device, rs, rs.camera_info.recommended_firmware_version
        ),
        "usb_type_descriptor": device_value(device, rs, rs.camera_info.usb_type_descriptor),
        "product_id": device_value(device, rs, rs.camera_info.product_id),
    }


def query_devices(rs: Any) -> Iterable[Any]:
    return rs.context().query_devices()


def print_devices(rs: Any) -> int:
    devices = list(query_devices(rs))
    if not devices:
        print("No RealSense device detected.")
        return 1
    for index, device in enumerate(devices, start=1):
        details = device_dict(device, rs)
        print(
            "{index}. {name} | serial={serial} | firmware={firmware} | USB={usb}".format(
                index=index,
                name=details["name"],
                serial=details["serial_number"],
                firmware=details["firmware_version"],
                usb=details["usb_type_descriptor"],
            )
        )
    return 0


def choose_device(rs: Any, serial: Optional[str]) -> Tuple[Any, str]:
    devices = list(query_devices(rs))
    if not devices:
        raise RuntimeError("No RealSense camera found. Reconnect it to a USB 3 port and retry.")
    if serial:
        for device in devices:
            value = device_value(device, rs, rs.camera_info.serial_number)
            if value == serial:
                return device, serial
        raise RuntimeError("RealSense serial '{}' was not found.".format(serial))
    if len(devices) > 1:
        raise RuntimeError("Multiple cameras found; select one with --serial.")
    selected = devices[0]
    selected_serial = device_value(selected, rs, rs.camera_info.serial_number)
    if not selected_serial:
        raise RuntimeError("The camera did not report a serial number.")
    return selected, selected_serial


def make_config(rs: Any, args: argparse.Namespace, serial: str, bag_path: Optional[Path]) -> Any:
    config = rs.config()
    config.enable_device(serial)
    config.enable_stream(rs.stream.color, args.width, args.height, rs.format.bgr8, args.fps)
    config.enable_stream(rs.stream.depth, args.width, args.height, rs.format.z16, args.fps)
    config.enable_stream(
        rs.stream.infrared, 1, args.width, args.height, rs.format.y8, args.fps
    )
    config.enable_stream(
        rs.stream.infrared, 2, args.width, args.height, rs.format.y8, args.fps
    )
    if bag_path is not None:
        config.enable_record_to_file(str(bag_path.resolve()))
    return config


def set_emitter(device: Any, rs: Any, setting: str) -> None:
    if setting == "auto":
        return
    sensor = device.first_depth_sensor()
    if not sensor.supports(rs.option.emitter_enabled):
        raise RuntimeError("This camera does not expose the emitter control.")
    sensor.set_option(rs.option.emitter_enabled, 1.0 if setting == "on" else 0.0)


def active_calibration(profile: Any, rs: Any) -> Dict[str, Any]:
    profiles = {
        "color": profile.get_stream(rs.stream.color),
        "depth": profile.get_stream(rs.stream.depth),
        "infrared_left": profile.get_stream(rs.stream.infrared, 1),
        "infrared_right": profile.get_stream(rs.stream.infrared, 2),
    }
    calibration = {"streams": {name: profile_dict(value) for name, value in profiles.items()}}
    calibration["extrinsics"] = {
        "depth_to_color": extrinsics_dict(profiles["depth"].get_extrinsics_to(profiles["color"])),
        "infrared_left_to_right": extrinsics_dict(
            profiles["infrared_left"].get_extrinsics_to(profiles["infrared_right"])
        ),
        "infrared_left_to_color": extrinsics_dict(
            profiles["infrared_left"].get_extrinsics_to(profiles["color"])
        ),
    }
    return calibration


def write_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def save_image(cv2: Any, path: Path, image: Any, jpeg_quality: int) -> None:
    if path.suffix.lower() == ".jpg":
        options = [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
    else:
        options = [cv2.IMWRITE_PNG_COMPRESSION, 1]
    if not cv2.imwrite(str(path), image, options):
        raise OSError("OpenCV could not write '{}'".format(path))


def frame_fields(frame: Any, prefix: str) -> Dict[str, Any]:
    return {
        "{}_frame_number".format(prefix): int(frame.get_frame_number()),
        "{}_timestamp_ms".format(prefix): float(frame.get_timestamp()),
    }


def preview_frame(cv2: Any, color: Any, depth: Any, ir_left: Any, ir_right: Any, count: int) -> bool:
    import numpy as np

    depth_view = cv2.applyColorMap(
        cv2.convertScaleAbs(depth, alpha=0.03), cv2.COLORMAP_TURBO
    )
    left_view = cv2.cvtColor(ir_left, cv2.COLOR_GRAY2BGR)
    right_view = cv2.cvtColor(ir_right, cv2.COLOR_GRAY2BGR)
    top = np.hstack((color, depth_view))
    bottom = np.hstack((left_view, right_view))
    canvas = np.vstack((top, bottom))
    cv2.putText(
        canvas,
        "Saved: {} | Q/Esc: stop".format(count),
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.imshow("RealSense D415 collector", canvas)
    return (cv2.waitKey(1) & 0xFF) in (ord("q"), 27)


def capture(args: argparse.Namespace, rs: Any, cv2: Any) -> Path:
    import numpy as np

    selected, serial = choose_device(rs, args.serial)
    initial_device = device_dict(selected, rs)
    if "D415" not in (initial_device["name"] or ""):
        print(
            "Warning: expected a D415 but found '{}'. Stream support may differ.".format(
                initial_device["name"]
            ),
            file=sys.stderr,
        )

    # Start once without recording so auto-exposure can settle and warm-up frames
    # do not appear in the BAG or exported dataset.
    if args.warmup:
        warm_pipeline = rs.pipeline()
        try:
            warm_profile = warm_pipeline.start(make_config(rs, args, serial, None))
            set_emitter(warm_profile.get_device(), rs, args.emitter)
            for _ in range(args.warmup):
                warm_pipeline.wait_for_frames(5000)
        finally:
            try:
                warm_pipeline.stop()
            except RuntimeError:
                pass

    session_dir = args.output.expanduser().resolve() / (args.session_name or default_session_name())
    session_dir.mkdir(parents=True, exist_ok=False)
    paths = make_directories(session_dir, not args.no_aligned_depth)
    bag_path = None if args.no_bag else session_dir / "recording.bag"

    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "status": "starting",
        "started_at_utc": utc_now(),
        "device": initial_device,
        "requested_configuration": {
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "emitter": args.emitter,
            "raw_bag": not args.no_bag,
            "aligned_depth": not args.no_aligned_depth,
            "color_format": args.color_format,
            "save_every": args.save_every,
        },
        "saved_frames": 0,
        "incomplete_framesets": 0,
    }
    write_json(session_dir / "session.json", manifest)

    pipeline = rs.pipeline()
    started = False
    csv_handle = None
    stop_requested = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    old_sigint = signal.signal(signal.SIGINT, request_stop)
    start_monotonic = time.monotonic()
    seen_framesets = 0

    try:
        try:
            profile = pipeline.start(make_config(rs, args, serial, bag_path))
        except RuntimeError as error:
            raise RuntimeError(
                "Could not start all four streams at {}x{} @ {} FPS. Check USB 3, "
                "firmware, and supported profiles with 'rs-enumerate-devices -c'. "
                "Original error: {}".format(args.width, args.height, args.fps, error)
            ) from error
        started = True
        device = profile.get_device()
        set_emitter(device, rs, args.emitter)
        depth_scale = float(device.first_depth_sensor().get_depth_scale())
        manifest.update(
            {
                "status": "recording",
                "device": device_dict(device, rs),
                "depth_scale_metres_per_unit": depth_scale,
                "calibration": active_calibration(profile, rs),
                "file_layout": {
                    "color": "color/NNNNNN.{}".format(args.color_format),
                    "depth": "depth/NNNNNN.png (uint16 raw units)",
                    "depth_aligned_to_color": (
                        "depth_aligned_to_color/NNNNNN.png (uint16 raw units)"
                        if not args.no_aligned_depth
                        else None
                    ),
                    "infrared_left": "infrared_left/NNNNNN.png (uint8)",
                    "infrared_right": "infrared_right/NNNNNN.png (uint8)",
                    "timestamps": "frames.csv",
                    "raw_recording": "recording.bag" if bag_path else None,
                },
            }
        )
        write_json(session_dir / "session.json", manifest)

        align = None if args.no_aligned_depth else rs.align(rs.stream.color)
        csv_handle = (session_dir / "frames.csv").open("w", encoding="utf-8", newline="")
        csv_writer = csv.DictWriter(csv_handle, fieldnames=CSV_FIELDS)
        csv_writer.writeheader()

        print("Recording to {}".format(session_dir))
        print("Depth scale: {} metres/raw unit".format(depth_scale))
        if not args.no_preview:
            cv2.namedWindow("RealSense D415 collector", cv2.WINDOW_NORMAL)

        while not stop_requested:
            elapsed = time.monotonic() - start_monotonic
            if args.duration and elapsed >= args.duration:
                break
            if args.frames and manifest["saved_frames"] >= args.frames:
                break

            frames = pipeline.wait_for_frames(5000)
            seen_framesets += 1
            color_frame = frames.get_color_frame()
            depth_frame = frames.get_depth_frame()
            left_frame = frames.get_infrared_frame(1)
            right_frame = frames.get_infrared_frame(2)
            if not all((color_frame, depth_frame, left_frame, right_frame)):
                manifest["incomplete_framesets"] += 1
                continue

            color = np.asanyarray(color_frame.get_data())
            depth = np.asanyarray(depth_frame.get_data())
            ir_left = np.asanyarray(left_frame.get_data())
            ir_right = np.asanyarray(right_frame.get_data())

            should_save = (seen_framesets - 1) % args.save_every == 0
            if should_save:
                index = int(manifest["saved_frames"])
                stem = "{:06d}".format(index)
                save_image(
                    cv2,
                    paths["color"] / "{}.{}".format(stem, args.color_format),
                    color,
                    args.jpeg_quality,
                )
                save_image(cv2, paths["depth"] / "{}.png".format(stem), depth, args.jpeg_quality)
                save_image(
                    cv2,
                    paths["infrared_left"] / "{}.png".format(stem),
                    ir_left,
                    args.jpeg_quality,
                )
                save_image(
                    cv2,
                    paths["infrared_right"] / "{}.png".format(stem),
                    ir_right,
                    args.jpeg_quality,
                )
                if align is not None:
                    aligned = align.process(frames).get_depth_frame()
                    if not aligned:
                        raise RuntimeError("Depth-to-color alignment returned no depth frame.")
                    aligned_depth = np.asanyarray(aligned.get_data())
                    save_image(
                        cv2,
                        paths["depth_aligned_to_color"] / "{}.png".format(stem),
                        aligned_depth,
                        args.jpeg_quality,
                    )

                row: Dict[str, Any] = {
                    "saved_index": index,
                    "host_time_utc_ns": time.time_ns(),
                    "elapsed_seconds": round(time.monotonic() - start_monotonic, 9),
                    "frameset_number": int(frames.get_frame_number()),
                    "timestamp_domain": str(color_frame.get_frame_timestamp_domain()),
                }
                row.update(frame_fields(color_frame, "color"))
                row.update(frame_fields(depth_frame, "depth"))
                row.update(frame_fields(left_frame, "infrared_left"))
                row.update(frame_fields(right_frame, "infrared_right"))
                csv_writer.writerow(row)
                csv_handle.flush()
                manifest["saved_frames"] += 1

            if not args.no_preview and preview_frame(
                cv2, color, depth, ir_left, ir_right, int(manifest["saved_frames"])
            ):
                break

        manifest["status"] = "complete"
    except BaseException as error:
        manifest["status"] = "failed"
        manifest["error"] = "{}: {}".format(type(error).__name__, error)
        raise
    finally:
        if csv_handle is not None:
            csv_handle.close()
        if started:
            try:
                pipeline.stop()
            except RuntimeError as stop_error:
                manifest["stop_warning"] = str(stop_error)
        if not args.no_preview:
            cv2.destroyAllWindows()
        signal.signal(signal.SIGINT, old_sigint)
        manifest["ended_at_utc"] = utc_now()
        manifest["elapsed_seconds"] = round(time.monotonic() - start_monotonic, 6)
        manifest["seen_framesets"] = seen_framesets
        write_json(session_dir / "session.json", manifest)

    print("Saved {} frame sets in {}".format(manifest["saved_frames"], session_dir))
    return session_dir


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_args(args)
    except ValueError as error:
        parser.error(str(error))

    try:
        import pyrealsense2 as rs
    except ImportError:
        print("pyrealsense2 is not installed. Run ./install.sh first.", file=sys.stderr)
        return 2

    if args.list_devices:
        return print_devices(rs)

    try:
        import cv2

        capture(args, rs, cv2)
        return 0
    except (OSError, RuntimeError) as error:
        print("Error: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
