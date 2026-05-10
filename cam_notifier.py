#!/usr/bin/env python3
import json
import logging
import sys
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path("/etc/cam_motion/config.toml")
LOG_PATH = Path("/var/log/cam_motion/notifier.log")


def setup_logging(log_path: Path) -> None:
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def load_config(config_path: Path) -> dict:
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def build_payload(config: dict, filepath: str, sequence) -> dict:
    return {
        "camera": config["camera"]["name"],
        "timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        "sequence": int(sequence),
        "clip": Path(filepath).name,
    }


def post_webhook(url: str, payload: dict, timeout: int) -> int:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def main() -> None:
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <filepath> <motion_timestamp> <event_number>", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    sequence = sys.argv[3]

    setup_logging(LOG_PATH)

    try:
        config = load_config(CONFIG_PATH)
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        sys.exit(1)

    payload = build_payload(config, filepath, sequence)
    url = config["webhook"]["url"]
    timeout = config["webhook"].get("timeout_seconds", 5)

    try:
        status = post_webhook(url, payload, timeout)
        logging.info(f"Webhook OK status={status} clip={payload['clip']} seq={payload['sequence']}")
    except urllib.error.URLError as e:
        logging.error(f"Webhook failed: {e} clip={payload['clip']}")
    except Exception as e:
        logging.error(f"Unexpected error: {e} clip={payload['clip']}")


if __name__ == "__main__":
    main()
