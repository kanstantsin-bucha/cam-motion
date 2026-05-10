# OpenSecurityCam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Raspberry Pi motion-detection system that records 1-minute MP4 clips to a NAS mount and fires a webhook immediately when each new clip begins.

**Architecture:** The `motion` daemon (installed via apt) handles Pi CSI camera capture, frame differencing, and MP4 recording to an NFS/SMB-mounted NAS path. Its `on_movie_start` hook invokes `cam_notifier.py` at the instant each clip begins recording — not after it finishes. The notifier reads `/etc/cam_motion/config.toml` and POSTs a JSON payload to a configurable webhook URL.

**Tech Stack:** Python 3.11 (stdlib only: `tomllib`, `urllib.request`, `logging`), `motion` 4.x (apt), systemd, NFS/SMB mount via `/etc/fstab`

---

## File Map

| File | Installed to | Purpose |
|---|---|---|
| `config.toml` | `/etc/cam_motion/config.toml` | Camera name and webhook settings |
| `motion.conf` | `/etc/cam_motion/motion.conf` | motion daemon: camera, sensitivity, output, hooks |
| `cam_notifier.py` | `/usr/local/bin/cam_notifier.py` | Webhook notifier invoked by `on_movie_start` |
| `tests/test_cam_notifier.py` | dev only | Unit tests for notifier logic |
| `opensecuritycam.service` | `/etc/systemd/system/` | systemd unit — starts motion on boot |
| `install.sh` | run on Pi as root | Installs all of the above |

---

### Task 1: Create `config.toml`

**Files:**
- Create: `config.toml`

- [ ] **Step 1: Create `config.toml`**

```toml
[camera]
name = "front-door"

[webhook]
url = "http://nas-host/api/motion"
timeout_seconds = 5
```

- [ ] **Step 2: Commit**

```bash
git add config.toml
git commit -m "feat: add notifier config"
```

---

### Task 2: Create `motion.conf`

**Files:**
- Create: `motion.conf`

The camera name in `movie_filename` uses the placeholder `CAMERA_NAME` — `install.sh` replaces it with the value from `config.toml` before copying to `/etc/cam_motion/`.

- [ ] **Step 1: Create `motion.conf`**

```conf
# /etc/cam_motion/motion.conf
# OpenSecurityCam — motion daemon configuration

############################################################
# Camera input
############################################################

# CSI camera exposed via v4l2 bridge on Raspberry Pi OS Bookworm
videodevice /dev/video0

# Resolution and framerate
width 1280
height 720
framerate 15

############################################################
# Motion detection — tune these for your environment
############################################################

# Number of changed pixels required to trigger motion (lower = more sensitive)
threshold 1500

# Noise filter level (0-255, higher = less sensitive to noise)
noise_level 32

# Consecutive frames with motion required to confirm detection (reduce false triggers)
minimum_motion_frames 2

# Seconds of no motion before the current event is closed
event_gap 60

############################################################
# Movie output
############################################################

output_movies on
movie_codec mp4

# Filename pattern: timestamp then camera name (motion appends .mp4 automatically)
# CAMERA_NAME is replaced by install.sh using the value from config.toml
movie_filename %Y%m%d-%H%M%S-CAMERA_NAME

# Output directory — must be the NAS mount point
target_dir /mnt/nas/security-cam

# Force a new file every 60 seconds while motion persists
max_movie_time 60

############################################################
# Hooks
############################################################

# Fires immediately when a new clip starts recording (not when it finishes)
# %f = full path to clip file, %t = timestamp string, %v = event number
on_movie_start python3 /usr/local/bin/cam_notifier.py %f "%t" %v

############################################################
# Logging
############################################################

log_file /var/log/cam_motion/motion.log

# 6=notice (recommended), 7=info, 8=debug
log_level 6
```

- [ ] **Step 2: Commit**

```bash
git add motion.conf
git commit -m "feat: add motion daemon config"
```

---

### Task 3: Implement `cam_notifier.py` (TDD)

**Files:**
- Create: `tests/test_cam_notifier.py`
- Create: `cam_notifier.py`

- [ ] **Step 1: Create `tests/test_cam_notifier.py` with failing tests**

