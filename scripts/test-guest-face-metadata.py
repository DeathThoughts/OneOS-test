#!/usr/bin/env python3
"""One UI face metadata must stay with One UI.

Face is guest-local in facehal-rs.  guest_prepare.sh never copies One UI's face
files into the guest and drops the guest's old copies; guest_start.sh never
moves face files and marks only a running facehal-rs; the return has nothing
of One UI's face to put back.
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


class FaceMetadataTest(unittest.TestCase):
    """One UI's face files are never copied, moved or restored by the session scripts."""

    def test_guest_prepare_does_not_copy_face_metadata(self):
        source = PREPARE.read_text()
        self.assertNotIn('cp -f "/data/system/users/0/$f" "$BIO_DST/$f"', source)
        # The only loop over the face files left is the stale copy cleanup, which copies nothing.
        self.assertNotIn('cp ', drop_function())
        self.assertNotIn('mv ', drop_function())

    def test_guest_start_does_not_move_face_metadata(self):
        source = START.read_text()
        self.assertNotIn('mv -f /data/system/users/0/settings_face.xml', source)
        self.assertNotIn('touch /data/oneos_facehal_moved', source)
        self.assertNotIn('mkdir -p /data/system/users/0/oneos-face-host', source)

    def test_return_leaves_one_ui_face_files_alone(self):
        # The old builds' move ran in the guest's /data: One UI's files never left, and the
        # return has nothing to put back (guest_prepare.sh drops the guest's old copies).
        source = RETURN.read_text()
        self.assertNotIn('oneos-face-host', source)
        self.assertNotIn('settings_face.xml', source)

    def test_face_hal_marker_only_while_facehal_runs(self):
        source = START.read_text()
        clear = source.index('rm -f /data/system/oneos_facehal_active')
        start = source.index('if [ -x "$FACEHAL_DIR/bin/hw/android.hardware.biometrics.face-service.facehal" ]')
        write = source.index(': > /data/system/oneos_facehal_active')
        self.assertLess(clear, start)
        self.assertLess(start, write)
        self.assertIn('kill -0 "$(cat /data/facehal.pid', source[start:write])


def drop_function():
    source = PREPARE.read_text()
    start = source.index('drop_oneui_face_copies() {')
    end = source.index('\n}\n', start) + 3
    return source[start:end]


class StaleFaceCopyTest(unittest.TestCase):
    """guest_prepare.sh removes the guest's old copies of One UI's face files.

    Where facehal-rs does not run, the guest's FaceService uses Samsung's face
    HAL, which holds One UI's face: a copy listed in the guest could be removed
    there and take One UI's face with it.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oneos-face-copies-')
        self.root = Path(self.temp.name)
        self.host = self.root / 'host'
        self.guest = self.root / 'stage/data/system/users/0'
        self.host.mkdir(parents=True)
        self.guest.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def drop(self):
        body = drop_function().replace('/data/system/users/0/$f', '$HOST/$f')
        script = body + '\ndrop_oneui_face_copies\n'
        return subprocess.run(
            ['sh', '-ec', script],
            env={**os.environ, 'HOST': str(self.host), 'STAGE': str(self.root / 'stage'),
                 'BIO_DST': str(self.guest)},
            capture_output=True, text=True,
        )

    def test_never_touches_one_ui(self):
        self.assertNotIn('rm -f "$h"', drop_function())
        self.assertNotIn('rm -rf "$h"', drop_function())

    def test_identical_copies_are_removed(self):
        for name in FACE_FILES:
            (self.host / name).write_text('one ui ' + name)
            (self.guest / name).write_text('one ui ' + name)
        result = self.drop()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        for name in FACE_FILES:
            self.assertFalse((self.guest / name).exists())
            self.assertEqual('one ui ' + name, (self.host / name).read_text())
        self.assertIn('removed 2 stale', result.stdout)

    def test_guest_local_enrollment_stays(self):
        (self.host / 'settings_face.xml').write_text('one ui')
        (self.guest / 'settings_face.xml').write_text('guest-local')
        result = self.drop()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('guest-local', (self.guest / 'settings_face.xml').read_text())
        self.assertEqual('', result.stdout)

    def test_guest_file_without_one_ui_counterpart_stays(self):
        (self.guest / 'settings_face.xml').write_text('guest-local')
        result = self.drop()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.guest / 'settings_face.xml').exists())

    def test_symlinked_guest_file_is_not_followed(self):
        (self.host / 'settings_face.xml').write_text('one ui')
        (self.guest / 'settings_face.xml').symlink_to(self.host / 'settings_face.xml')
        result = self.drop()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.guest / 'settings_face.xml').is_symlink())
        self.assertEqual('one ui', (self.host / 'settings_face.xml').read_text())

    def test_old_moved_folder_and_marker_go(self):
        old = self.guest / 'oneos-face-host'
        old.mkdir()
        (old / 'settings_face.xml').write_text('copy')
        marker = self.root / 'stage/data/oneos_facehal_moved'
        marker.write_text('')
        result = self.drop()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(old.exists())
        self.assertFalse(marker.exists())

    def test_symlinked_moved_folder_is_left(self):
        target = self.root / 'elsewhere'
        target.mkdir()
        (target / 'keep').write_text('keep')
        (self.guest / 'oneos-face-host').symlink_to(target)
        result = self.drop()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('keep', (target / 'keep').read_text())

    def test_runs_after_the_fingerprint_mirror(self):
        source = PREPARE.read_text()
        self.assertLess(source.index('BIO_DST=$STAGE/data/system/users/0'),
                        source.index('\ndrop_oneui_face_copies\n'))


if __name__ == '__main__':
    unittest.main()
