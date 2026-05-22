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
