# RTSP Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `motion`-daemon pipeline with `mediamtx` streaming two Reolink-style RTSP paths on port 554 — `/h264Preview_01_main` (960×720 @ 10 fps, hardware H.264) and `/h264Preview_01_sub` (640×480 @ 5 fps, software x264) — for consumption by Frigate NVR; remove all local detection, NAS recording, and webhook/curl usage.

**Architecture:** `libcamera-vid` captures the CSI camera using libcamera directly (no V4L2 shim needed) and pipes hardware-encoded H.264 to `ffmpeg`, which publishes it to `mediamtx` as the main RTSP stream. A second `ffmpeg` instance pulls the main stream, scales it down, and publishes the sub stream back into mediamtx. mediamtx manages both processes via `runOnInit` and auto-restarts them on failure.

**Tech Stack:** mediamtx (arm64 binary), libcamera-vid (Raspberry Pi OS built-in), ffmpeg, systemd, YAML config.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `mediamtx.yml` | mediamtx stream configuration — both paths, port, logging |
| Modify | `opensecuritycam.service` | Run mediamtx instead of motion; drop LD_PRELOAD; add CAP_NET_BIND_SERVICE; use `cam` user |
| Rewrite | `install.sh` | Remove motion/NAS/notifier setup; add cam user, ffmpeg, mediamtx binary download |
| Modify | `config.toml` | Remove `[webhook]` section; keep `[camera]` |
| Delete | `motion.conf` | Replaced by mediamtx.yml |
| Delete | `cam_notifier.sh` | Notifier removed entirely |
| Delete | `cam_notifier.py` | Notifier removed entirely |
| Delete | `tests/test_cam_notifier.py` | Tests for deleted module |
| Modify | `README.md` | Update architecture, features, config docs, project structure |
| Modify | `SETUP.md` | Update install steps; remove NAS steps; add RTSP verification |

---

## Task 1: Create mediamtx.yml

**Files:**
- Create: `mediamtx.yml`

- [ ] **Step 1: Write mediamtx.yml**

Create `/Users/kanstantsinbucha/me/OpenSecurityCam/mediamtx.yml` with this exact content:

```yaml
# /etc/cam_motion/mediamtx.yml
# OpenSecurityCam — mediamtx RTSP streaming configuration

###############################################################################
# Network — standard RTSP port so clients need no port suffix
###############################################################################
rtspAddress: :554

###############################################################################
# Logging
###############################################################################
logLevel: warn
logDestinations: [file]
logFile: /var/log/cam_motion/mediamtx.log

###############################################################################
# Disable unused protocols to reduce attack surface and memory use
###############################################################################
hls: no
webrtc: no
srt: no

###############################################################################
# Paths
###############################################################################
paths:

  # Main stream — hardware H.264 via libcamera-vid piped through ffmpeg (copy only).
  # libcamera-vid uses libcamera directly (no V4L2 shim needed).
  # ffmpeg does no re-encode — it just wraps the raw H.264 bytestream for RTSP.
  # mediamtx runs runOnInit through sh -c internally, so shell pipelines work directly.
  h264Preview_01_main:
    runOnInit: >-
      libcamera-vid -t 0 --codec h264 --width 960 --height 720
      --framerate 10 --bitrate 2000000 --inline -o - |
      ffmpeg -hide_banner -loglevel error -re -i - -c:v copy
      -f rtsp rtsp://127.0.0.1:554/h264Preview_01_main
    runOnInitRestartPause: 2s

  # Sub stream — ffmpeg pulls main, scales to 640×480 at 5 fps.
  # x264 ultrafast keeps CPU load low on the Zero 2 W.
  # mediamtx retries every 2 s, so this naturally comes up after /main is ready.
  h264Preview_01_sub:
    runOnInit: >-
      ffmpeg -hide_banner -loglevel error
      -rtsp_transport tcp
      -i rtsp://127.0.0.1:554/h264Preview_01_main
      -vf scale=640:480 -r 5
      -c:v libx264 -preset ultrafast -b:v 500k
      -f rtsp rtsp://127.0.0.1:554/h264Preview_01_sub
    runOnInitRestartPause: 2s
```

