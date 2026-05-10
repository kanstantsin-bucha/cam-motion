#!/usr/bin/env bash
set -euo pipefail

# NAS connection — override via environment variables before running:
#   NAS_HOST=192.168.1.100 NAS_SHARE=/volume1/security NAS_TYPE=nfs sudo bash install.sh
NAS_HOST="${NAS_HOST:-nas-host}"
NAS_SHARE="${NAS_SHARE:-/volume1/security-cam}"
NAS_TYPE="${NAS_TYPE:-nfs}"          # "nfs" or "smb"
MOUNT_POINT="/mnt/nas/security-cam"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# 1. Install motion
# ---------------------------------------------------------------------------
echo "==> Installing motion"
apt-get update -qq
apt-get install -y motion

# ---------------------------------------------------------------------------
# 2. Install uv to /usr/local/bin
# ---------------------------------------------------------------------------
echo "==> Installing uv"
curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

# ---------------------------------------------------------------------------
# 3. Install Python 3.11 via uv
# ---------------------------------------------------------------------------
echo "==> Installing Python 3.11 via uv"
uv python install 3.11

# ---------------------------------------------------------------------------
# 4. Create /etc/cam_motion/ and populate it
# ---------------------------------------------------------------------------
echo "==> Creating config directory"
mkdir -p /etc/cam_motion

echo "==> Reading camera name from config.toml"
CAMERA_NAME=$(uv run python3 -c "import tomllib; c=tomllib.load(open('${SCRIPT_DIR}/config.toml','rb')); print(c['camera']['name'])")
echo "  Camera name: ${CAMERA_NAME}"

echo "==> Installing motion.conf (substituting camera name)"
sed "s/CAMERA_NAME/${CAMERA_NAME}/g" "${SCRIPT_DIR}/motion.conf" > /etc/cam_motion/motion.conf

echo "==> Installing config.toml"
cp "${SCRIPT_DIR}/config.toml" /etc/cam_motion/config.toml

# ---------------------------------------------------------------------------
# 5. Create /var/log/cam_motion/ with motion:motion ownership
# ---------------------------------------------------------------------------
echo "==> Creating log directory"
mkdir -p /var/log/cam_motion
chown motion:motion /var/log/cam_motion

# ---------------------------------------------------------------------------
# 6. Install cam_notifier.py
# ---------------------------------------------------------------------------
echo "==> Installing cam_notifier.py"
cp "${SCRIPT_DIR}/cam_notifier.py" /usr/local/bin/cam_notifier.py
chmod +x /usr/local/bin/cam_notifier.py

# ---------------------------------------------------------------------------
# 7. Create NAS mount point with motion:motion ownership
# ---------------------------------------------------------------------------
echo "==> Creating NAS mount point: ${MOUNT_POINT}"
mkdir -p "${MOUNT_POINT}"
chown motion:motion "${MOUNT_POINT}"

# ---------------------------------------------------------------------------
# 8. Add fstab entry and mount
# ---------------------------------------------------------------------------
echo "==> Adding NAS mount to /etc/fstab"
if [ "${NAS_TYPE}" = "nfs" ]; then
    FSTAB_ENTRY="${NAS_HOST}:${NAS_SHARE} ${MOUNT_POINT} nfs defaults,_netdev,auto 0 0"
elif [ "${NAS_TYPE}" = "smb" ]; then
    FSTAB_ENTRY="//${NAS_HOST}${NAS_SHARE} ${MOUNT_POINT} cifs defaults,_netdev,auto 0 0"
else
    echo "  ERROR: NAS_TYPE must be 'nfs' or 'smb'"
    exit 1
fi

if grep -qF "${MOUNT_POINT}" /etc/fstab; then
    echo "  fstab entry already exists, skipping"
else
    echo "${FSTAB_ENTRY}" >> /etc/fstab
    echo "  Added: ${FSTAB_ENTRY}"
fi

echo "==> Mounting NAS"
mount "${MOUNT_POINT}" || echo "  Warning: mount failed — verify NAS is reachable and fstab entry is correct"

# ---------------------------------------------------------------------------
# 9. Install and enable systemd service
# ---------------------------------------------------------------------------
echo "==> Installing systemd service"
cp "${SCRIPT_DIR}/opensecuritycam.service" /etc/systemd/system/opensecuritycam.service
systemctl daemon-reload
systemctl enable opensecuritycam
systemctl start opensecuritycam

echo ""
echo "==> Done. Service status:"
systemctl status opensecuritycam --no-pager
