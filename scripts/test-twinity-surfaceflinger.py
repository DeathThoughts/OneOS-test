#!/usr/bin/env python3
"""Exercise Twinity's SurfaceFlinger patch, optionally on the original binary."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = (ROOT / "scripts/make-overlays.sh").read_text()
COMMAND = 'python3 -I - "$WORK/surfaceflinger" "$OUT/surfaceflinger_patched"'
PATCH = OVERLAYS.split(f"{COMMAND} <<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
PIN = "4c51f07dd84fb8161f9791dd2a3403fce74e6a520784cfe426f60c6058f22445"
OFFSET = 0x470A58
OLD = bytes.fromhex("680200b4")
NEW = bytes.fromhex("13000014")
REAL_INPUT = os.environ.get("ONEOS_TWINITY_SF")


class TwinitySurfaceFlingerTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.source = Path(scratch.name) / "surfaceflinger"
        self.output = self.source.with_name("surfaceflinger_patched")
        self.fixture = b"IQtiComposer3Client".ljust(OFFSET, b"\0") + OLD + b"tail"

    def run_patch(self, data, *, fixture_pin=None):
        patch = PATCH
        if fixture_pin is not None:
            self.assertEqual(patch.count(PIN), 1)
            patch = patch.replace(PIN, hashlib.sha256(fixture_pin).hexdigest())
        self.source.write_bytes(data)
        result = subprocess.run(
            [sys.executable, "-I", "-", str(self.source), str(self.output)],
            input=patch,
            capture_output=True,
            text=True,
        )
        self.assertEqual(self.source.read_bytes(), data)
        return result

    def assert_only_branch_changed(self, data, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("QTI composer client skipped", result.stdout)
        self.assertEqual(data[OFFSET:OFFSET + 4], OLD)
        self.assertEqual(self.output.read_bytes(), data[:OFFSET] + NEW + data[OFFSET + 4:])

    def assert_rejected_without_clobber(self, data, *, fixture_pin=None, message):
        self.output.write_bytes(b"previous output")
        result = self.run_patch(data, fixture_pin=fixture_pin)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertEqual(self.output.read_bytes(), b"previous output")

    def test_pinned_fixture_changes_only_branch(self):
        self.assert_only_branch_changed(self.fixture, self.run_patch(self.fixture, fixture_pin=self.fixture))

    def test_changed_fixture_fails_pin_check_without_clobber(self):
        self.assert_rejected_without_clobber(
            self.fixture[:-1], fixture_pin=self.fixture, message="unexpected build"
        )

    def test_pinned_fixture_still_requires_exact_instruction(self):
        changed = self.fixture[:OFFSET] + b"\x00" + self.fixture[OFFSET + 1:]
        self.assert_rejected_without_clobber(
            changed, fixture_pin=changed, message=f"unexpected bytes at {OFFSET:#x}"
        )

    @unittest.skipUnless(REAL_INPUT, "set ONEOS_TWINITY_SF to the original Twinity SurfaceFlinger")
    def test_original_twinity_binary_and_corrupted_copy(self):
        data = Path(REAL_INPUT).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), PIN)
        self.assert_only_branch_changed(data, self.run_patch(data))
        self.assert_rejected_without_clobber(data[:-1], message="unexpected build")


if __name__ == "__main__":
    unittest.main()