- [ ] **Step 2: Validate YAML syntax**

Run: `python3 -c "import yaml, sys; yaml.safe_load(open('mediamtx.yml'))" && echo "YAML OK"`

Expected: `YAML OK`

If it fails, fix the YAML before proceeding.

- [ ] **Step 3: Commit**

```bash
git add mediamtx.yml
git commit -m "add mediamtx.yml for RTSP streaming"
```

---

## Task 2: Update opensecuritycam.service

**Files:**
- Modify: `opensecuritycam.service`

- [ ] **Step 1: Overwrite the service file**

Replace the entire content of `opensecuritycam.service`:

```ini
[Unit]
Description=OpenSecurityCam RTSP streaming service
After=network.target

[Service]
Type=simple
User=cam
Group=cam
# CAP_NET_BIND_SERVICE lets the cam user bind the privileged RTSP port 554.
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
ExecStart=/usr/local/bin/mediamtx /etc/cam_motion/mediamtx.yml
Restart=on-failure
RestartSec=5
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Key changes from the previous version:
- `ExecStart`: `motion` → `mediamtx`
- `User`/`Group`: `motion` → `cam`
- Removed `LD_PRELOAD` (libcamera-vid uses libcamera directly, not V4L2)
- Removed `Requires=remote-fs.target` (no NAS)
- Added `AmbientCapabilities` + `CapabilityBoundingSet` for port 554

- [ ] **Step 2: Validate service file syntax**

Run: `systemd-analyze verify opensecuritycam.service 2>&1 || true`

Expected: no output (warnings about missing binary are acceptable on the dev machine; errors about syntax are not).

- [ ] **Step 3: Commit**

```bash
git add opensecuritycam.service
git commit -m "update service: mediamtx replaces motion, cam user, port 554 caps"
```

---

## Task 3: Rewrite install.sh

**Files:**
- Modify: `install.sh`

The install script is rewritten from scratch — NAS vars, motion config, cam_notifier install, and fstab logic are all removed.

- [ ] **Step 1: Check the latest mediamtx arm64 release tag**

Run on your development machine (or the Pi):

```bash
curl -s https://api.github.com/repos/bluenviron/mediamtx/releases/latest \
  | grep '"tag_name"' | cut -d'"' -f4
```

Note the version string (e.g. `v1.9.3`). You will put it in the `MEDIAMTX_VERSION` variable below.

- [ ] **Step 2: Overwrite install.sh**

Replace the entire content of `install.sh` with the following, substituting the actual version from Step 1 for `v1.9.3`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Update MEDIAMTX_VERSION to the latest arm64 release from:
# https://github.com/bluenviron/mediamtx/releases
MEDIAMTX_VERSION="v1.9.3"
MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_arm64v8.tar.gz"

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
```

- [ ] **Step 3: Check bash syntax**

Run: `bash -n install.sh && echo "Syntax OK"`

Expected: `Syntax OK`

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "rewrite install.sh: mediamtx replaces motion, remove NAS and notifier"
```

---

## Task 4: Update config.toml

**Files:**
- Modify: `config.toml`

- [ ] **Step 1: Remove the [webhook] section**

Replace the entire content of `config.toml`:

```toml
[camera]
name = "front-door"
```

The camera name is kept — it is used in clip filenames and log context on the Pi and can be referenced in Frigate camera config.

- [ ] **Step 2: Commit**

```bash
git add config.toml
git commit -m "config: remove webhook section, camera name kept"
```

---

## Task 5: Delete obsolete files

**Files:**
- Delete: `motion.conf`
- Delete: `cam_notifier.sh`
- Delete: `cam_notifier.py`
- Delete: `tests/test_cam_notifier.py`

- [ ] **Step 1: Delete the files**

```bash
git rm motion.conf cam_notifier.sh cam_notifier.py tests/test_cam_notifier.py
```

Expected output lists all four files as deleted.

- [ ] **Step 2: Verify no dangling references in remaining files**

```bash
grep -rn "cam_notifier\|motion\.conf\|on_movie_start\|webhook" \
  --include="*.py" --include="*.sh" --include="*.toml" --include="*.md" \
  --exclude-dir=".git" --exclude-dir="docs" .
