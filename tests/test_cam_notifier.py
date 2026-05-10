import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import cam_notifier


CONFIG_TOML = b"""
[camera]
name = "front-door"

[webhook]
url = "http://nas-host/api/motion"
timeout_seconds = 5
"""


class TestLoadConfig(unittest.TestCase):
    def test_loads_camera_and_webhook(self):
        with patch("builtins.open", mock_open(read_data=CONFIG_TOML)):
            config = cam_notifier.load_config(Path("/fake/config.toml"))
        self.assertEqual(config["camera"]["name"], "front-door")
        self.assertEqual(config["webhook"]["url"], "http://nas-host/api/motion")
        self.assertEqual(config["webhook"]["timeout_seconds"], 5)


class TestBuildPayload(unittest.TestCase):
    def test_payload_fields(self):
        config = {
            "camera": {"name": "front-door"},
            "webhook": {"url": "http://x", "timeout_seconds": 5},
        }
        payload = cam_notifier.build_payload(
            config,
            filepath="/mnt/nas/security-cam/20260510-143200-front-door.mp4",
            sequence=3,
        )
        self.assertEqual(payload["camera"], "front-door")
        self.assertEqual(payload["sequence"], 3)
        self.assertEqual(payload["clip"], "20260510-143200-front-door.mp4")
        # timestamp is ISO 8601 with Z suffix
        self.assertTrue(payload["timestamp"].endswith("Z"))

    def test_sequence_coerced_to_int(self):
        config = {"camera": {"name": "cam"}, "webhook": {}}
        payload = cam_notifier.build_payload(config, "/some/path/file.mp4", sequence="7")
        self.assertEqual(payload["sequence"], 7)
        self.assertIsInstance(payload["sequence"], int)

    def test_clip_is_filename_only(self):
        config = {"camera": {"name": "cam"}, "webhook": {}}
        payload = cam_notifier.build_payload(
            config, "/deep/nested/path/20260510-143200-cam.mp4", sequence=1
        )
        self.assertEqual(payload["clip"], "20260510-143200-cam.mp4")


class TestPostWebhook(unittest.TestCase):
    def test_posts_json_to_url(self):
        payload = {"camera": "front-door", "sequence": 1}
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            status = cam_notifier.post_webhook("http://nas-host/api/motion", payload, timeout=5)

        self.assertEqual(status, 200)
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://nas-host/api/motion")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(req.data), payload)

    def test_uses_correct_timeout(self):
        mock_response = MagicMock()
        mock_response.status = 204
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            cam_notifier.post_webhook("http://x", {}, timeout=10)

        self.assertEqual(mock_urlopen.call_args[1]["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
