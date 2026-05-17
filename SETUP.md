# OpenSecurityCam — Setup Guide

Complete step-by-step guide from a blank SD card to a running security camera.

---

## What You Need

**Hardware**
- Raspberry Pi 4 or 5 (2 GB RAM minimum recommended) — or Raspberry Pi Zero 2 W for a budget build (see note below)
- Pi Camera Module (v2, v3, or HQ) with CSI ribbon cable
- microSD card (16 GB+, Class 10 or faster)
- Power supply (official USB-C for Pi 4/5, micro-USB for Zero 2 W)
- NAS with an NFS or SMB/CIFS share

> **Pi Zero 2 W:** fully supported, but edit `motion.conf` before running the installer — set `width 640`, `height 480`, `framerate 5`, and leave `minimum_frame_time 2` (the default). This keeps CPU usage manageable on the quad-core 512 MB board.

**On your computer**
- [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (free download)
- SSH client (macOS/Linux: built-in terminal; Windows: Windows Terminal or PuTTY)
- Your NAS share details: host IP, share path, protocol (NFS or SMB)

---

## Step 1 — Flash Raspberry Pi OS

1. Open **Raspberry Pi Imager**.
2. Click **Choose Device** → select your Pi model.
3. Click **Choose OS** → **Raspberry Pi OS (other)** → **Raspberry Pi OS Lite (64-bit)**.
   - Lite has no desktop — correct for a headless camera.
   - Use Bookworm (Debian 12) or newer.
4. Click **Choose Storage** → select your SD card.
5. Click **Next**, then **Edit Settings** when asked about OS customisation:

   | Setting | Value |
   |---|---|
   | Hostname | `camera1` (becomes `camera1.local` on your network — use a unique name per camera) |
   | Username | `pi` |
   | Password | a strong password |
   | WiFi SSID/password | your network credentials (if using WiFi) |
   | Enable SSH | ✅ Use password authentication |
   | Locale / timezone | set to your location |

6. Save settings, then click **Yes** to apply and flash.
7. Wait for the flash and verification to complete, then eject the card.

---

## Step 2 — Connect the Camera and Boot

1. With the Pi **powered off**, attach the camera ribbon cable to the CSI port.
   - Lift the latch, insert ribbon (contacts facing away from latch), push latch down.
2. Insert the SD card and power on the Pi.
3. Wait ~60 seconds for first boot.

---

## Step 3 — SSH Into the Pi

From your computer (replace `camera1` with the hostname you set in Step 1):

```bash
ssh pi@camera1.local
```

If `camera1.local` doesn't resolve, find the Pi's IP from your router's DHCP list and use that instead:

```bash
ssh pi@192.168.1.x
```

---

## Step 3.5 — Rename the Camera (mDNS Name)

Each camera must have a unique hostname so you can reach it at `<hostname>.local` on your network. If you set the hostname correctly in Step 1, skip this step. If you need to rename a running Pi:

```bash
# Set the new hostname (e.g. camera1, camera2, front-door, garage)
sudo hostnamectl set-hostname camera1

# Update /etc/hosts so the Pi can resolve its own name
sudo sed -i "s/$(hostname)/camera1/g" /etc/hosts

# Restart the mDNS daemon to advertise the new name
sudo systemctl restart avahi-daemon
```

After this, you can SSH using the new name:

```bash
ssh pi@camera1.local
```

> **Multiple cameras:** give each Pi a distinct hostname (`camera1`, `camera2`, `front-door`, `garage`, etc.) and set the matching `name` in `config.toml` so webhook payloads and clip filenames are unambiguous.

---

## Step 4 — Update the System

```bash
sudo apt-get update && sudo apt-get upgrade -y
```

This takes a few minutes. Reboot if a kernel update was applied:

```bash
sudo reboot
```

---

## Step 5 — Verify the Camera Is Detected

```bash
v4l2-ctl --list-devices
```

You should see something like:

```
pispbe (platform:1000880000.pisp_be):
    /dev/video20
    ...

rp1-cfe (platform:1f00110000.csi):
    /dev/video0       ← this is the camera
    ...
```

If `/dev/video0` is absent, check the ribbon cable connection and run:

```bash
sudo raspi-config
# Interface Options → Legacy Camera → Enable
# (only needed on older Pi OS versions)
```

---

## Step 6 — Configure Your NAS Share

### If using NFS

On your NAS, export the share with write access for the Pi's IP. Example NFS export entry (on the NAS):

```
/volume1/security-cam  192.168.1.0/24(rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=1000)
```

> `all_squash` maps all writes to `anonuid`/`anongid`. Set these to match the `motion` user's UID/GID on the Pi (check after install with `id motion`).

### If using SMB/CIFS

Create a dedicated share user on the NAS with read/write access to the security camera share. Note the username and password — you'll need to create a credentials file after install.

---

## Step 7 — Clone the Repository

```bash
git clone https://github.com/kanstantsin-bucha/cam-motion.git
cd cam-motion
```

---

## Step 8 — Edit Configuration

### `config.toml` — camera name and webhook

```bash
nano config.toml
```

```toml
[camera]
name = "front-door"        # used in filenames and webhook payload

[webhook]
url = "http://192.168.1.x/api/motion"   # your NAS webhook endpoint
timeout_seconds = 5
```

### `motion.conf` — sensitivity (optional, tune after first run)

The defaults are conservative and work for most indoor environments. You can adjust after deployment:

| Parameter | Default | Effect |
|---|---|---|
| `threshold` | 1500 | Lower = more sensitive to small movements |
| `noise_level` | 32 | Higher = ignores more background noise |
| `minimum_motion_frames` | 2 | Higher = requires more sustained motion |
| `event_gap` | 10 | Seconds of no motion before the clip is closed and written. Shorter = faster availability on NAS; longer = fewer files when motion is continuous. |
| `width` / `height` | 640 / 480 | OV5647 supported modes: 640×480, 1296×972, 1920×1080, 2592×1944 — all at up to 30 fps. Higher resolution increases CPU load and clip file size. |
| `framerate` | 5 | Frames captured per second — controls both detection rate and video smoothness. Use 5 on a Zero 2 W, up to 15 on a Pi 4/5. |
| `minimum_frame_time` | 0 | Minimum seconds between frame grabs. Throttles the entire capture loop (detection AND video). Leave at 0 and use `framerate` to control rate instead. |

---

## Step 9 — Run the Installer

The installer requires root and environment variables for your NAS:

```bash
# For NFS:
sudo NAS_HOST=192.168.1.x \
     NAS_SHARE=/volume1/security-cam \
     NAS_TYPE=nfs \
     bash install.sh

# For SMB/CIFS (no password):
sudo NAS_HOST=192.168.1.x \
     NAS_SHARE=/NAS \
     NAS_TYPE=smb \
     bash install.sh

# For SMB/CIFS (password-protected share):
sudo NAS_HOST=192.168.1.x \
     NAS_SHARE=/NAS \
     NAS_TYPE=smb \
     SMB_USER=your-nas-user \
     SMB_PASS=your-nas-password \
     bash install.sh
```

When `SMB_USER` and `SMB_PASS` are provided the installer automatically writes `/etc/cam_motion/smb-credentials` (mode 600) and wires it into the fstab entry.

The installer does the following automatically:

| Step | What happens |
|---|---|
| 1 | Installs `motion`, `nfs-common`, `cifs-utils`, `curl` via apt |
| 2 | Copies `config.toml` and `motion.conf` to `/etc/cam_motion/` (substituting camera name) |
| 3 | Creates `/var/log/cam_motion/` owned by `motion` user |
| 4 | Installs `cam_notifier.sh` to `/usr/local/bin/` |
| 5 | Creates NAS mount point `/mnt/nas/security-cam` |
| 6 | Writes SMB credentials file if `SMB_USER`/`SMB_PASS` set; adds NAS entry to `/etc/fstab` and mounts it |
| 7 | Installs and starts `opensecuritycam` systemd service |

---

## Step 10 — Verify Everything Works

### Check the service is running

```bash
systemctl status opensecuritycam
```

Expected: `Active: active (running)`

### Check the NAS is mounted

```bash
df -h | grep nas
ls /mnt/nas/security-cam
```

### Trigger motion and check logs

Wave in front of the camera, then after ~5 seconds:

```bash
# Check for a clip on the NAS:
ls /mnt/nas/security-cam/*.mp4

# Check the notifier log:
tail -f /var/log/cam_motion/notifier.log
```

Expected log line:
```
2026-05-10 14:32:01 INFO Webhook OK status=200 clip=20260510-143200-front-door.mp4 seq=1
```

### Check the motion log for errors

```bash
sudo tail /var/log/cam_motion/motion.log
```

---

## Troubleshooting

### Quick reference

| Symptom | First check |
|---|---|
| Service not starting | `journalctl -u opensecuritycam -n 50` |
| `VIDIOC_STREAMON: Invalid argument` in motion.log | See **Camera / libcamera** section below |
| No `/dev/video0` | Reseat ribbon cable; `dmesg \| grep -E "csi\|video\|ov5647"` |
| NAS mount fails | `sudo mount /mnt/nas/security-cam` — check NAS IP and export settings |
| No clips recorded | Lower `threshold` in `motion.conf`; restart service |
| Webhook not firing | Check `notifier.log`; verify URL in `config.toml` |
| Clips record but webhook fails | Check NAS reachable on port 80/443; check `timeout_seconds` |

---

### Camera / libcamera

The OV5647 CSI sensor outputs raw 10-bit Bayer (SGBRG10_1X10). On kernel 6.12 the unicam V4L2 node exposes this as format `pGAA` (SGBRG10P). Neither motion nor ffmpeg can negotiate this format directly — both fail with `VIDIOC_STREAMON: Invalid argument`.

The service works around this by LD_PRELOADing the libcamera V4L2 compat shim (`v4l2-compat.so`), which intercepts V4L2 ioctls and routes them through the full libcamera pipeline (unicam → ISP), presenting motion with a standard YUV420 stream.

**Verify libcamera sees the camera:**

```bash
rpicam-hello --list-cameras
# Expected: ov5647 [2592x1944 10-bit GBRG] with SGBRG10_CSI2P modes
```

**Verify the shim is loaded at runtime:**

```bash
sudo cat /proc/$(systemctl show -p MainPID --value opensecuritycam)/maps \
  | grep v4l2-compat
# Expected: a line containing v4l2-compat.so
```

**Check what format motion actually negotiated:**

```bash
sudo grep "pixfmt_set\|pixfmt_select\|STREAMON" /var/log/cam_motion/motion.log | tail -5
# Healthy: "Using palette YU12 (640x480)"
# Broken:  "Using palette Y12" followed by "VIDIOC_STREAMON: Invalid argument"
```

If you see Y12 / STREAMON error, the shim is not loading. Check `journalctl -u opensecuritycam -n 20` for LD_PRELOAD errors and verify the shim path:

```bash
ls /usr/libexec/aarch64-linux-gnu/libcamera/v4l2-compat.so
```

---

### Camera diagnostics (install v4l-utils first)

```bash
sudo apt-get install -y v4l-utils
```

```bash
# List all V4L2 devices and their driver:
v4l2-ctl --list-devices

# Show the current capture format on the raw unicam node:
sudo v4l2-ctl -d /dev/video0 --get-fmt-video

# Show the media pipeline topology (unicam on /dev/media2):
sudo media-ctl -d /dev/media2 -p

# Show what format the sensor subdev is configured for:
sudo v4l2-ctl -d /dev/v4l-subdev0 --get-subdev-fmt
# Expected: MEDIA_BUS_FMT_SGBRG10_1X10 640x480
```

---

### NAS

```bash
# Remount after fstab changes:
sudo mount -a

# Check what is mounted:
df -h | grep nas

# List clips on the NAS:
ls -lh /mnt/nas/security-cam/
```

---

### Logs

```bash
# Service startup and crashes:
journalctl -u opensecuritycam -n 50

# Motion detection log (live):
sudo tail -f /var/log/cam_motion/motion.log

# Webhook notifier log (live):
tail -f /var/log/cam_motion/notifier.log

# Restart after config changes:
sudo systemctl restart opensecuritycam
```

---

### Camera preview

```bash
# Quick sanity check — streams 5 seconds to console (no display needed):
rpicam-hello -t 5000 --nopreview

# Capture a single JPEG to verify framing and focus:
rpicam-still -o /tmp/test.jpg && scp pi@camera1.local:/tmp/test.jpg .
```

---

### Webhook

```bash
# Send a test event manually:
curl -X POST http://your-nas/api/motion \
  -H 'Content-Type: application/json' \
  -d '{"camera":"front-door","timestamp":"2026-01-01T00:00:00Z","sequence":1,"clip":"test.mp4"}'
```

---

## Updating

To update the software after pulling new changes:

```bash
cd ~/cam-motion
git pull
sudo bash install.sh  # omit NAS vars to skip fstab (safe if already configured)
```

> **Note:** Re-running without `NAS_HOST`/`NAS_SHARE` skips the fstab step entirely, preserving your existing NAS mount.
