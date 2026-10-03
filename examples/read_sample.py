#!/usr/bin/env python3
"""Load one exported RGB-D sample with OpenCV and convert depth to metres."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    with (args.session / "session.json").open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    stem = "{:06d}".format(args.index)
    color_pattern = metadata["file_layout"]["color"]
    color_suffix = Path(color_pattern.split("/")[-1].replace("NNNNNN", stem)).suffix
    color = cv2.imread(str(args.session / "color" / (stem + color_suffix)), cv2.IMREAD_COLOR)
    depth_raw = cv2.imread(
        str(args.session / "depth_aligned_to_color" / (stem + ".png")),
        cv2.IMREAD_UNCHANGED,
    )
    if color is None or depth_raw is None:
        raise SystemExit("Sample is missing, or aligned-depth export was disabled.")

    depth_metres = depth_raw.astype(np.float32) * metadata["depth_scale_metres_per_unit"]
    valid = depth_metres > 0
    print("color:", color.shape, color.dtype)
    print("depth:", depth_metres.shape, depth_metres.dtype, "metres")
    if np.any(valid):
        print("valid depth range: {:.3f} to {:.3f} m".format(depth_metres[valid].min(), depth_metres[valid].max()))

    # This 4-channel tensor is a convenient input for custom RGB-D models.
    rgb = cv2.cvtColor(color, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    rgbd = np.dstack((rgb, depth_metres))
    print("RGB-D tensor:", rgbd.shape, rgbd.dtype)

    if args.show:
        depth_view = cv2.applyColorMap(
            cv2.convertScaleAbs(depth_raw, alpha=0.03), cv2.COLORMAP_TURBO
        )
        cv2.imshow("color | aligned depth", np.hstack((color, depth_view)))
        cv2.waitKey(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

