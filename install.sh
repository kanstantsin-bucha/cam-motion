#!/usr/bin/env bash
set -euo pipefail

# NAS connection — override via environment variables before running:
#   NAS_HOST=192.168.1.100 NAS_SHARE=/volume1/security NAS_TYPE=nfs sudo bash install.sh
#   NAS_HOST=192.168.1.100 NAS_SHARE=/NAS NAS_TYPE=smb SMB_USER=user SMB_PASS=pass sudo bash install.sh
NAS_HOST="${NAS_HOST:-}"
NAS_SHARE="${NAS_SHARE:-}"
NAS_TYPE="${NAS_TYPE:-nfs}"          # "nfs" or "smb"
SMB_USER="${SMB_USER:-}"             # SMB only — leave empty to skip credentials file
SMB_PASS="${SMB_PASS:-}"
MOUNT_POINT="/mnt/nas/security-cam"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# 1. Install motion
# ---------------------------------------------------------------------------
echo "==> Installing motion"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y motion nfs-common cifs-utils curl
id motion &>/dev/null || { echo "ERROR: 'motion' user not found after install"; exit 1; }

# ---------------------------------------------------------------------------
# 2. Create /etc/cam_motion/ and populate it
# ---------------------------------------------------------------------------
echo "==> Creating config directory"
mkdir -p /etc/cam_motion

echo "==> Reading camera name from config.toml"
CAMERA_NAME=$(sed -n '/^\[camera\]/,/^\[/{ s/^[[:space:]]*name[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p }' \
  "${SCRIPT_DIR}/config.toml" | head -1)
[ -n "${CAMERA_NAME}" ] || { echo "ERROR: could not read camera.name from config.toml"; exit 1; }
echo "  Camera name: ${CAMERA_NAME}"

echo "==> Installing motion.conf (substituting camera name)"
SAFE_NAME=$(printf '%s\n' "${CAMERA_NAME}" | sed 's/[&|/\]/\\&/g')
sed "s|CAMERA_NAME|${SAFE_NAME}|g" "${SCRIPT_DIR}/motion.conf" > /etc/cam_motion/motion.conf

echo "==> Installing config.toml"
cp "${SCRIPT_DIR}/config.toml" /etc/cam_motion/config.toml

# ---------------------------------------------------------------------------
# 3. Create /var/log/cam_motion/ with motion:motion ownership
# ---------------------------------------------------------------------------
echo "==> Creating log directory"
mkdir -p /var/log/cam_motion
chown motion:motion /var/log/cam_motion

# ---------------------------------------------------------------------------
# 4. Install cam_notifier.sh
# ---------------------------------------------------------------------------
echo "==> Installing cam_notifier.sh"
cp "${SCRIPT_DIR}/cam_notifier.sh" /usr/local/bin/cam_notifier.sh
chmod +x /usr/local/bin/cam_notifier.sh

# ---------------------------------------------------------------------------
# 5. Create NAS mount point
# ---------------------------------------------------------------------------
echo "==> Creating NAS mount point: ${MOUNT_POINT}"
mkdir -p "${MOUNT_POINT}"
# Note: do NOT chown the local mount point — the NFS/SMB mount overlays it.
# Write access must be configured on the NAS side via export options or share permissions.

# ---------------------------------------------------------------------------
# 6. Add fstab entry and mount
# ---------------------------------------------------------------------------
if [ -z "${NAS_HOST}" ] || [ -z "${NAS_SHARE}" ]; then
    echo "==> NAS_HOST/NAS_SHARE not set — skipping fstab (re-run with NAS_HOST=... NAS_SHARE=... to configure)"
else
    echo "==> Adding NAS mount to /etc/fstab"
    MOTION_UID=$(id -u motion)
    MOTION_GID=$(id -g motion)
    if [ "${NAS_TYPE}" = "nfs" ]; then
        FSTAB_ENTRY="${NAS_HOST}:${NAS_SHARE} ${MOUNT_POINT} nfs defaults,_netdev,auto 0 0"
    elif [ "${NAS_TYPE}" = "smb" ]; then
        SMB_CREDS_OPT=""
        if [ -n "${SMB_USER}" ] && [ -n "${SMB_PASS}" ]; then
            echo "==> Writing SMB credentials file"
            SMB_CREDS="/etc/cam_motion/smb-credentials"
            printf 'username=%s\npassword=%s\n' "${SMB_USER}" "${SMB_PASS}" > "${SMB_CREDS}"
            chmod 600 "${SMB_CREDS}"
            echo "  Written: ${SMB_CREDS}"
            SMB_CREDS_OPT=",credentials=${SMB_CREDS}"
        fi
        FSTAB_ENTRY="//${NAS_HOST}${NAS_SHARE} ${MOUNT_POINT} cifs uid=${MOTION_UID},gid=${MOTION_GID}${SMB_CREDS_OPT},_netdev,auto 0 0"
    else
        echo "  ERROR: NAS_TYPE must be 'nfs' or 'smb'"
        exit 1
    fi

    # Remove any existing entry for this mount point, then write the current one
    sed -i "\|${MOUNT_POINT}|d" /etc/fstab
    echo "${FSTAB_ENTRY}" >> /etc/fstab
    echo "  Written: ${FSTAB_ENTRY}"

    echo "==> Mounting NAS"
    mountpoint -q "${MOUNT_POINT}" \
        || mount "${MOUNT_POINT}" \
        || echo "  Warning: mount failed — verify NAS is reachable and fstab entry is correct"
fi

# ---------------------------------------------------------------------------
# 7. Install and enable systemd service
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
