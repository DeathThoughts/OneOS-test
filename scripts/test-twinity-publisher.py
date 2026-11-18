#!/usr/bin/env python3
"""Test the Twinity publisher without downloading or uploading release assets."""
import copy
import importlib.util
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "publish_twinity_payloads", Path(__file__).with_name("publish-twinity-payloads.py")
)
publisher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publisher
SPEC.loader.exec_module(publisher)

TARGET = "a" * 40
BUILDER = runpy.run_path(str(Path(__file__).with_name("build-twinity-image.py")))


def provenance():
    return BUILDER["metadata"](Path("/build/system.img"), {
        "size": publisher.IMAGE_SIZE, "sha256": "b" * 64,
    })


class OverlayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.overlays = self.work / "overlays"
        self.overlays.mkdir()
        self.expected = publisher.GENERATED | publisher.BUILD_ONLY
        for name in self.expected:
            (self.overlays / name).write_text(name)
        self.pins = {name: publisher.file_pin(self.overlays / name) for name in publisher.GUARDED}
        self.header = "# " + ", ".join(sorted(publisher.GUARD_WORDS))
        self.rows = [f"{self.pins[name].sha}  {name}" for name in sorted(publisher.GUARDED)]
        self.guards = self.overlays / "guards.txt"
        self.write_guards(self.header, self.rows)

    def write_guards(self, header, rows):
        self.guards.write_text("\n".join([header, *rows]) + "\n")

    def test_overlay_inventory(self):
        self.assertEqual(len(publisher.validate_files(self.overlays, self.expected)), 32)
        self.assertEqual(len(publisher.GENERATED), 27)
        self.assertEqual(len(publisher.GUARDED), 8)
        (self.overlays / "unknown.apk").write_bytes(b"x")
        (self.overlays / "services_patched.jar").rename(self.overlays / "services patched.jar")
        with self.assertRaisesRegex(ValueError, "Payload names differ"):
            publisher.validate_files(self.overlays, self.expected)

    def test_symlink_and_invalid_size(self):
        payload = self.overlays / "vold_patched"
        payload.unlink()
        target = self.work / "external"
        target.write_bytes(b"payload")
        payload.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "Expected a regular"):
            publisher.validate_files(self.overlays, self.expected)
        payload.unlink()
        payload.touch()
        with self.assertRaisesRegex(ValueError, "Expected a regular"):
            publisher.validate_files(self.overlays, self.expected)
        with payload.open("wb") as output:
            output.truncate(publisher.MAX_PAYLOAD + 1)
        with self.assertRaisesRegex(ValueError, "Expected a regular"):
            publisher.validate_files(self.overlays, self.expected)

    def test_guard_rows(self):
        publisher.validate_guards(self.guards, self.pins)
        for rows, message in (
            (self.rows[:-1], "Missing guards.txt"),
            (self.rows + [self.rows[0]], "Invalid guards.txt pin"),
            ([f'{"f" * 64}  {self.rows[0].split()[1]}', *self.rows[1:]], "Invalid guards.txt pin"),
            ([self.rows[0].replace("  ", " "), *self.rows[1:]], "Invalid guards.txt line"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.write_guards(self.header, rows)
                publisher.validate_guards(self.guards, self.pins)

    def test_guard_header_requires_a_whole_word(self):
        self.write_guards(self.header.replace("secims", "nonsecims"), self.rows)
        with self.assertRaisesRegex(ValueError, "missing guard names.*secims"):
            publisher.validate_guards(self.guards, self.pins)

    def test_metadata_records_pins_without_build_only_files(self):
        files = publisher.validate_files(self.overlays, self.expected)
        pins = {name: publisher.file_pin(files[name]) for name in publisher.GENERATED}
        pins.update(publisher.LEGACY_ASSETS)
        out = self.work / "metadata"
        publisher.write_metadata(out, pins, TARGET, f"commit {TARGET}", provenance(),
                                 self.guards.read_bytes())
        inventory = json.loads((out / "inventory.json").read_text())
        self.assertEqual(len(inventory["payloads"]), 29)
        self.assertEqual(set(inventory["payloads"]), set(pins))
        self.assertEqual(inventory["image"], provenance()["image"])
        self.assertEqual((out / "twinity-s23.guards.txt").read_bytes(), self.guards.read_bytes())
        remotes = publisher.index_rows(out / "remote.additions.txt")
        self.assertTrue(remotes)
        self.assertEqual(set(remotes.values()), {publisher.REMOTE})


class ProvenanceTests(unittest.TestCase):
    def test_valid_metadata(self):
        publisher.validate_image_metadata(provenance())

    def test_rejects_source_pin_mismatch(self):
        data = copy.deepcopy(provenance())
        data["source_parts"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "pinned builder metadata: source_parts"):
            publisher.validate_image_metadata(data)

    def test_rejects_invalid_image_digest_or_size(self):
        for name, value in (("sha256", "broken"), ("size", "10238480384"), ("size", 1)):
            data = provenance()
            data["image"][name] = value
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Invalid image"):
                publisher.validate_image_metadata(data)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "GITHUB_REPOSITORY": publisher.REPO,
            "GITHUB_REF": f"refs/heads/{publisher.BRANCH}",
            "GITHUB_ACTIONS": "true", "CI": "true", "GITHUB_SHA": TARGET,
            "GITHUB_EVENT_NAME": "push", "GH_TOKEN": "test",
        }

    def test_publish_context(self):
        with patch.object(publisher, "git", side_effect=[TARGET, ""]):
            publisher.require_publish_context(self.env, TARGET)
        for key in self.env:
            with self.subTest(key=key), self.assertRaises(ValueError):
                publisher.require_publish_context({**self.env, key: ""}, TARGET)
        for git_result in (["b" * 40], [TARGET, "?? untracked_file"]):
            with patch.object(publisher, "git", side_effect=git_result):
                with self.assertRaises(ValueError):
                    publisher.require_publish_context(self.env, TARGET)

    def test_publish_build_provenance(self):
        publisher.require_build_info(f"commit {TARGET}\ndirty 0\n", TARGET)
        for bad in ("dirty 1", "commit b", "dirty 0\ndirty 0", f"commit {TARGET}"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                publisher.require_build_info(f"commit {TARGET}\n{bad}\n", TARGET)

    def test_existing_asset_mismatch(self):
        pin = publisher.Pin(5, "a" * 64)
        existing = {"state": "uploaded", "size": pin.size, "digest": f"sha256:{pin.sha}"}
        publisher.verify_asset(existing, pin, None)
        for invalid in ({"state": "new"}, {"size": 4}, {"digest": "sha256:other"}):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                publisher.verify_asset({**existing, **invalid}, pin, None)

    def test_existing_asset_download_fallback(self):
        pin = publisher.Pin(5, "a" * 64)
        existing = {"state": "uploaded", "size": pin.size}
        with tempfile.TemporaryDirectory() as work, patch.object(
            publisher, "download", side_effect=lambda url, path, size: path.write_bytes(b"wrong")
        ):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                publisher.verify_asset(existing, pin, work)


if __name__ == "__main__":
    unittest.main()