```

Expected: no output (or only hits inside `docs/` which is fine). If any non-doc file still references the deleted items, fix it before committing.

- [ ] **Step 3: Commit**

```bash
git commit -m "remove motion, cam_notifier, and their tests"
```

---

## Task 6: Update README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Overwrite README.md**

Replace the entire content of `README.md`:

```markdown
# OpenSecurityCam

An open source RTSP security camera built on open hardware. Runs on a Raspberry Pi with an official camera module and streams two Reolink-compatible RTSP paths for consumption by Frigate NVR or any RTSP-capable system.

---

## Features

- **Dual RTSP streams** — main (960×720 @ 10 fps) and sub (640×480 @ 5 fps), Reolink-style paths on port 554
- **Hardware H.264 encoding** — main stream encoded by the Pi's camera ISP pipeline; very low CPU
- **Frigate-ready** — stream paths mimic Reolink cameras so Frigate config is familiar
- **Open hardware** — Raspberry Pi + official Pi Camera Module (CSI), no proprietary lock-in
- **mediamtx daemon** — lightweight RTSP server with automatic source process restart
- **One-command install** — `install.sh` sets up everything from scratch

---

## Hardware

| Component | Recommended | Budget / minimal |
|---|---|---|
| Board | Raspberry Pi 4 or 5 (2 GB RAM+) | Raspberry Pi Zero 2 W |
| Camera | Pi Camera Module v2 / v3 / HQ (CSI ribbon) | same |
| Storage | microSD 16 GB+ (Class 10 or faster) | same |
| OS | Raspberry Pi OS Lite 64-bit (Bookworm) | same |

> **Pi Zero 2 W note:** fully supported. The sub stream uses x264 `ultrafast` to keep software transcode CPU manageable. The main stream uses hardware encoding (near-zero CPU).

---

## How It Works

```
Pi Camera (CSI)
    → libcamera-vid (hardware H.264)
        → ffmpeg (copy, no re-encode) → mediamtx
            → rtsp://<pi-ip>:554/h264Preview_01_main   960×720 @ 10 fps
            → rtsp://<pi-ip>:554/h264Preview_01_sub    640×480 @ 5 fps
                ← ffmpeg (pulls main, scales, re-publishes)
```

---

## Quick Start

See **[SETUP.md](SETUP.md)** for the full guide.

**TL;DR** — once you have a running Pi with the repo cloned:

```bash
# Edit config.toml with your camera name
nano config.toml

# Run the installer as root
sudo bash install.sh
```

---

## Configuration

| File | Purpose |
|---|---|
| `config.toml` | Camera name |
| `mediamtx.yml` | Stream paths, resolutions, framerates, port |

### `config.toml`

```toml
[camera]
name = "front-door"
```

### Key `mediamtx.yml` parameters

| Parameter | Default | Description |
|---|---|---|
| `rtspAddress` | `:554` | RTSP listen port |
| Main `--width` / `--height` | 960×720 | Main stream resolution |
| Main `--framerate` | 10 | Main stream FPS |
| Main `--bitrate` | 2000000 | Main stream bitrate (bps) |
| Sub `scale=` | 640×480 | Sub stream resolution |
| Sub `-r` | 5 | Sub stream FPS |
| Sub `-b:v` | 500k | Sub stream bitrate |

---

## Frigate Configuration (reference)

```yaml
cameras:
  front-door:
    ffmpeg:
      inputs:
        - path: rtsp://<pi-ip>:554/h264Preview_01_main
          roles:
            - record
        - path: rtsp://<pi-ip>:554/h264Preview_01_sub
          roles:
            - detect
    detect:
      width: 640
      height: 480
```

---

## Project Structure

```
OpenSecurityCam/
├── config.toml              # Camera name
├── mediamtx.yml             # Stream configuration
├── install.sh               # One-command installer
├── opensecuritycam.service  # systemd unit
└── SETUP.md                 # Full setup guide
```

---

## Development

