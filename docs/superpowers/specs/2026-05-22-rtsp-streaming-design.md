# RTSP Streaming Design

**Date:** 2026-05-22  
**Status:** Approved

---

## Goal

Replace the `motion`-daemon-based local detection pipeline with a pure RTSP streaming setup using `mediamtx`. The Pi streams two RTSP paths (mimicking Reolink camera naming) for consumption by Frigate NVR. All local detection, webhook notification, NAS recording, and `curl` usage are removed.

---

## Architecture

```
Pi Camera (CSI)
    → libcamera-vid (hardware H.264)
        → stdin pipe → mediamtx
            → rtsp://<pi-ip>:554/h264Preview_01_main   (960×720 @ 10 fps)
            → rtsp://<pi-ip>:554/h264Preview_01_sub    (640×480 @ 5 fps)
                ← ffmpeg (pulls /h264Preview_01_main, scales, re-publishes)
```

---

## Stream Specs

| Path | Port | Resolution | FPS | Bitrate | Encoder | Frigate use |
|---|---|---|---|---|---|---|
| `/h264Preview_01_main` | 554 | 960×720 | 10 | 2 Mbps | libcamera hardware H.264 | `record` |
| `/h264Preview_01_sub` | 554 | 640×480 | 5 | 500 Kbps | ffmpeg x264 ultrafast | `detect` |

Both streams are H.264 over RTSP/TCP.

---

## Components

### mediamtx

- Installed from GitHub releases (arm64 binary, pinned version) to `/usr/local/bin/mediamtx`.
- Config at `/etc/cam_motion/mediamtx.yml`.
- Listens on port 554 (standard RTSP).
- Manages both source processes via `runOnInit` and auto-restarts them on exit.

### `/h264Preview_01_main` source

`runOnInit` in mediamtx config launches `libcamera-vid`, which uses libcamera directly (no V4L2 shim needed) and pushes hardware-encoded H.264 via RTSP back into mediamtx:

```
libcamera-vid -t 0 --codec h264 --width 960 --height 720 --framerate 10 \
  --bitrate 2000000 --inline -o rtsp://127.0.0.1:554/h264Preview_01_main
```

`--inline` embeds SPS/PPS headers in every keyframe so clients can join mid-stream.

### `/h264Preview_01_sub` source

`runOnInit` in mediamtx config launches ffmpeg, which pulls from the main path, scales, and pushes back into mediamtx:

```
ffmpeg -rtsp_transport tcp -i rtsp://127.0.0.1:554/h264Preview_01_main \
  -vf scale=640:480 -r 5 -c:v libx264 -preset ultrafast -b:v 500k \
  -f rtsp rtsp://127.0.0.1:554/h264Preview_01_sub
```

Uses `ultrafast` preset to minimise CPU on the Zero 2W.

### Systemd service

- Replaces `ExecStart` from `motion` to `mediamtx -c /etc/cam_motion/mediamtx.yml`.
- Removes `LD_PRELOAD` — not needed; `libcamera-vid` uses libcamera directly, not V4L2.
- Adds `AmbientCapabilities=CAP_NET_BIND_SERVICE` + `CapabilityBoundingSet=CAP_NET_BIND_SERVICE` so mediamtx can bind port 554 without running as root.
- Runs as `User=cam` (dedicated system user created by install.sh — see below).
- Removes `Requires=remote-fs.target` (no NAS dependency).

### System user

The `motion` apt package (which created the `motion` system user) is removed. install.sh creates a dedicated `cam` system user:

```bash
id cam &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin cam
```

The systemd service and log directory use `User=cam` / `chown cam:cam`.

---

## Files Changed

### Removed from install.sh
- `motion` and `curl` apt packages
- motion config copy steps
- NAS fstab / mount logic
- `cam_notifier.sh` install step

### Added to install.sh
- `ffmpeg` apt package
- Download `mediamtx` arm64 binary from GitHub releases, install to `/usr/local/bin/`
- Install `mediamtx.yml` to `/etc/cam_motion/`

### New files
- `mediamtx.yml` — mediamtx configuration
- Updated `opensecuritycam.service`
- Updated `install.sh`

### Modified files
- `config.toml` — remove `[webhook]` section; keep `[camera]` name
- `motion.conf` — deleted (replaced by `mediamtx.yml`)
- `cam_notifier.sh` — deleted
- `cam_notifier.py` — deleted
- `tests/test_cam_notifier.py` — deleted

### README / SETUP
- Update architecture diagram, feature list, configuration table, troubleshooting section to reflect RTSP-only setup.

---

## Error Handling

- mediamtx `runOnInitRestartPause: 2` restarts each source process automatically on exit.
- `/h264Preview_01_sub` ffmpeg will fail to connect until `/h264Preview_01_main` is up; mediamtx retries every 2s — this resolves naturally within a few seconds of startup.
- systemd `Restart=on-failure, RestartSec=5` covers mediamtx process crashes.

---

## Frigate Configuration (reference, not implemented here)

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

## Out of Scope

- Authentication on RTSP streams (network-level security assumed via VLAN/firewall).
- NAS mounting or local clip recording.
- Webhook notifications.
- `cam_notifier.py` / `cam_notifier.sh` (files deleted, not preserved).
