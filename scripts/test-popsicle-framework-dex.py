#!/usr/bin/env python3
"""Exercise the Popsicle framework DEX layout helper on decoded fixtures."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "switcher/patches/popsicle_framework_dex.py"
PATCH_SCRIPT = ROOT / "scripts/patch-framework-camera.sh"
FAMILY = (
    "INetworkManagementService",
    "INetworkManagementService$Default",
    "INetworkManagementService$Stub",
    "INetworkManagementService$Stub$Proxy",
)


class PopsicleFrameworkDexTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.decoded = Path(scratch.name)
        self.source = self.decoded / "smali_classes3/android/os"
        self.source.mkdir(parents=True)
        self.other_dex = self.decoded / "smali_classes9/android/os"
        self.other_dex.mkdir(parents=True)
        self.destination = self.decoded / "smali_classes10"
        self.contents = {}
        for name in FAMILY:
            filename = f"{name}.smali"
            data = f"# generated\r\n.class public Landroid/os/{name};\r\n.super Ljava/lang/Object;\r\n".encode()
            (self.source / filename).write_bytes(data)
            self.contents[filename] = data

    def run_helper(self):
        return subprocess.run(
            [sys.executable, "-I", str(HELPER), str(self.decoded)],
            capture_output=True,
            text=True,
            check=False,
        )

    def snapshot(self):
        tree = {}
        for path in self.decoded.rglob("*"):
            relative = path.relative_to(self.decoded)
            if path.is_symlink():
                tree[relative] = path.readlink()
            elif path.is_dir():
                tree[relative] = None
            else:
                tree[relative] = path.read_bytes()
        return tree

    def assert_rejected(self, message):
        before = self.snapshot()
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn(message, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_moves_entire_family_to_next_dex_without_changing_bytes(self):
        unrelated = self.source / "Other.smali"
        unrelated.write_bytes(b".class public Landroid/os/Other;\n")
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "smali_classes3 smali_classes10\n")
        moved = self.destination / "android/os"
        self.assertEqual({path.name: path.read_bytes() for path in moved.iterdir()}, self.contents)
        self.assertEqual(list(self.source.iterdir()), [unrelated])
        self.assertEqual(unrelated.read_bytes(), b".class public Landroid/os/Other;\n")
        self.assertTrue(self.other_dex.is_dir())

    def test_missing_family_member_leaves_tree_unchanged(self):
        (self.source / "INetworkManagementService$Default.smali").unlink()
        self.assert_rejected("missing smali_classes3 sources")

    def test_duplicate_in_another_dex_leaves_tree_unchanged(self):
        name = "INetworkManagementService.smali"
        (self.other_dex / name).write_bytes(self.contents[name])
        self.assert_rejected("unexpected INetworkManagementService family source")

    def test_duplicate_descriptor_under_another_name_leaves_tree_unchanged(self):
        (self.other_dex / "Aliased.smali").write_bytes(self.contents["INetworkManagementService.smali"])
        self.assert_rejected("unexpected INetworkManagementService family source")

    def test_extra_family_member_leaves_tree_unchanged(self):
        (self.source / "INetworkManagementService$Extra.smali").write_bytes(
            b".class public Landroid/os/INetworkManagementService$Extra;\n"
        )
        self.assert_rejected("unexpected INetworkManagementService family source")

    def test_symlink_source_leaves_tree_unchanged(self):
        name = "INetworkManagementService$Default.smali"
        (self.source / name).unlink()
        target = self.decoded / "backing.smali"
        target.write_bytes(self.contents[name])
        (self.source / name).symlink_to(target)
        self.assert_rejected("regular non-symlink file")

    def test_wrong_descriptor_leaves_tree_unchanged(self):
        (self.source / "INetworkManagementService$Default.smali").write_bytes(
            self.contents["INetworkManagementService.smali"]
        )
        self.assert_rejected("expected exactly one .class for Landroid/os/INetworkManagementService$Default;")

    def test_occupied_destination_leaves_tree_unchanged(self):
        self.destination.write_bytes(b"occupied")
        self.assert_rejected("destination already exists")

    def test_framework_script_rejects_unknown_optional_argument(self):
        jar = self.decoded / "framework.jar"
        jar.write_bytes(b"placeholder")
        result = subprocess.run(
            ["sh", str(PATCH_SCRIPT), str(jar), str(self.decoded), "--unknown"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--popsicle-dex-layout", result.stderr)
        self.assertFalse((self.decoded / "framework_patched.jar").exists())


if __name__ == "__main__":
    unittest.main()
