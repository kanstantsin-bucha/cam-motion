#!/usr/bin/env bash
# mediamtx runOnInit for h264Preview_01_main.
# rpicam-vid uses the hardware H.264 encoder; ffmpeg wraps the bytestream for RTSP.
exec rpicam-vid -t 0 --codec h264 --width 960 --height 720 \
  --framerate 10 --bitrate 2000000 --intra 10 --inline -o - | \
  ffmpeg -hide_banner -loglevel error \
  -fflags nobuffer -flags low_delay -avioflags direct \
  -use_wallclock_as_timestamps 1 \
  -i - -c:v copy \
  -f rtsp rtsp://127.0.0.1:554/h264Preview_01_main
