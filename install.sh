#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Update MEDIAMTX_VERSION to the latest arm64 release from:
# https://github.com/bluenviron/mediamtx/releases
MEDIAMTX_VERSION="v1.18.2"
MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_arm64.tar.gz"

# ---------------------------------------------------------------------------
# 1. Install dependencies
# ---------------------------------------------------------------------------
echo "==> Installing dependencies"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg

# ---------------------------------------------------------------------------
# 2. Create cam system user and add to video group
# ---------------------------------------------------------------------------
echo "==> Creating cam system user"
id cam &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin cam
usermod -aG video cam

# ---------------------------------------------------------------------------
# 3. Create /etc/cam_motion/ and populate it
# ---------------------------------------------------------------------------
echo "==> Creating config directory"
mkdir -p /etc/cam_motion

echo "==> Installing mediamtx.yml"
cp "${SCRIPT_DIR}/mediamtx.yml" /etc/cam_motion/mediamtx.yml

echo "==> Installing config.toml"
cp "${SCRIPT_DIR}/config.toml" /etc/cam_motion/config.toml

# ---------------------------------------------------------------------------
# 4. Create /var/log/cam_motion/ owned by cam
# ---------------------------------------------------------------------------
echo "==> Creating log directory"
mkdir -p /var/log/cam_motion
chown cam:cam /var/log/cam_motion

# ---------------------------------------------------------------------------
# 5. Download and install mediamtx binary
# ---------------------------------------------------------------------------
echo "==> Installing mediamtx ${MEDIAMTX_VERSION}"
TMP=$(mktemp -d)
wget -qO- "${MEDIAMTX_URL}" | tar -xz -C "${TMP}"
install -m 755 "${TMP}/mediamtx" /usr/local/bin/mediamtx
rm -rf "${TMP}"
echo "  Installed: $(mediamtx --version 2>&1 | head -1)"

# ---------------------------------------------------------------------------
# 6. Install and enable systemd service
# ---------------------------------------------------------------------------
echo "==> Installing systemd service"
cp "${SCRIPT_DIR}/opensecuritycam.service" /etc/systemd/system/opensecuritycam.service
systemctl daemon-reload
systemctl enable opensecuritycam
systemctl is-active --quiet opensecuritycam \
    && systemctl restart opensecuritycam \
    || systemctl start opensecuritycam

echo ""
echo "==> Done. Service status:"
systemctl status opensecuritycam --no-pager

echo ""
echo "==> RTSP streams (available in a few seconds):"
echo "    Main:  rtsp://$(hostname -I | awk '{print $1}'):554/h264Preview_01_main"
echo "    Sub:   rtsp://$(hostname -I | awk '{print $1}'):554/h264Preview_01_sub"
