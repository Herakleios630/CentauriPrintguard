import asyncio
import json
import os
import unittest
from collections import deque
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from alarm_state import AlarmState
from printguard.ai import build_analysis_prompt, extract_verdict
from printguard.camera import redact_url
from printguard.camera_coordinator import CameraCoordinator
from printguard.configuration import load_config
from printguard.diagnostics import DryRunDiagnostics
from printguard.monitor import _capture_views, _pause_cooldown_active
from printguard.printer import PRINT_STATUS_NAMES
from printguard.review import save_review_frames


class FakeCamera:
    def __init__(self, role, frame):
        self.role = role
        self.frame = frame
        self.last_success_at = None

    def grab_frame(self):
        self.last_success_at = 1.0
        return self.frame


class ReconnectingCamera(FakeCamera):
    def __init__(self, role, frame):
        super().__init__(role, frame)
        self.label = role
        self.reconnect_calls = 0
        self.failed_once = True

    def grab_frame(self):
        if self.failed_once:
            self.failed_once = False
            raise RuntimeError("stream unavailable")
        return super().grab_frame()

    def reconnect(self):
        self.reconnect_calls += 1


class MultiViewTests(unittest.TestCase):
    def test_current_configuration_resolves_secondary_from_environment(self):
        expected_url = "rtsp://user:example-password@10.0.0.88:554/stream1"
        with patch.dict(os.environ, {"CENTAURI_CAMERA_2_RTSP": expected_url}):
            config = load_config()
        self.assertEqual(config["cameras"]["secondary"]["url"], expected_url)
        self.assertEqual(config["cameras"]["primary"]["label"], "Frontansicht")

    def test_missing_secondary_environment_is_rejected_without_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "CENTAURI_CAMERA_2_RTSP"):
                load_config()

    def test_redact_url_hides_password(self):
        redacted = redact_url("rtsp://user:secret@10.0.0.88:554/stream1")
        self.assertEqual(redacted, "rtsp://user:***@10.0.0.88:554/stream1")
        self.assertNotIn("secret", redacted)

    def test_capture_views_keeps_camera_roles(self):
        cameras = [FakeCamera("primary", b"front"), FakeCamera("secondary", b"side")]
        frames = asyncio.run(_capture_views(cameras))
        self.assertEqual(frames, {"primary": b"front", "secondary": b"side"})

    def test_camera_coordinator_reconnects_failed_camera_independently(self):
        async def scenario():
            camera = ReconnectingCamera("secondary", b"side")
            coordinator = CameraCoordinator([camera])
            with patch("printguard.camera_coordinator.asyncio.sleep", new_callable=AsyncMock):
                views = await coordinator.capture_views()
            self.assertEqual(views, {"secondary": b"side"})
            self.assertEqual(camera.reconnect_calls, 1)

        asyncio.run(scenario())

    def test_review_writes_distinct_camera_files_and_metadata(self):
        entry = {
            "frame": b"front",
            "views": [
                {
                    "frame": b"front",
                    "camera_role": "primary",
                    "camera_label": "Frontansicht",
                    "captured_at": "2026-08-10T10:00:00",
                    "age_seconds": 0.4,
                    "available": True,
                },
                {
                    "frame": b"side",
                    "camera_role": "secondary",
                    "camera_label": "Seitenansicht",
                    "captured_at": "2026-08-10T10:00:01",
                    "age_seconds": 0.5,
                    "available": True,
                },
            ],
            "check": 1,
            "captured_at": "2026-08-10T10:00:00",
            "verdict": "OK",
            "time_offset_seconds": 1.0,
        }
        with TemporaryDirectory() as directory:
            output = save_review_frames(
                deque([entry]), directory, datetime(2026, 8, 10, 10, 0, 2)
            )
            self.assertTrue((Path(output) / "frame_01_primary.jpg").exists())
            self.assertTrue((Path(output) / "frame_01_secondary.jpg").exists())
            metadata = json.loads((Path(output) / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["frame_count"], 2)
        self.assertEqual(
            [frame["camera_role"] for frame in metadata["frames"]],
            ["primary", "secondary"],
        )

    def test_dry_run_ring_buffer_overwrites_first_slot(self):
        with TemporaryDirectory() as directory:
            diagnostics = DryRunDiagnostics(directory, pair_count=2)
            views = [
                {"frame": b"front", "camera_role": "primary", "camera_label": "Frontansicht"},
                {"frame": b"side", "camera_role": "secondary", "camera_label": "Seitenansicht"},
            ]
            diagnostics.save_pair(1, views, "2026-08-10T10:00:00")
            diagnostics.save_pair(2, views, "2026-08-10T10:00:10")
            diagnostics.save_pair(3, [{**view, "frame": b"new"} for view in views], "2026-08-10T10:00:20")
            self.assertEqual((Path(directory) / "pair_01_primary.jpg").read_bytes(), b"new")
            self.assertEqual((Path(directory) / "pair_02_primary.jpg").read_bytes(), b"front")

    def test_diagnostic_prompt_and_verdict_extraction(self):
        prompt = build_analysis_prompt([("Frontansicht / Bild 1", b"front")], diagnostic=True)
        self.assertIn("BEOBACHTUNGEN_FRONT", prompt)
        self.assertEqual(extract_verdict("UNSICHER: Druckkopf verdeckt\nBEGRUENDUNG: unklar"), "UNSICHER: Druckkopf verdeckt")

    def test_uncertain_evidence_preserves_pending_alarm(self):
        alarm = AlarmState(required_errors=2)
        first = alarm.observe("FEHLER: SPAGHETTI", b"front")
        uncertain = alarm.observe("UNSICHER: Kameraevidenz unvollständig", None)

        self.assertEqual(first.action, "COLLECT")
        self.assertEqual(uncertain.action, "COLLECT")
        self.assertEqual(alarm.state, "ALARM_PENDING")
        self.assertEqual(alarm.catastrophe, "SPAGHETTI")
        self.assertEqual(alarm.unknown_count, 1)
        self.assertEqual(len(alarm.frames), 2)

    def test_ok_requires_configured_streak_to_clear_pending_alarm(self):
        alarm = AlarmState(confirmation_frames=3, required_errors=2, clear_ok_count=2)

        alarm.observe("FEHLER: SPAGHETTI", b"front")
        first_ok = alarm.observe("OK", b"front")
        cleared = alarm.observe("OK", b"front")

        self.assertEqual(first_ok.action, "COLLECT")
        self.assertEqual(first_ok.state, "ALARM_PENDING")
        self.assertEqual(cleared.action, "RESET")
        self.assertEqual(cleared.state, "ALARM_CLEARED")
        self.assertEqual(alarm.ok_streak, 2)

    def test_pause_cooldown_only_defers_until_expired(self):
        self.assertTrue(_pause_cooldown_active(100.0, 120.0, 60.0))
        self.assertFalse(_pause_cooldown_active(100.0, 160.0, 60.0))
        self.assertFalse(_pause_cooldown_active(None, 120.0, 60.0))

    def test_diagnostic_prompt_is_saved_once_and_referenced(self):
        with TemporaryDirectory() as directory:
            diagnostics = DryRunDiagnostics(directory)
            pair = {"slot": 1, "views": []}
            diagnostics.save_analysis(
                1,
                "2026-08-10T10:00:00",
                pair,
                {"prompt": "prompt text", "raw_response": "OK", "verdict": "OK"},
            )
            record = json.loads((Path(directory) / "analysis_000001.json").read_text(encoding="utf-8"))
            prompt_file = Path(directory) / record["prompt_file"]
            prompt_content = prompt_file.read_text(encoding="utf-8")
            prompt_count = len(list(Path(directory).glob("prompt_*.txt")))

        self.assertNotIn("prompt", record)
        self.assertEqual(prompt_content, "prompt text")
        self.assertEqual(prompt_count, 1)

    def test_print_status_13_has_a_name(self):
        self.assertEqual(PRINT_STATUS_NAMES[13], "Printing")


if __name__ == "__main__":
    unittest.main()