```python
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import cam_notifier


CONFIG_TOML = b"""
[camera]
name = "front-door"

[webhook]
url = "http://nas-host/api/motion"
timeout_seconds = 5
"""


class TestLoadConfig(unittest.TestCase):
    def test_loads_camera_and_webhook(self):
        with patch("builtins.open", mock_open(read_data=CONFIG_TOML)):
            config = cam_notifier.load_config(Path("/fake/config.toml"))
        self.assertEqual(config["camera"]["name"], "front-door")
        self.assertEqual(config["webhook"]["url"], "http://nas-host/api/motion")
        self.assertEqual(config["webhook"]["timeout_seconds"], 5)


class TestBuildPayload(unittest.TestCase):
    def test_payload_fields(self):
        config = {
            "camera": {"name": "front-door"},
            "webhook": {"url": "http://x", "timeout_seconds": 5},
        }
        payload = cam_notifier.build_payload(
            config,
            filepath="/mnt/nas/security-cam/20260510-143200-front-door.mp4",
            sequence=3,
        )
        self.assertEqual(payload["camera"], "front-door")
        self.assertEqual(payload["sequence"], 3)
        self.assertEqual(payload["clip"], "20260510-143200-front-door.mp4")
        self.assertTrue(payload["timestamp"].endswith("Z"))

    def test_sequence_coerced_to_int(self):
        config = {"camera": {"name": "cam"}, "webhook": {}}
        payload = cam_notifier.build_payload(config, "/some/path/file.mp4", sequence="7")
        self.assertEqual(payload["sequence"], 7)
        self.assertIsInstance(payload["sequence"], int)

    def test_clip_is_filename_only(self):
        config = {"camera": {"name": "cam"}, "webhook": {}}
        payload = cam_notifier.build_payload(
            config, "/deep/nested/path/20260510-143200-cam.mp4", sequence=1
        )
        self.assertEqual(payload["clip"], "20260510-143200-cam.mp4")


class TestPostWebhook(unittest.TestCase):
    def test_posts_json_to_url(self):
        payload = {"camera": "front-door", "sequence": 1}
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            status = cam_notifier.post_webhook("http://nas-host/api/motion", payload, timeout=5)

        self.assertEqual(status, 200)
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://nas-host/api/motion")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(req.data), payload)

    def test_uses_correct_timeout(self):
        mock_response = MagicMock()
        mock_response.status = 204
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            cam_notifier.post_webhook("http://x", {}, timeout=10)

        self.assertEqual(mock_urlopen.call_args[1]["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest tests/test_cam_notifier.py -v
```

Expected: `ModuleNotFoundError: No module named 'cam_notifier'`

- [ ] **Step 3: Create `cam_notifier.py`**

```python
#!/usr/bin/env python3
import json
import logging
import sys
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path("/etc/cam_motion/config.toml")
LOG_PATH = Path("/var/log/cam_motion/notifier.log")


def setup_logging(log_path: Path) -> None:
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def load_config(config_path: Path) -> dict:
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def build_payload(config: dict, filepath: str, sequence) -> dict:
    return {
        "camera": config["camera"]["name"],
        "timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        "sequence": int(sequence),
        "clip": Path(filepath).name,
    }


def post_webhook(url: str, payload: dict, timeout: int) -> int:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def main() -> None:
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <filepath> <motion_timestamp> <event_number>", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    sequence = sys.argv[3]

    setup_logging(LOG_PATH)

    try:
        config = load_config(CONFIG_PATH)
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        sys.exit(1)

    payload = build_payload(config, filepath, sequence)
    url = config["webhook"]["url"]
    timeout = config["webhook"].get("timeout_seconds", 5)

    try:
        status = post_webhook(url, payload, timeout)
        logging.info(f"Webhook OK status={status} clip={payload['clip']} seq={payload['sequence']}")
    except urllib.error.URLError as e:
        logging.error(f"Webhook failed: {e} clip={payload['clip']}")
    except Exception as e:
        logging.error(f"Unexpected error: {e} clip={payload['clip']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python3 -m pytest tests/test_cam_notifier.py -v
```

Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add cam_notifier.py tests/test_cam_notifier.py
git commit -m "feat: add cam_notifier with webhook POST"
```

---

### Task 4: Create `opensecuritycam.service`

**Files:**
- Create: `opensecuritycam.service`

- [ ] **Step 1: Create `opensecuritycam.service`**

```ini
[Unit]
Description=OpenSecurityCam motion detection service
After=network.target remote-fs.target
Requires=remote-fs.target

