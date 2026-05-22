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

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Contributing

Issues and pull requests are welcome. Please open an issue first for significant changes.
