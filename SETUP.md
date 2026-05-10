# OpenSecurityCam — Setup Guide

Complete step-by-step guide from a blank SD card to a running security camera.

---

## What You Need

**Hardware**
- Raspberry Pi 4 or 5 nano (2GB RAM minimum)
- Pi Camera Module (v2, v3, or HQ) with CSI ribbon cable
- microSD card (16 GB+, Class 10 or faster)
- Power supply (official micro-USB for nano adapter recommended)
- NAS with an NFS or SMB/CIFS share

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
| `event_gap` | 60 | Seconds of calm before a new event starts |

---

## Step 9 — Run the Installer

The installer requires root and three environment variables for your NAS:

```bash
# For NFS:
sudo NAS_HOST=192.168.1.x \
     NAS_SHARE=/volume1/security-cam \
     NAS_TYPE=nfs \
     bash install.sh

# For SMB/CIFS:
sudo NAS_HOST=192.168.1.x \
     NAS_SHARE=/volume1/security-cam \
     NAS_TYPE=smb \
     bash install.sh
```

The installer does the following automatically:

| Step | What happens |
|---|---|
| 1 | Installs `motion`, `nfs-common`, `cifs-utils` via apt |
| 2 | Installs `uv` to `/usr/local/bin` |
| 3 | Installs Python 3.11 via `uv python install 3.11` |
| 4 | Copies `config.toml` and `motion.conf` to `/etc/cam_motion/` (substituting camera name) |
| 5 | Creates `/var/log/cam_motion/` owned by `motion` user |
| 6 | Installs `cam_notifier.py` to `/usr/local/bin/` |
| 7 | Creates NAS mount point `/mnt/nas/security-cam` |
| 8 | Adds NAS entry to `/etc/fstab` and mounts it |
| 9 | Installs and starts `opensecuritycam` systemd service |

### SMB credentials file (SMB only)

For password-protected SMB shares, create a credentials file after running the installer:

```bash
sudo nano /etc/cam_motion/smb-credentials
```

```
username=your-nas-user
password=your-nas-password
```

```bash
sudo chmod 600 /etc/cam_motion/smb-credentials
```

Then update the fstab entry to reference it:

```bash
sudo nano /etc/fstab
# Change the cifs line to add: credentials=/etc/cam_motion/smb-credentials
# Example:
# //192.168.1.x/volume1/security-cam /mnt/nas/security-cam cifs uid=...,gid=...,credentials=/etc/cam_motion/smb-credentials,_netdev,auto 0 0
sudo mount -a
```

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

| Symptom | Check |
|---|---|
| Service not starting | `journalctl -u opensecuritycam -n 50` |
| No `/dev/video0` | Reseat ribbon cable; check `dmesg \| grep video` |
| NAS mount fails | Verify NAS IP and export/share settings; `sudo mount /mnt/nas/security-cam` manually |
| No clips recorded | Check `motion.log`; lower `threshold` in `motion.conf`, run `sudo systemctl restart opensecuritycam` |
| Webhook not firing | Check `notifier.log`; verify webhook URL in `config.toml` |
| Clips record but webhook fails | Check NAS server is reachable on port 80/443; check `timeout_seconds` |

### Useful commands

```bash
# Restart the service after config changes:
sudo systemctl restart opensecuritycam

# Watch motion log live:
sudo tail -f /var/log/cam_motion/motion.log

# Watch notifier log live:
tail -f /var/log/cam_motion/notifier.log

# Test camera preview (requires display or VNC):
libcamera-hello -t 5000

# Test webhook manually:
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
sudo bash install.sh  # safe to re-run, idempotent
```
