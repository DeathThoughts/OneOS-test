#!/usr/bin/env python3
"""Refuse guest-planted symlinks on the paths used by the biometric mirrors.

guest_prepare.sh copies the host's fingerprint metadata into the guest and
creates data/vendor_de/0/fpdata for the guest fingerprint HAL.  A symlink at
data/system/users/0 or data/vendor_de/0/fpdata would make that copy/removal
reach One UI's data.  The guard is tested here without a phone, root or mounts.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / 'switcher/assets/guest_prepare.sh'


def no_symlink_function():
    source = PREPARE.read_text()
    start = source.index('no_symlink() {')
    end = source.index('\n}\n', start) + 3
    return source[start:end]


class BiometricSymlinkGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = PREPARE.read_text()
        cls.function = no_symlink_function()
        start = source.index('for s in data ')
        cls.loop = source[start:source.index('\ndone\n', start) + len('\ndone\n')]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oneos-biometric-links-')
        self.stage = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_guard(self, relative):
        script = self.function + '\nno_symlink "$1"\n'
        return subprocess.run(
            ['sh', '-ec', script, 'guest-prepare-symlink-test', relative],
            env={**os.environ, 'STAGE': str(self.stage)},
            capture_output=True, text=True,
        )

    def run_loop(self):
        return subprocess.run(
            ['sh', '-ec', self.function + '\n' + self.loop],
            env={**os.environ, 'STAGE': str(self.stage)},
            capture_output=True, text=True,
        )

    def test_protected_paths_are_in_the_checked_loop(self):
        self.assertIn('system/users/0', self.loop)
        self.assertIn('vendor_de/0/fpdata', self.loop)

    def test_regular_biometric_paths_pass(self):
        (self.stage / 'data/system/users/0').mkdir(parents=True)
        (self.stage / 'data/vendor_de/0/fpdata').mkdir(parents=True)
        result = self.run_loop()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_symlink_at_system_users_0_is_refused(self):
        (self.stage / 'data/system/users').mkdir(parents=True)
        (self.stage / 'data/system/users/0').symlink_to('/data/system/users/0')
        result = self.run_loop()
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn('symlink', result.stdout)
        self.assertIn('system/users/0', result.stdout)

    def test_symlink_at_vendor_de_0_fpdata_is_refused(self):
        (self.stage / 'data/vendor_de/0').mkdir(parents=True)
        (self.stage / 'data/vendor_de/0/fpdata').symlink_to('/data/vendor_de/0/fpdata')
        result = self.run_loop()
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn('symlink', result.stdout)
        self.assertIn('vendor_de/0/fpdata', result.stdout)

    def test_symlinked_parent_is_refused(self):
        (self.stage / 'data').mkdir()
        (self.stage / 'data/vendor_de').symlink_to('/data/vendor_de')
        result = self.run_loop()
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        self.assertIn('symlink', result.stdout)

    def test_direct_guard_still_refuses_the_new_paths(self):
        (self.stage / 'data/system/users').mkdir(parents=True)
        (self.stage / 'data/system/users/0').symlink_to('/data/system/users/0')
        result = self.run_guard('system/users/0')
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