[Service]
Type=simple
ExecStart=/usr/bin/motion -c /etc/cam_motion/motion.conf -n
Restart=on-failure
RestartSec=5
StandardOutput=null
StandardError=null

[Install]
WantedBy=multi-user.target
```

The `-n` flag runs motion in non-daemon mode so systemd can manage the process lifecycle. `Requires=remote-fs.target` ensures the NAS mount is available before motion starts.

- [ ] **Step 2: Commit**

```bash
git add opensecuritycam.service
git commit -m "feat: add systemd service unit"
```

---

### Task 5: Create `install.sh`

**Files:**
- Create: `install.sh`

- [ ] **Step 1: Create `install.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

# NAS connection — override via environment variables before running:
#   NAS_HOST=192.168.1.100 NAS_SHARE=/volume1/security NAS_TYPE=nfs sudo bash install.sh
NAS_HOST="${NAS_HOST:-nas-host}"
NAS_SHARE="${NAS_SHARE:-/volume1/security-cam}"
NAS_TYPE="${NAS_TYPE:-nfs}"          # "nfs" or "smb"
MOUNT_POINT="/mnt/nas/security-cam"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing motion"
apt-get update -qq
apt-get install -y motion

echo "==> Creating config directory"
mkdir -p /etc/cam_motion

echo "==> Reading camera name from config.toml"
CAMERA_NAME=$(python3 -c "import tomllib; c=tomllib.load(open('${SCRIPT_DIR}/config.toml','rb')); print(c['camera']['name'])")
echo "  Camera name: ${CAMERA_NAME}"

echo "==> Installing motion.conf (substituting camera name)"
sed "s/CAMERA_NAME/${CAMERA_NAME}/g" "${SCRIPT_DIR}/motion.conf" > /etc/cam_motion/motion.conf

echo "==> Installing config.toml"
cp "${SCRIPT_DIR}/config.toml" /etc/cam_motion/config.toml

echo "==> Creating log directory"
mkdir -p /var/log/cam_motion

echo "==> Installing cam_notifier.py"
cp "${SCRIPT_DIR}/cam_notifier.py" /usr/local/bin/cam_notifier.py
chmod +x /usr/local/bin/cam_notifier.py

echo "==> Creating NAS mount point: ${MOUNT_POINT}"
mkdir -p "${MOUNT_POINT}"

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

echo "==> Installing systemd service"
cp "${SCRIPT_DIR}/opensecuritycam.service" /etc/systemd/system/opensecuritycam.service
systemctl daemon-reload
systemctl enable opensecuritycam
systemctl start opensecuritycam

echo ""
echo "==> Done. Service status:"
systemctl status opensecuritycam --no-pager
```

- [ ] **Step 2: Make executable and commit**

```bash
chmod +x install.sh
git add install.sh
git commit -m "feat: add install script"
```

---

### Task 6: Verify on the Raspberry Pi

This task runs on the Pi. Requires the repo to be cloned or copied there.

- [ ] **Step 1: Copy repo to Pi and run install**

```bash
# On the Pi as root:
cd /opt
git clone <your-repo-url> opensecuritycam
cd opensecuritycam
NAS_HOST=192.168.1.100 NAS_SHARE=/volume1/security-cam NAS_TYPE=nfs sudo bash install.sh
```

- [ ] **Step 2: Confirm motion service is running**

```bash
systemctl status opensecuritycam
```

Expected: `Active: active (running)`

- [ ] **Step 3: Confirm NAS is mounted**

```bash
df -h | grep nas
ls /mnt/nas/security-cam
```

Expected: no errors, directory listing from NAS

- [ ] **Step 4: Trigger motion and verify clip + webhook**

Wave in front of the camera, wait ~5 seconds, then:

```bash
# Check for a clip on NAS:
ls /mnt/nas/security-cam/*.mp4

# Check notifier log:
tail -f /var/log/cam_motion/notifier.log
```

Expected log line:
```
2026-05-10 14:32:01 INFO Webhook OK status=200 clip=20260510-143200-front-door.mp4 seq=1
```

- [ ] **Step 5: Check motion log for errors**

```bash
tail /var/log/cam_motion/motion.log
```

Expected: no ERROR lines; clip start events visible at log_level 6
