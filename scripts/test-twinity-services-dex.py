#!/usr/bin/env python3
"""Exercise the Twinity services DEX layout helper on decoded fixtures."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "switcher/patches/twinity_services_dex.py"
PATCH_SCRIPT = ROOT / "scripts/make-overlays.sh"
FAMILY = (
    "INetd",
    "INetd$Default",
    "INetd$Stub",
    "INetd$Stub$Proxy",
)


class TwinityServicesDexTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.decoded = Path(scratch.name)
        self.source = self.decoded / "smali/android/net"
        self.source.mkdir(parents=True)
        self.other_dex = self.decoded / "smali_classes9/android/net"
        self.other_dex.mkdir(parents=True)
        self.destination = self.decoded / "smali_classes10"
        self.contents = {}
        for name in FAMILY:
            filename = f"{name}.smali"
            data = f"# generated\r\n.class public Landroid/net/{name};\r\n.super Ljava/lang/Object;\r\n".encode()
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
        unrelated.write_bytes(b".class public Landroid/net/Other;\n")
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "smali smali_classes10\n")
        moved = self.destination / "android/net"
        self.assertEqual({path.name: path.read_bytes() for path in moved.iterdir()}, self.contents)
        self.assertEqual(list(self.source.iterdir()), [unrelated])
        self.assertEqual(unrelated.read_bytes(), b".class public Landroid/net/Other;\n")
        self.assertTrue(self.other_dex.is_dir())

    def test_missing_family_member_leaves_tree_unchanged(self):
        (self.source / "INetd$Default.smali").unlink()
        self.assert_rejected("missing smali sources")

    def test_duplicate_in_another_dex_leaves_tree_unchanged(self):
        name = "INetd.smali"
        (self.other_dex / name).write_bytes(self.contents[name])
        self.assert_rejected("unexpected INetd family source")

    def test_duplicate_descriptor_under_another_name_leaves_tree_unchanged(self):
        (self.other_dex / "Aliased.smali").write_bytes(self.contents["INetd.smali"])
        self.assert_rejected("unexpected INetd family source")

    def test_extra_family_member_leaves_tree_unchanged(self):
        (self.source / "INetd$Extra.smali").write_bytes(
            b".class public Landroid/net/INetd$Extra;\n"
        )
        self.assert_rejected("unexpected INetd family source")

    def test_symlink_source_leaves_tree_unchanged(self):
        name = "INetd$Default.smali"
        (self.source / name).unlink()
        target = self.decoded / "backing.smali"
        target.write_bytes(self.contents[name])
        (self.source / name).symlink_to(target)
        self.assert_rejected("unexpected symlink in decoded dex")

    def test_wrong_descriptor_leaves_tree_unchanged(self):
        (self.source / "INetd$Default.smali").write_bytes(
            self.contents["INetd.smali"]
        )
        self.assert_rejected("expected exactly one .class for Landroid/net/INetd$Default;")

    def test_occupied_destination_leaves_tree_unchanged(self):
        self.destination.write_bytes(b"occupied")
        self.assert_rejected("destination already exists")

    def test_duplicate_descriptor_after_unrelated_declaration_leaves_tree_unchanged(self):
        (self.other_dex / "Aliased.smali").write_bytes(
            b".class public Landroid/net/Aliased;\n" + self.contents["INetd.smali"]
        )
        self.assert_rejected("unexpected INetd family source")

    def test_second_class_declaration_in_family_member_leaves_tree_unchanged(self):
        name = "INetd$Stub.smali"
        (self.source / name).write_bytes(self.contents[name] + self.contents[name])
        self.assert_rejected("expected exactly one .class for Landroid/net/INetd$Stub;")

    def test_symlink_directory_leaves_tree_unchanged(self):
        (self.other_dex / "alias").symlink_to(self.source, target_is_directory=True)
        self.assert_rejected("unexpected directory in decoded dex")

    def test_overlay_gate_uses_original_jar_and_rebuilds_both_dex_entries(self):
        script = PATCH_SCRIPT.read_text()
        pin = "f669c8373f10043d674cc3cd7391627de4d6d958fe5ebb8b5dd303ab46e64270"
        hash_line = 'SERVICES_SHA=$(sha256sum < "$WORK/services.jar")'
        invocation = 'KEY_DIRS="$KEY_DIRS $(python3 -I "$ROOT/switcher/patches/twinity_services_dex.py" "$WORK/services")"'
        block = "\n".join((
            hash_line,
            f'if [ "${{SERVICES_SHA%% *}}" = {pin} ] ||',
            '   { [ "${ONEOS_COLOROS_PARITY:-0}" = 1 ] && [ "${IMAGE_SHA%% *}" = eec6c10581657b17f3164d6f964c749b351c4a7ec07233840da5f6a0ce28a47c ]; }; then',
            f"    {invocation}",
            "fi",
        ))
        self.assertEqual(script.count("twinity_services_dex.py"), 1)
        self.assertIn(block, script)
        self.assertLess(script.index("qti_builtin_display_public.py"), script.index(block))
        self.assertLess(script.index(block), script.index('"$WORK/rebuilt.jar"'))
        self.assertIn('"$DISPLAY_DIR" $KEY_DIRS | sort -u', script)


if __name__ == "__main__":
    unittest.main()
