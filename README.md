# OpenSecurityCam

An open source motion detection security camera built on open hardware. Runs on a Raspberry Pi with an official camera module, records motion-triggered video clips to network storage, and fires a webhook notification the instant motion is detected.

---

## Features

- **Motion detection** — frame differencing via the battle-tested `motion` daemon, tunable sensitivity
- **Continuous clip recording** — 1-minute MP4 segments written directly to NAS while motion persists
- **Instant webhook notification** — fires at the start of each clip, not after it finishes
- **Open hardware** — Raspberry Pi + official Pi Camera Module (CSI), no proprietary lock-in
- **NAS storage** — NFS or SMB/CIFS mount, clips land directly on your own storage
- **Python 3.11, stdlib only** — no third-party runtime dependencies for the notifier
- **UV-managed Python** — reproducible Python version per project
- **One-command install** — `install.sh` sets up everything from scratch

---

## Hardware

| Component | Recommended |
|---|---|
| Board | Raspberry Pi 4 or 5 nano (2 GB RAM+) |
| Camera | Pi Camera Module v2 / v3 / HQ (CSI ribbon: Standard - Mini) |
| Storage | microSD 16 GB+ (Class 10 or faster) |
| OS | Raspberry Pi OS Lite 64-bit (Bookworm) |
| Network storage | Any NAS with NFS or SMB/CIFS share |

All hardware is off-the-shelf and widely available. No soldering required.

---

## How It Works

```
Pi Camera (CSI)
    → motion daemon   — detects movement, records 1-min MP4 clips to NAS
        → on_movie_start hook
            → cam_notifier.py   — POSTs webhook immediately when each clip starts
                → your NAS / server endpoint
```

Each webhook payload:

```json
{
  "camera": "front-door",
  "timestamp": "2026-05-10T14:32:00Z",
  "sequence": 3,
  "clip": "20260510-143200-front-door.mp4"
}
```

---

## Quick Start

See **[SETUP.md](SETUP.md)** for the full guide: flashing the SD card, connecting the camera, configuring the NAS, and running the installer.

**TL;DR** — once you have a running Pi with the repo cloned:

```bash
# Edit config.toml with your camera name and webhook URL
nano config.toml

# Run the installer as root
sudo NAS_HOST=192.168.1.x \
     NAS_SHARE=/volume1/security-cam \
     NAS_TYPE=nfs \
     bash install.sh
```

The installer sets up `motion`, `uv`, Python 3.11, config files, NAS mount, and the systemd service.

---

## Configuration

| File | Purpose |
|---|---|
| `config.toml` | Camera name and webhook URL |
| `motion.conf` | Sensitivity, resolution, framerate, clip length |

### `config.toml`

```toml
[camera]
name = "front-door"

[webhook]
url = "http://your-nas/api/motion"
timeout_seconds = 5
```

### Key `motion.conf` parameters

| Parameter | Default | Description |
|---|---|---|
| `threshold` | 1500 | Pixel change count to trigger motion (lower = more sensitive) |
| `noise_level` | 32 | Noise filter (0–255, higher = less sensitive) |
| `minimum_motion_frames` | 2 | Consecutive frames required to confirm motion |
| `event_gap` | 60 | Seconds of calm before a new event starts |
| `width` / `height` | 1280×720 | Recording resolution |
| `framerate` | 15 | Frames per second |

---

## Project Structure

```
OpenSecurityCam/
├── config.toml              # Notifier config
├── motion.conf              # motion daemon config
├── cam_notifier.py          # Webhook notifier (invoked by motion hook)
├── pyproject.toml           # Python project / UV config
├── uv.lock                  # Locked dependencies
├── install.sh               # One-command installer
├── opensecuritycam.service  # systemd unit
├── tests/
│   └── test_cam_notifier.py
└── SETUP.md                 # Full setup guide
```

---

## Development

```bash
# Clone
git clone https://github.com/kanstantsin-bucha/cam-motion.git
cd cam-motion

# Run tests
uv run pytest tests/ -v
```

Requirements: [uv](https://docs.astral.sh/uv/) installed locally.

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Contributing

Issues and pull requests are welcome. Please open an issue first for significant changes.