```bash
git clone https://github.com/kanstantsin-bucha/cam-motion.git
cd cam-motion
```

Requirements: [uv](https://docs.astral.sh/uv/) not required (Python notifier removed).

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Contributing

Issues and pull requests are welcome. Please open an issue first for significant changes.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "update README for RTSP streaming with mediamtx"
```

---

## Task 7: Update SETUP.md

**Files:**
- Modify: `SETUP.md`

- [ ] **Step 1: Overwrite SETUP.md**

Replace the entire content of `SETUP.md`:

```markdown
# OpenSecurityCam — Setup Guide

Complete step-by-step guide from a blank SD card to a running RTSP security camera.

---

## What You Need

**Hardware**
- Raspberry Pi 4 or 5 (2 GB RAM minimum recommended) — or Raspberry Pi Zero 2 W
- Pi Camera Module (v2, v3, or HQ) with CSI ribbon cable
- microSD card (16 GB+, Class 10 or faster)
- Power supply (official USB-C for Pi 4/5, micro-USB for Zero 2 W)

**On your computer**
- [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
- SSH client (macOS/Linux: terminal; Windows: Windows Terminal or PuTTY)

---

## Step 1 — Flash Raspberry Pi OS

1. Open **Raspberry Pi Imager**.
2. Click **Choose Device** → select your Pi model.
3. Click **Choose OS** → **Raspberry Pi OS (other)** → **Raspberry Pi OS Lite (64-bit)**.
   - Lite has no desktop — correct for a headless camera.
   - Use Bookworm (Debian 12) or newer.
4. Click **Choose Storage** → select your SD card.
5. Click **Next**, then **Edit Settings**:

   | Setting | Value |
   |---|---|
   | Hostname | `camera1` (unique name per camera) |
   | Username | `pi` |
   | Password | a strong password |
   | WiFi SSID/password | your network credentials |
   | Enable SSH | ✅ Use password authentication |
   | Locale / timezone | your location |

6. Save, then click **Yes** to flash. Eject when done.

---

## Step 2 — Connect the Camera and Boot

1. With the Pi **powered off**, attach the ribbon cable to the CSI port (contacts facing away from latch).
2. Insert the SD card and power on.
3. Wait ~60 seconds for first boot.

---

## Step 3 — SSH Into the Pi

```bash
ssh pi@camera1.local
```

If `camera1.local` doesn't resolve, use the IP from your router's DHCP list.

---

## Step 3.5 — Rename the Camera (optional)

Each camera must have a unique hostname:

```bash
sudo hostnamectl set-hostname camera1
sudo sed -i "s/$(hostname)/camera1/g" /etc/hosts
sudo systemctl restart avahi-daemon
```

---

## Step 4 — Update the System

```bash
sudo apt-get update && sudo apt-get upgrade -y
```

Reboot if a kernel update was applied:

```bash
sudo reboot
```

---

## Step 5 — Verify the Camera Is Detected

```bash
rpicam-hello --list-cameras
```

Expected: one camera listed (e.g. `ov5647 [2592x1944 10-bit GBRG]`).

If no camera is listed:
- Reseat the ribbon cable.
- Check `dmesg | grep -E "csi|ov5647"` for errors.

---

## Step 6 — Clone the Repository

```bash
git clone https://github.com/kanstantsin-bucha/cam-motion.git
cd cam-motion
```

---

## Step 7 — Edit Configuration

```bash
nano config.toml
```

```toml
[camera]
name = "front-door"   # used in Frigate camera label and log entries
```

Optionally adjust stream parameters in `mediamtx.yml` (resolution, fps, bitrate).

---

## Step 8 — Run the Installer

```bash
sudo bash install.sh
```

The installer does the following:

| Step | What happens |
|---|---|
| 1 | Installs `ffmpeg` via apt |
| 2 | Creates `cam` system user and adds to `video` group |
| 3 | Copies `mediamtx.yml` and `config.toml` to `/etc/cam_motion/` |
| 4 | Creates `/var/log/cam_motion/` owned by `cam` |
| 5 | Downloads and installs `mediamtx` binary to `/usr/local/bin/` |
| 6 | Installs and starts `opensecuritycam` systemd service |

---

## Step 9 — Verify Everything Works

### Check the service is running

```bash
systemctl status opensecuritycam
```

Expected: `Active: active (running)`

### Check the streams are available

```bash
# Install ffprobe if not already present (from ffmpeg package):
ffprobe -v quiet -show_streams rtsp://127.0.0.1:554/h264Preview_01_main 2>&1 | grep codec_name
```

Expected: `codec_name=h264`

Wait ~5 seconds after service start for the sub stream to come up:

```bash
ffprobe -v quiet -show_streams rtsp://127.0.0.1:554/h264Preview_01_sub 2>&1 | grep codec_name
```

Expected: `codec_name=h264`

### Check the logs

```bash
# Service startup:
journalctl -u opensecuritycam -n 30

# mediamtx log:
tail -f /var/log/cam_motion/mediamtx.log
```

---

## Frigate Integration

Add to your Frigate `config.yaml`:

```yaml
cameras:
  front-door:
    ffmpeg:
      inputs:
        - path: rtsp://<pi-ip>:554/h264Preview_01_main
          roles:
            - record
        - path: rtsp://<pi-ip>:554/h264Preview_01_sub
          roles:
            - detect
    detect:
      width: 640
      height: 480
```

Replace `<pi-ip>` with the Pi's IP or `.local` hostname (e.g. `camera1.local`).

---

## Troubleshooting

### Quick reference

| Symptom | First check |
|---|---|
| Service not starting | `journalctl -u opensecuritycam -n 50` |
| No camera detected | `rpicam-hello --list-cameras`; reseat ribbon cable |
| Main stream unavailable | Check mediamtx.log; verify libcamera-vid path: `which libcamera-vid` |
| Sub stream unavailable | Wait 5–10 s after service start; sub stream comes up after main |
| Port 554 permission denied | Verify `AmbientCapabilities=CAP_NET_BIND_SERVICE` in service file |
| Frigate can't connect | Check Pi firewall: `sudo ufw status`; test with `ffprobe` from Frigate host |

### Camera diagnostics

```bash
# Verify libcamera sees the camera:
rpicam-hello --list-cameras

# Capture a test JPEG:
rpicam-still -o /tmp/test.jpg && scp pi@camera1.local:/tmp/test.jpg .

# List V4L2 devices (for reference):
v4l2-ctl --list-devices
```

### Stream diagnostics

```bash
# Check mediamtx is listening:
ss -tlnp | grep 554

# Probe main stream from the Pi itself:
ffprobe -v error -show_streams rtsp://127.0.0.1:554/h264Preview_01_main

# Probe from another machine:
ffprobe -v error -show_streams rtsp://camera1.local:554/h264Preview_01_main
```

### Logs

```bash
# Service startup and crashes:
journalctl -u opensecuritycam -n 50

# mediamtx runtime log:
tail -f /var/log/cam_motion/mediamtx.log

# Restart after config changes:
sudo systemctl restart opensecuritycam
```

---

## Updating

```bash
cd ~/cam-motion
git pull
sudo bash install.sh
```
```

- [ ] **Step 2: Commit**

```bash
git add SETUP.md
git commit -m "update SETUP.md for mediamtx RTSP streaming"
```

---

## Self-Review Checklist

Run after all tasks are committed on the Pi:

- [ ] `systemctl is-active opensecuritycam` → `active`
- [ ] `ffprobe -v quiet -show_streams rtsp://127.0.0.1:554/h264Preview_01_main 2>&1 | grep codec_name` → `codec_name=h264`
- [ ] `ffprobe -v quiet -show_streams rtsp://127.0.0.1:554/h264Preview_01_sub 2>&1 | grep codec_name` → `codec_name=h264`
- [ ] `grep -r "motion\|cam_notifier\|webhook\|curl" --include="*.sh" --include="*.toml" --include="*.service" --include="*.yml" . | grep -v ".git" | grep -v "docs/"` → no output
- [ ] `ls motion.conf cam_notifier.sh cam_notifier.py tests/test_cam_notifier.py 2>&1` → all "No such file"
