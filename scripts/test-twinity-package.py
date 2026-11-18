#!/usr/bin/env python3
"""Exercise Twinity packaging and publish gates without network or large files."""

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SCRIPT = Path(__file__).with_name("package-twinity-image.py")
SPEC = importlib.util.spec_from_file_location("twinity_package", SCRIPT)
pkg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pkg)
TARGET = "a" * 40


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PackageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.out = self.work / "out"
        self.image = self.work / "system.img"
        self.raw = b"\x00" * 1080 + b"\x53\xef" + bytes(range(256)) * 4
        self.image.write_bytes(self.raw)
        self.metadata = {
            "image": {"path": str(self.image), "size": len(self.raw), "sha256": sha(self.raw)},
            "release": "example Twinity source",
            "source_parts": [{"name": "source.zip.001", "size": 123, "sha256": "b" * 64}],
        }

    def test_package_inventory_matches_joined_zip_and_image(self):
        assets = pkg.package(self.image, self.metadata, self.out, TARGET)
        inventory = json.loads((self.out / "inventory.json").read_text())
        self.assertEqual(inventory["target"], TARGET)
        self.assertEqual(inventory["image"], {"name": "system.img", "size": len(self.raw), "sha256": sha(self.raw)})
        self.assertEqual(inventory["source"]["source_parts"], self.metadata["source_parts"])
        self.assertNotIn("path", inventory["image"])
        name = "OneOS_ColorOS17_Twinity4c.zip"
        self.assertEqual(inventory["zip"]["name"], name)
        self.assertEqual(len(inventory["parts"]), 1)
        part = inventory["parts"][0]
        self.assertEqual(part["name"], f"{name}.001")
        body = (self.out / part["name"]).read_bytes()
        self.assertEqual(part, {"name": f"{name}.001", "size": len(body), "sha256": sha(body)})
        self.assertEqual(inventory["zip"], {"name": name, "size": len(body), "sha256": sha(body)})
        fmt_version, = struct.unpack_from("<H", body, 4)
        self.assertEqual(fmt_version, 45)
        with zipfile.ZipFile(self.out / part["name"]) as archive:
            self.assertEqual(archive.namelist(), ["system.img"])
            self.assertEqual(archive.getinfo("system.img").compress_type, zipfile.ZIP_DEFLATED)
            self.assertEqual(archive.read("system.img"), self.raw)
        self.assertEqual(set(assets), {"inventory.json", "release-notes.md", f"{name}.001"})
        self.assertEqual(pkg.package(self.image, self.metadata, self.out, TARGET), assets)

    def test_truncated_image_is_rejected(self):
        self.image.write_bytes(self.raw[:-1])
        with self.assertRaisesRegex(pkg.PackageError, "size differs"):
            pkg.package(self.image, self.metadata, self.out, TARGET)
        self.assertFalse((self.out / "inventory.json").exists())

    def test_wrong_image_hash_is_rejected(self):
        self.image.write_bytes(self.raw[:-1] + b"X")
        with self.assertRaisesRegex(pkg.PackageError, "SHA-256 differs"):
            pkg.package(self.image, self.metadata, self.out, TARGET)

    def test_wrong_ext4_magic_is_rejected(self):
        invalid = b"\x00" * len(self.raw)
        self.image.write_bytes(invalid)
        self.metadata["image"]["sha256"] = sha(invalid)
        with self.assertRaisesRegex(pkg.PackageError, "ext4 superblock"):
            pkg.package(self.image, self.metadata, self.out, TARGET)

    def test_zip_with_additional_entry_is_rejected(self):
        path = self.work / "extra.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("system.img", self.raw)
            archive.writestr("unexpected.img", self.raw)
        with self.assertRaisesRegex(pkg.PackageError, "exactly one entry"):
            pkg.validate_zip(path, self.metadata["image"])

    def test_zip_with_same_size_wrong_image_is_rejected(self):
        path = self.work / "wrong.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("system.img", self.raw[:-1] + b"X")
        expected = {"size": len(self.raw), "sha256": sha(self.raw)}
        with self.assertRaisesRegex(pkg.PackageError, "ZIP entry SHA-256"):
            pkg.validate_zip(path, expected)

    def test_split_exact_boundary_and_tail(self):
        archive = self.work / "split.zip"
        for payload, expected_sizes in ((b"abcdefgh", [4, 4]), (b"abcdefghi", [4, 4, 1])):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory(dir=self.work) as staging:
                archive.write_bytes(payload)
                dest = Path(staging)
                joined, parts = pkg.split_zip(archive, dest, limit=4)
                self.assertEqual([part["size"] for part in parts], expected_sizes)
                self.assertEqual([part["name"] for part in parts], sorted(path.name for path in dest.iterdir()))
                self.assertEqual(b"".join(path.read_bytes() for path in sorted(dest.iterdir())), payload)
                self.assertEqual(joined, {"name": archive.name, "size": len(payload), "sha256": sha(payload)})

    def test_mismatching_existing_assets_are_preserved(self):
        assets = pkg.package(self.image, self.metadata, self.out, TARGET)
        damaged = next(name for name in assets if name.endswith(".001"))
        (self.out / damaged).write_bytes(b"existing")
        before = (self.out / "inventory.json").read_bytes()
        with self.assertRaisesRegex(pkg.PackageError, "existing output differs"):
            pkg.package(self.image, self.metadata, self.out, TARGET)
        self.assertEqual((self.out / damaged).read_bytes(), b"existing")
        self.assertEqual((self.out / "inventory.json").read_bytes(), before)

    def test_publish_gate_requires_all_ci_values_and_clean_git(self):
        env = {
            "GITHUB_ACTIONS": "true", "CI": "true", "GITHUB_REPOSITORY": "DeathThoughts/OneOS-test",
            "GITHUB_REF": "refs/heads/test/coloros-twinity-4c", "GITHUB_SHA": TARGET,
            "GITHUB_EVENT_NAME": "push", "GH_TOKEN": "fake",
        }
        with patch.object(pkg, "git", side_effect=[TARGET, ""]):
            pkg.require_publish_context(env, TARGET)
        for key in env:
            with self.subTest(key=key), patch.object(pkg, "git", side_effect=[TARGET, ""]):
                incomplete = dict(env)
                del incomplete[key]
                with self.assertRaises(pkg.PackageError):
                    pkg.require_publish_context(incomplete, TARGET)
        with patch.object(pkg, "git", side_effect=[TARGET, " M scripts/package-twinity-image.py"]):
            with self.assertRaisesRegex(pkg.PackageError, "clean checkout"):
                pkg.require_publish_context(env, TARGET)

    def test_existing_release_asset_checks_digest_and_state(self):
        content = b"part"
        expected = {"size": len(content), "sha256": sha(content)}
        uploaded = {"name": "split.zip.001", "state": "uploaded", "size": len(content),
                    "digest": f"sha256:{sha(content)}"}
        pkg.verify_asset(uploaded, expected, self.out)
        with self.assertRaisesRegex(pkg.PackageError, "different SHA-256"):
            pkg.verify_asset({**uploaded, "digest": "sha256:" + "0" * 64}, expected, self.out)
        with self.assertRaisesRegex(pkg.PackageError, "state or size"):
            pkg.verify_asset({**uploaded, "state": "new"}, expected, self.out)

    def test_existing_release_asset_without_digest_is_downloaded_and_hashed(self):
        asset = {"name": "split.zip.001", "id": 17, "state": "uploaded", "size": 4}
        expected = {"size": 4, "sha256": sha(b"part")}

        def download(*args, stdout, **kwargs):
            self.assertIn("repos/DeathThoughts/OneOS-test/releases/assets/17", args)
            stdout.write(b"part")

        with patch.object(pkg, "gh", side_effect=download):
            pkg.verify_asset(asset, expected, self.work)
            with self.assertRaisesRegex(pkg.PackageError, "downloaded release asset differs"):
                pkg.verify_asset(asset, {**expected, "sha256": sha(b"fail")}, self.work)


if __name__ == "__main__":
    unittest.main()
