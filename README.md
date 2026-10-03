# RealSense D415 data collector

Collect synchronized data from all imaging sensors on an Intel RealSense D415:

- RGB/color (the single conventional camera), as lossless PNG by default
- raw 16-bit depth and depth aligned to RGB
- left and right 8-bit infrared images (the stereo pair)
- camera intrinsics, distortion, inter-camera extrinsics, depth scale, device/firmware details
- per-stream frame numbers and hardware timestamps
- a RealSense `.bag` recording containing the original streams

The exported PNG files load directly in OpenCV, while the calibration and lossless depth make the sessions suitable for RGB-D human detection and custom ML datasets. Object/person labels are **not** generated; add annotations with a tool such as CVAT or Label Studio before supervised training.

## 1. Install on a new Ubuntu PC

Supported targets are Ubuntu 20.04, 22.04, 24.04 and 26.04 LTS on x86-64 or ARM64. Use a native Ubuntu installation and a USB 3 port; virtual-machine USB forwarding is unreliable for RealSense bandwidth.

```bash
git clone https://github.com/kartikeyagg/realsenseDataCollector.git
cd realsenseDataCollector
chmod +x install.sh
./install.sh
```

The installer adds the official RealSense APT repository, installs the viewer/runtime/udev permissions, and creates `.venv`. It deliberately does not install the DKMS kernel replacement: D415 support is already in the stock Ubuntu driver, and avoiding DKMS makes the setup much more portable across LTS kernel updates and Secure Boot systems.

Ubuntu 20.04 uses Python 3.8, so `requirements.txt` selects the last compatible RealSense Python wheel (`2.55.1.6486`). Ubuntu 22.04–26.04 select a current wheel matching the OS Python.

After installation, unplug/reconnect the camera and verify it:

```bash
source .venv/bin/activate
python capture_realsense.py --list-devices
rs-enumerate-devices -s
realsense-viewer
```

Update camera firmware from `realsense-viewer` if its recommended and installed versions differ. Do not connect through an unpowered hub.

## 2. Capture data

Start the default reliable profile (640×480, 30 FPS):

```bash
source .venv/bin/activate
python capture_realsense.py
```

The live 2×2 view shows RGB, depth, left IR and right IR. Press `q`, Escape, or Ctrl+C to finish cleanly. A non-interactive ten-minute recording is:

```bash
python capture_realsense.py --no-preview --duration 600 --output /mnt/fast-ssd/d415-data
```

Useful variants:

```bash
# Stop after 1,000 exported synchronized samples
python capture_realsense.py --frames 1000

# Request the D415 high-resolution profile (requires USB 3 and compatible firmware)
python capture_realsense.py --width 1280 --height 720 --fps 30

# Capture every frame in the BAG but export only every fifth frame for labeling
python capture_realsense.py --save-every 5

# Natural-light stereo images without the projector pattern
python capture_realsense.py --emitter off

# Pick a camera in a multi-camera setup
python capture_realsense.py --serial 123456789

# Save individual images only, with smaller lossy RGB files
python capture_realsense.py --no-bag --color-format jpg --jpeg-quality 95
```

The `.bag` is enabled by default because it preserves all device frames and metadata even if PNG compression cannot keep pace with the camera. Use a fast SSD. Individual files plus the BAG can consume tens of gigabytes per hour.

Each session is self-contained:

```text
data/d415_...Z/
├── color/                       # BGR PNG/JPEG, uint8
├── depth/                       # native stereo geometry, uint16
├── depth_aligned_to_color/      # matches color pixels, uint16
├── infrared_left/               # rectified stereo left, uint8
├── infrared_right/              # rectified stereo right, uint8
├── frames.csv                   # frame numbers and timestamps
├── recording.bag                # original SDK stream recording
└── session.json                 # calibration, depth scale, device and run summary
```

A raw depth pixel is not millimetres by assumption. Convert it with the recorded scale:

```python
depth_metres = depth_raw.astype("float32") * session["depth_scale_metres_per_unit"]
```

Zero depth is invalid/missing. For detections made in the RGB image, use `depth_aligned_to_color`, not native `depth`, so the bounding-box pixels refer to the same rays.

## OpenCV and ML example

Load a saved RGB-D pair and build a four-channel RGB-D NumPy tensor:

```bash
python examples/read_sample.py data/d415_20261003T120000_000000Z --index 0 --show
```

For a typical human-detection workflow:

1. Label people on images in `color/` (YOLO, COCO, or another format).
2. Split by recording session, not random neighboring frames, to prevent train/test leakage.
3. Train a normal RGB detector first; use aligned depth as a fourth channel or fuse it after detection.
4. Preserve `session.json` with every derived dataset so depth and geometry remain interpretable.
5. Obtain consent and protect recorded biometric/person data.

The left/right infrared images are already rectified for stereo use. Keep their filenames paired. With `--emitter on` or the default camera behavior, the left image may contain the projector dot pattern; `--emitter off` is preferable when training models on natural IR appearance, while the projector usually improves depth on textureless surfaces.

## Troubleshooting

- **No device / permission denied:** reconnect after installation, try a direct USB 3 port, then run `rs-enumerate-devices`. Check that `/etc/udev/rules.d/99-realsense-libusb.rules` exists.
- **USB reports 2.x:** replace the cable/port. Four simultaneous streams need USB 3 bandwidth.
- **Couldn't resolve requests:** inspect supported combinations with `rs-enumerate-devices -c`, then select one common resolution/FPS. Start with 640×480 at 30 FPS.
- **Frames stop or corrupt:** write to a local SSD, close `realsense-viewer`, and ensure no other process has opened the camera.
- **Black/dotted IR or weak depth:** test emitter `on`, `off`, and `auto`; avoid direct sunlight for active depth.
- **Secure Boot/DKMS errors:** this project does not require DKMS for a D415. Remove a partially installed `librealsense2-dkms` package before retrying the normal installer.

## Development checks

Tests do not require camera hardware:

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest -q
python -m py_compile capture_realsense.py examples/read_sample.py
```

Official references: [Linux packages](https://github.com/realsenseai/librealsense/blob/master/doc/distribution_linux.md), [source/driver installation](https://github.com/realsenseai/librealsense/blob/master/doc/installation.md), and [Python wrapper](https://github.com/realsenseai/librealsense/blob/master/wrappers/python/readme.md).
