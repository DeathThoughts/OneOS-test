#!/usr/bin/env python3
"""One UI face metadata must stay with One UI.

Face is guest-local in facehal-rs.  guest_prepare.sh must not copy One UI's
face settings into the guest and guest_start.sh must not move them aside.
Older builds did move them; return_to_oneui.sh has to restore them without
overwriting a current file.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / 'switcher/assets/guest_prepare.sh'
START = ROOT / 'switcher/assets/guest_start.sh'
RETURN = ROOT / 'switcher/assets/return_to_oneui.sh'
FACE_FILES = ('settings_face.xml', 'face_enrolled_position_0.xml')


def restore_function():
    source = RETURN.read_text()
    start = source.index('restore_oneos_face_metadata() {')
    end = source.index('\n}\n', start) + 3
    return source[start:end]


class FaceMetadataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.function = restore_function()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oneos-face-metadata-')
        self.root = Path(self.temp.name)
        self.backup = self.root / 'oneos-face-host'
        self.marker = self.root / 'marker'

    def tearDown(self):
        self.temp.cleanup()

    def restore(self):
        body = (self.function
                .replace('/data/system/users/0/oneos-face-host', '$ROOT/oneos-face-host')
                .replace('/data/system/users/0/$f', '$ROOT/$f')
                .replace('/data/oneos_facehal_moved', '$ROOT/marker'))
        script = body + '\nrestore_oneos_face_metadata\n'
        return subprocess.run(
            ['sh', '-ec', script],
            env={**os.environ, 'ROOT': str(self.root)},
            capture_output=True, text=True,
        )

    def test_guest_prepare_does_not_copy_face_metadata(self):
        source = PREPARE.read_text()
        self.assertNotIn('for f in settings_face.xml face_enrolled_position_0.xml', source)
        self.assertNotIn('cp -f "/data/system/users/0/$f" "$BIO_DST/$f"', source)

    def test_guest_start_does_not_move_face_metadata(self):
        source = START.read_text()
        self.assertNotIn('mv -f /data/system/users/0/settings_face.xml', source)
        self.assertNotIn('touch /data/oneos_facehal_moved', source)
        self.assertNotIn('oneos-face-host', source)

    def test_restore_runs_before_oneui_face_hal(self):
        source = RETURN.read_text()
        restore_at = source.index('restore_oneos_face_metadata ||')
        face_hal_at = source.index('setprop ctl.start sec-biometrics-face-aidl')
        self.assertLess(restore_at, face_hal_at)

    def test_restores_moved_files_and_clears_marker(self):
        self.backup.mkdir()
        for name in FACE_FILES:
            (self.backup / name).write_text(name)
        self.marker.write_text('moved')
        result = self.restore()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        for name in FACE_FILES:
            self.assertEqual(name, (self.root / name).read_text())
            self.assertFalse((self.backup / name).exists())
        self.assertFalse(self.marker.exists())

    def test_missing_backup_clears_stale_marker(self):
        self.marker.write_text('moved')
        result = self.restore()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(self.marker.exists())

    def test_current_file_is_not_overwritten(self):
        self.backup.mkdir()
        (self.backup / 'settings_face.xml').write_text('old')
        (self.root / 'settings_face.xml').write_text('current')
        self.marker.write_text('moved')
        result = self.restore()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('current', (self.root / 'settings_face.xml').read_text())
        self.assertEqual('old', (self.backup / 'settings_face.xml').read_text())
        self.assertFalse(self.marker.exists())
        self.assertIn('already exists', result.stdout)

    def test_symlinked_destination_is_not_overwritten(self):
        self.backup.mkdir()
        (self.backup / 'settings_face.xml').write_text('old')
        (self.root / 'settings_face.xml').symlink_to('/data/system/users/0/settings_face.xml')
        self.marker.write_text('moved')
        result = self.restore()
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(self.marker.exists())
        self.assertTrue((self.backup / 'settings_face.xml').exists())

    def test_symlinked_backup_is_refused(self):
        self.backup.mkdir()
        (self.backup / 'settings_face.xml').symlink_to('/data/system/users/0/settings_face.xml')
        self.marker.write_text('moved')
        result = self.restore()
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(self.marker.exists())
        self.assertTrue((self.backup / 'settings_face.xml').is_symlink())

    def test_non_regular_backup_is_refused(self):
        self.backup.mkdir()
        (self.backup / 'settings_face.xml').mkdir()
        self.marker.write_text('moved')
        result = self.restore()
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(self.marker.exists())
        self.assertTrue((self.backup / 'settings_face.xml').is_dir())

    def test_symlinked_backup_directory_is_refused(self):
        self.backup.symlink_to('/data/system/users/0/oneos-face-host')
        self.marker.write_text('moved')
        result = self.restore()
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
