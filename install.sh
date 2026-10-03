#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -r /etc/os-release ]]; then
    echo "Error: /etc/os-release is missing. This installer is for Ubuntu." >&2
    exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "Error: this installer supports Ubuntu, detected '${ID:-unknown}'." >&2
    exit 1
fi

case "${VERSION_ID:-}" in
    20.04|22.04|24.04|26.04) ;;
    *)
        echo "Error: Ubuntu 20.04, 22.04, 24.04, or 26.04 is required; detected '${VERSION_ID:-unknown}'." >&2
        exit 1
        ;;
esac

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=()
elif command -v sudo >/dev/null 2>&1; then
    SUDO=(sudo)
else
    echo "Error: sudo is required for system package installation." >&2
    exit 1
fi

echo "Installing OS dependencies for Ubuntu ${VERSION_ID}..."
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y \
    apt-transport-https ca-certificates curl gnupg lsb-release \
    python3 python3-pip python3-venv libgl1 libglib2.0-0

echo "Configuring the official RealSense package repository..."
"${SUDO[@]}" install -d -m 0755 /etc/apt/keyrings
key_tmp="$(mktemp)"
trap 'rm -f -- "$key_tmp"' EXIT
curl --fail --silent --show-error \
    https://librealsense.realsenseai.com/Debian/librealsenseai.asc \
    -o "$key_tmp"
gpg --dearmor < "$key_tmp" | "${SUDO[@]}" tee \
    /etc/apt/keyrings/librealsenseai.gpg >/dev/null
"${SUDO[@]}" chmod 0644 /etc/apt/keyrings/librealsenseai.gpg

codename="${VERSION_CODENAME:-$(lsb_release -cs)}"
repo_line="deb [signed-by=/etc/apt/keyrings/librealsenseai.gpg] https://librealsense.realsenseai.com/Debian/apt-repo ${codename} main"
printf '%s\n' "$repo_line" | "${SUDO[@]}" tee \
    /etc/apt/sources.list.d/librealsense.list >/dev/null
"${SUDO[@]}" apt-get update

# The D415 is supported by Ubuntu's stock uvcvideo driver. The utils package
# also installs the runtime and udev rules, without replacing a working kernel
# module with DKMS.
"${SUDO[@]}" apt-get install -y librealsense2-utils librealsense2-udev-rules

echo "Creating Python environment..."
python3 -m venv "${PROJECT_DIR}/.venv"
"${PROJECT_DIR}/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"${PROJECT_DIR}/.venv/bin/python" -m pip install -r "${PROJECT_DIR}/requirements.txt"

echo
echo "Installation complete. Unplug and reconnect the D415, then run:"
echo "  ${PROJECT_DIR}/.venv/bin/python ${PROJECT_DIR}/capture_realsense.py --list-devices"
echo "  ${PROJECT_DIR}/.venv/bin/python ${PROJECT_DIR}/capture_realsense.py"

