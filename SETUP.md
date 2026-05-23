# OpenSecurityCam — Setup Guide

Complete step-by-step guide from a blank SD card to a running RTSP/ONVIF security camera.

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
   | Username | `camera` |
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
ssh camera@camera1.local
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

Optionally adjust stream parameters in `cam_stream.sh` (resolution, fps, bitrate).

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
| 3 | Copies `mediamtx.yml`, `cam_stream.sh`, and `config.toml` to `/etc/cam_motion/` |
| 4 | Creates `/var/log/cam_motion/` owned by `cam` |
| 5 | Downloads and installs `mediamtx` binary to `/usr/local/bin/` |
| 6 | Installs and starts `opensecuritycam` systemd service |
| 7 | Installs `onvif_server.py` to `/usr/local/bin/` (generates ONVIF password on first run) |
| 8 | Installs and starts `onvifcam` systemd service |

At the end the installer prints:

```
==> RTSP stream (available in a few seconds):
    Main:  rtsp://<pi-ip>:554/h264Preview_01_main

==> ONVIF endpoint:
    http://<pi-ip>:8090/onvif/device_service
    User: camera  Password: <generated-password>
```

**Save the ONVIF password** — you will need it when adding the camera to Home Assistant.

---

## Step 9 — Verify Everything Works

### Check services are running

```bash
systemctl status opensecuritycam
systemctl status onvifcam
```

Expected: both `Active: active (running)`

### Check the RTSP stream

```bash
ffprobe -v error -show_entries stream=codec_name,width,height \
  -of default=noprint_wrappers=1 \
  rtsp://127.0.0.1:554/h264Preview_01_main
```

Expected: `codec_name=h264`

### Check the ONVIF endpoint

```bash
curl -s -X POST http://127.0.0.1:8090/onvif/device_service \
  -H 'Content-Type: application/soap+xml' \
  -d '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
        xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
        <s:Body><tds:GetSystemDateAndTime/></s:Body>
      </s:Envelope>'
```

Expected: a SOAP XML response containing `GetSystemDateAndTimeResponse`.

### Check the logs

```bash
# RTSP service:
journalctl -u opensecuritycam -n 30

# ONVIF service:
journalctl -u onvifcam -n 30

# mediamtx log:
tail -f /var/log/cam_motion/mediamtx.log
```

---

## Home Assistant Integration

### Via ONVIF (recommended — manually)

1. Settings → Integrations → **Add Integration** → search **ONVIF**
2. Enter:
   - Host: `<pi-ip>` or `camera1.local`
   - Port: `8090`
   - Username: `camera`
   - Password: the password printed by `install.sh`

HA will use WS-Discovery to find the camera automatically, or you can add it manually with the details above.

After adding the camera, HA will automatically create a `binary_sensor` for motion detection. The camera runs a lightweight ffmpeg process in the background that detects pixel changes in the stream and publishes ONVIF motion events — no additional configuration needed.

Add `stream:` to your `configuration.yaml` to enable the `camera.record` action for motion-triggered recording:

```yaml
logger:
  default: info
  logs:
    homeassistant.components.onvif: info
    homeassistant.components.automation: debug

stream:

automation: !include automations.yaml
```

---

## Troubleshooting

### Quick reference

| Symptom | First check |
|---|---|
| Service not starting | `journalctl -u opensecuritycam -n 50` |
| No camera detected | `rpicam-hello --list-cameras`; reseat ribbon cable |
| RTSP stream unavailable | Check mediamtx.log; verify `rpicam-vid` path: `which rpicam-vid` |
| Port 554 permission denied | Verify `AmbientCapabilities=CAP_NET_BIND_SERVICE` in service file |
| ONVIF not responding | `journalctl -u onvifcam -n 20`; check port 8090 is free: `ss -tlnp \| grep 8090` |
| HA can't connect | Check Pi firewall: `sudo ufw status`; test with `curl` from HA host |

### Camera diagnostics

```bash
# Verify libcamera sees the camera:
rpicam-hello --list-cameras

# Capture a test JPEG:
rpicam-still -o /tmp/test.jpg && scp camera@camera1.local:/tmp/test.jpg .
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

### ONVIF diagnostics

```bash
# Check ONVIF service:
systemctl status onvifcam

# Check port:
ss -tlnp | grep 8090

# Retrieve ONVIF password from installed script:
grep '^ONVIF_PASSWORD' /usr/local/bin/onvif_server.py
```

### Logs

```bash
# RTSP startup and crashes:
journalctl -u opensecuritycam -n 50

# ONVIF startup and crashes:
journalctl -u onvifcam -n 50

# mediamtx runtime log:
tail -f /var/log/cam_motion/mediamtx.log

# Restart after config changes:
sudo systemctl restart opensecuritycam
sudo systemctl restart onvifcam
```

---

## Updating

```bash
cd ~/cam-motion
git pull
sudo bash install.sh
```

The installer reuses the existing ONVIF password on updates — no need to reconfigure Home Assistant.
