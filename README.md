# OpenSecurityCam

An open source RTSP/ONVIF security camera built on open hardware. Runs on a Raspberry Pi with an official camera module, streams H.264 over RTSP, and exposes an ONVIF Profile S endpoint for plug-and-play integration with Home Assistant, Frigate NVR, and any ONVIF-compatible system.

---

## Features

- **RTSP stream** — 960×720 @ 10 fps, hardware H.264, port 554
- **ONVIF Profile S** — full device/media service + WS-Discovery (auto-detected by Home Assistant)
- **Hardware H.264 encoding** — encoded by the Pi's camera ISP pipeline; near-zero CPU
- **mediamtx daemon** — lightweight RTSP server with automatic source restart
- **One-command install** — `install.sh` sets up everything from scratch

---

## Hardware

| Component | Recommended | Budget / minimal |
|---|---|---|
| Board | Raspberry Pi 4 or 5 (2 GB RAM+) | Raspberry Pi Zero 2 W |
| Camera | Pi Camera Module v2 / v3 / HQ (CSI ribbon) | same |
| Storage | microSD 16 GB+ (Class 10 or faster) | same |
| OS | Raspberry Pi OS Lite 64-bit (Bookworm) | same |

---

## How It Works

```
Pi Camera (CSI)
    → rpicam-vid (hardware H.264)
        → ffmpeg (copy, no re-encode) → mediamtx
            → rtsp://<pi-ip>:554/h264Preview_01_main   960×720 @ 10 fps

onvif_server.py
    → ONVIF HTTP service on port 8090
    → WS-Discovery on UDP 3702
    → returns RTSP URI to ONVIF clients
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
| `mediamtx.yml` | RTSP stream path and port |
| `cam_stream.sh` | rpicam-vid + ffmpeg pipeline |
| `onvif_server.py` | ONVIF server (config substituted at install time) |

### `config.toml`

```toml
[camera]
name = "front-door"
```

### Key `mediamtx.yml` parameters

| Parameter | Default | Description |
|---|---|---|
| `rtspAddress` | `:554` | RTSP listen port |

### Key `cam_stream.sh` parameters

| Parameter | Default | Description |
|---|---|---|
| `--width` / `--height` | 960×720 | Stream resolution |
| `--framerate` | 10 | FPS |
| `--bitrate` | 2000000 | Bitrate (bps) |

---

## ONVIF

The ONVIF server (`onvif_server.py`) implements Profile S and WS-Discovery. It is installed and configured by `install.sh`, which generates a random ONVIF password on first install and prints it to the console.

| Endpoint | Value |
|---|---|
| Device service | `http://<pi-ip>:8090/onvif/device_service` |
| Media service | `http://<pi-ip>:8090/onvif/media_service` |
| WS-Discovery | UDP multicast 239.255.255.250:3702 |
| Username | `camera` |
| Password | generated at install time |

**To add in Home Assistant:** Settings → Integrations → Add → ONVIF → enter the host, port `8090`, and credentials printed during install.

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
            - detect
    detect:
      width: 960
      height: 720
```

---

## Project Structure

```
OpenSecurityCam/
├── config.toml              # Camera name
├── mediamtx.yml             # RTSP stream configuration
├── cam_stream.sh            # rpicam-vid → ffmpeg pipeline
├── onvif_server.py          # ONVIF Profile S + WS-Discovery server
├── install.sh               # One-command installer
├── opensecuritycam.service  # systemd unit (mediamtx)
├── onvifcam.service         # systemd unit (ONVIF server)
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
