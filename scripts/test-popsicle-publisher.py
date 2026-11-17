#!/usr/bin/env python3
"""Exercise the payload publisher without building or contacting the release."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile


spec = importlib.util.spec_from_file_location(
    "publisher", Path(__file__).with_name("build-popsicle-payloads.py"))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PayloadPublisherTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.overlays = self.work / "payloads"
        self.overlays.mkdir()
        for name in publisher.PUBLISHED:
            (self.overlays / name).write_bytes(name.encode())
        self.guards = self.overlays / "guards.txt"
        self.guards.write_text("# guards\n" + "".join(
            f"{publisher.file_pin(self.overlays / name).sha}  {name}\n"
            for name in sorted(publisher.GUARDED)))
        legacy = {name: publisher.file_pin(self.overlays / name) for name in publisher.LEGACY_ASSETS}
        socket = publisher.file_pin(self.overlays / "socket_exec")
        for name, replacement in (("LEGACY_ASSETS", legacy), ("SOCKET_EXEC", socket)):
            patcher = patch.object(publisher, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_payload_names_and_guard_coverage_are_exact(self):
        self.assertEqual(publisher.validate_payloads(self.overlays).keys(), publisher.PUBLISHED)
        unknown = self.overlays / "unknown.jar"
        unknown.touch()
        with self.assertRaisesRegex(ValueError, "unexpected.*unknown.jar"):
            publisher.validate_payloads(self.overlays)
        unknown.unlink()

        lines = self.guards.read_text().splitlines(keepends=True)
        self.guards.write_text("".join(lines[:-1]))
        with self.assertRaisesRegex(ValueError, "Missing guards.txt pins"):
            publisher.validate_payloads(self.overlays)

        self.guards.write_text("".join(lines + [lines[-1]]))
        with self.assertRaisesRegex(ValueError, "Invalid guards.txt pin"):
            publisher.validate_payloads(self.overlays)

        self.guards.write_text("".join(lines))
        (self.overlays / "services_patched.jar").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "Invalid guards.txt pin for services_patched.jar"):
            publisher.validate_payloads(self.overlays)

    def test_payload_rejects_symlinks_empty_and_oversized_files(self):
        victim = self.overlays / "wifi_payload.img"
        victim.unlink()
        victim.symlink_to(self.guards)
        with self.assertRaisesRegex(ValueError, "Expected a regular"):
            publisher.validate_payloads(self.overlays)
        victim.unlink()
        victim.touch()
        with self.assertRaisesRegex(ValueError, "Expected a regular"):
            publisher.validate_payloads(self.overlays)
        with victim.open("wb") as stream:
            stream.truncate(publisher.MAX_PAYLOAD + 1)
        with self.assertRaisesRegex(ValueError, "Expected a regular"):
            publisher.validate_payloads(self.overlays)

    def test_extract_member_checks_shape_and_bytes(self):
        archive = self.work / "source.zip"
        name = "system.img"
        data = b"Popsicle"
        output = self.work / name
        output.write_bytes(data)
        pin = publisher.file_pin(output)
        output.unlink()
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as z:
            z.writestr(name, data)
            crc = z.getinfo(name).CRC
        publisher.extract_member(archive, output, name, ZIP_DEFLATED, crc, pin)
        self.assertEqual(output.read_bytes(), data)
        output.unlink()
        with self.assertRaisesRegex(ValueError, "unexpected entry"):
            publisher.extract_member(archive, output, name, ZIP_DEFLATED, 0, pin)
        self.assertFalse(output.exists())
        with ZipFile(archive, "a") as z:
            z.writestr("second.img", data)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            publisher.extract_member(archive, output, name, ZIP_DEFLATED, crc, pin)

    def test_release_validates_every_existing_asset_before_uploading(self):
        pins = publisher.validate_payloads(self.overlays)
        pin = pins["wifi_payload.img"]
        with patch.object(publisher, "release_assets", return_value={
            pin.sha: {"state": "uploaded", "size": pin.size, "digest": "sha256:wrong"},
        }), patch.object(publisher.subprocess, "run") as upload:
            with self.assertRaisesRegex(RuntimeError, "different digest"):
                publisher.publish(self.overlays, pins, self.work)
            upload.assert_not_called()

    def test_release_checks_download_when_digest_is_missing(self):
        path = self.overlays / "wifi_payload.img"
        pin = publisher.file_pin(path)
        asset = {"state": "uploaded", "size": pin.size}
        with patch.object(publisher, "download", side_effect=lambda url, output, size:
                          output.write_bytes(path.read_bytes())) as download:
            publisher.verify_asset(asset, pin, self.work)
            self.assertTrue(download.call_args.args[0].endswith(pin.sha))
        with patch.object(publisher, "download", side_effect=lambda url, output, size:
                          output.write_bytes(b"!" * size)):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                publisher.verify_asset(asset, pin, self.work)

    def test_release_uploads_only_missing_hashes_and_verifies_them_afterward(self):
        name = "wifi_payload.img"
        pin = publisher.file_pin(self.overlays / name)
        remote = {pin.sha: {"state": "uploaded", "size": pin.size, "digest": f"sha256:{pin.sha}"}}
        with patch.object(publisher, "release_assets", side_effect=[{}, remote]), \
                patch.object(publisher.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as upload:
            publisher.publish(self.overlays, {name: pin}, self.work)
            cmd = upload.call_args.args[0]
            self.assertEqual(cmd[:4], ["gh", "release", "upload", publisher.TAG])
            self.assertEqual(cmd[4].name, pin.sha)
            self.assertEqual(publisher.file_pin(cmd[4]), pin)
            self.assertEqual(cmd[5:], ["--repo", publisher.REPO])

    def test_publish_context_requires_fork_branch_and_same_checkout(self):
        target = "a" * 40
        env = {"GITHUB_REPOSITORY": publisher.REPO, "GITHUB_REF": f"refs/heads/{publisher.BRANCH}",
               "GITHUB_ACTIONS": "true", "CI": "true", "GITHUB_EVENT_NAME": "push",
               "GITHUB_SHA": target, "GH_TOKEN": "test"}
        publisher.require_publish_context(env, target, target)
        for name in env:
            with self.subTest(name=name), self.assertRaises(ValueError):
                publisher.require_publish_context(env | {name: ""}, target, target)
        with self.assertRaisesRegex(ValueError, "checked-out"):
            publisher.require_publish_context(env, target, "b" * 40)

    def test_metadata_lists_manifest_and_index_additions(self):
        pins = publisher.validate_payloads(self.overlays)
        publisher.write_metadata(self.work, pins, "a" * 40, "dirty 1\n")
        data = json.loads((self.work / "sources.additions.json").read_text())
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["payloads"], {
            pin.sha: {"bytes": pin.size, "sources": [publisher.REMOTE]} for pin in pins.values()
        })
        manifest = publisher.index_rows(self.work / "hyper-popsicle.sha256")
        self.assertEqual(manifest, {pin.sha: name for name, pin in pins.items()})
        inventory = json.loads((self.work / "inventory.json").read_text())
        self.assertEqual(inventory["image"]["system_img"]["sha"], publisher.SYSTEM.sha)
        self.assertEqual(inventory["payloads"]["wifi_payload.img"]["sha"], pins["wifi_payload.img"].sha)


if __name__ == "__main__":
    unittest.main()
