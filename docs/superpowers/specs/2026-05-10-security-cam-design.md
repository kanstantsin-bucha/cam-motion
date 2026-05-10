# OpenSecurityCam — Design Spec

**Date:** 2026-05-10

## Overview

A Raspberry Pi motion-detection security camera system. The `motion` daemon handles camera access, frame differencing, and video recording. A lightweight Python notifier script fires a webhook to a NAS-hosted REST endpoint immediately when each new clip begins recording.

## Architecture

```
Pi Camera (CSI)
    → motion daemon (detection + recording)
        → NAS mount (clips saved as MP4)
        → on_movie_start hook → cam_notifier.py → HTTP POST → NAS webhook endpoint
```

Two components:

1. **`motion` daemon** — detects motion, records 1-minute H.264/MP4 clips to the NAS mount. Runs as a systemd service (`opensecuritycam.service`).
2. **`cam_notifier.py`** — invoked by `motion`'s `on_movie_start` hook at the start of each new clip. Reads `config.toml` and POSTs a JSON payload to the configured webhook URL immediately.

## motion Configuration (`/etc/cam_motion/motion.conf`)

Key settings:

- **Camera backend**: `v4l2` (`/dev/video0` — Pi CSI camera via libcamera/v4l2 bridge)
- **Resolution**: configurable (default 1280x720)
- **Framerate**: configurable (default 15fps)
- **Output path**: NAS mount, e.g. `/mnt/nas/security-cam/<camera-name>/`
- **Clip length**: `max_movie_time 60` — new file every 60 seconds while motion persists
- **File naming**: `%Y%m%d-%H%M%S-<camera-name>.mp4`
- **Hook**: `on_movie_start /path/to/cam_notifier.py %f %t %v`
  - `%f` — full path to new clip file
  - `%t` — timestamp
  - `%v` — event number (sequence within motion event)
- **Log directory**: `/var/log/cam_motion/`

### Tunable Sensitivity Parameters (all in `motion.conf`, documented inline)

| Parameter | Description |
|---|---|
| `threshold` | Pixel change count required to trigger motion |
| `noise_level` | Noise filter level |
| `minimum_motion_frames` | Consecutive changed frames required to confirm motion |
| `event_gap` | Seconds of no motion before closing an event |

## Notifier (`cam_notifier.py`)

Invoked by `motion` as a subprocess at the start of each new clip.

**Config file (`config.toml`):**
```toml
[camera]
name = "front-door"

[webhook]
url = "http://nas-host/api/motion"
timeout_seconds = 5
```

**Webhook payload (POST):**
```json
{
  "camera": "front-door",
  "timestamp": "2026-05-10T14:32:00Z",
  "sequence": 3,
  "clip": "/mnt/nas/security-cam/front-door/20260510-143200-front-door.mp4"
}
```

- Fires immediately when clip recording starts (not after finalization)
- No retries — if the POST fails, logs the error to `/var/log/cam_motion/notifier.log` and exits
- `motion` ignores the hook's exit code

## Project Structure

```
OpenSecurityCam/
├── config.toml              # cam_notifier config (camera name, webhook URL)
├── motion.conf              # motion daemon config (sensitivity, paths, hooks)
├── cam_notifier.py          # webhook notifier script
├── install.sh               # setup script (installs motion, mounts NAS, enables systemd)
└── opensecuritycam.service  # systemd unit file for motion daemon
```

## Deployment (`install.sh`)

1. Install `motion` via `apt`
2. Create `/etc/cam_motion/` and copy `motion.conf`
3. Create NAS mount directory and add NFS/SMB entry to `/etc/fstab`
4. Make `cam_notifier.py` executable and place it at the configured path
5. Install and enable `opensecuritycam.service` via systemd

## Logging

| Log | Location |
|---|---|
| motion daemon | `/var/log/cam_motion/motion.log` |
| cam_notifier | `/var/log/cam_motion/notifier.log` |

## Out of Scope (Phase 1)

- Video playback UI
- Multiple cameras
- Retry/queuing for failed webhook calls
- Authentication on the webhook endpoint
