#!/usr/bin/env python3
"""Exercise staging, installer failure and retry using the real guest runtime control path."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'switcher/assets/guest_runtime.sh').read_text()


class GuestManagerUpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.adb = self.root / 'adb'
        for child in ('oneos', 'modules', 'modules_update'):
            (self.adb / child).mkdir(parents=True)
        self.input = self.root / 'app/files/oneos-update/OneOSGuest.apk'
        self.input.parent.mkdir(parents=True)
        self.input.write_bytes(b'test signed APK bytes')
        self.localtmp = self.root / 'localtmp'
        self.localtmp.mkdir()
        self.folder = self.adb / 'oneos/manager-update'
        self.script = self.root / 'runtime.sh'
        tools = self.root / 'bin'
        tools.mkdir()
        for name, command in {
            'busybox': '''#!/bin/sh
[ "$1" = timeout ] && [ "$2" = -s ] && [ "$3" = KILL ] || exit 2
bound=$4
shift 4
case "$1:$bound" in pm:45|am:5) ;; *) exit 2 ;; esac
exec "$@"
''',
            'pm': f'''#!/bin/sh
printf '%s\\n' "$*" > '{self.root}/pm.args'
if [ -f '{self.root}/pm.timeout' ]; then exit 137; fi
if [ -f '{self.root}/pm.fail' ]; then
    echo 'Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]'
    exit 1
fi
echo Success
''',
            'am': f'''#!/bin/sh
printf '%s\\n' "$*" > '{self.root}/am.args'
''',
        }.items():
            path = tools / name
            path.write_text(command)
            path.chmod(0o700)
        text = SOURCE.replace('A=/data/adb', f'A={self.adb}')
        text = text.replace('/data/user/0/com.fahadali.oneos.guest/files/oneos-update/OneOSGuest.apk', str(self.input))
        text = text.replace('/data/local/tmp/oneos-manager-install.', str(self.localtmp / 'install.'))
        text = text.replace('/data/oneos_bin/busybox', str(tools / 'busybox'))
        self.script.write_text(text)
        self.shell = f'''
export PATH='{tools}':"$PATH"
restorecon() {{ :; }}
. '{self.script}'
'''

    def tearDown(self):
        self.tmp.cleanup()

    def run_control(self, operation, *args):
        return subprocess.run(['sh', '-c', self.shell, 'sh', 'control', 'manager-update-' + operation, *map(str, args)],
                              capture_output=True, text=True, timeout=10)

    def stage(self, digest=None):
        return self.run_control('stage', self.input, self.input.stat().st_size,
                                digest or hashlib.sha256(self.input.read_bytes()).hexdigest(), 156)

    def test_stage_and_apply_uses_guest_package(self):
        self.assertEqual(0, self.stage().returncode)
        self.assertEqual('pending 156', self.run_control('status').stdout.strip())
        self.input.write_bytes(b'changed after staging')
        self.assertEqual(b'test signed APK bytes', (self.folder / 'pending/OneOSGuest.apk').read_bytes())
        self.assertEqual(0, self.run_control('apply').returncode)
        args = (self.root / 'pm.args').read_text()
        self.assertIn('install -r --user 0 --pkg com.fahadali.oneos.guest ', args)
        self.assertNotIn(' -d ', args)
        self.assertFalse((self.folder / 'pending').exists())
        self.assertEqual([], list(self.localtmp.iterdir()))

    def test_bad_hash_and_symlink_are_rejected(self):
        self.assertNotEqual(0, self.stage('0' * 64).returncode)
        self.assertFalse((self.folder / 'pending').exists())
        other = self.input.with_suffix('.other')
        self.input.rename(other)
        self.input.symlink_to(other)
        self.assertNotEqual(0, self.stage().returncode)

    def test_pending_update_is_not_replaced(self):
        self.assertEqual(0, self.stage().returncode)
        self.assertNotEqual(0, self.stage().returncode)
        self.assertEqual('pending 156', self.run_control('status').stdout.strip())

    def test_installer_failure_can_be_retried(self):
        self.stage()
        (self.root / 'pm.fail').touch()
        self.assertNotEqual(0, self.run_control('apply').returncode)
        self.assertIn('INSTALL_FAILED_UPDATE_INCOMPATIBLE', self.run_control('status').stdout)
        self.assertTrue((self.folder / 'pending').exists())
        (self.root / 'pm.fail').unlink()
        self.assertEqual(0, self.run_control('apply').returncode)
        self.assertEqual('idle', self.run_control('status').stdout.strip())

    def test_corrupted_queue_never_reaches_installer(self):
        self.stage()
        (self.folder / 'pending/OneOSGuest.apk').write_bytes(b'corrupted')
        self.assertNotEqual(0, self.run_control('apply').returncode)
        self.assertFalse((self.root / 'pm.args').exists())
        self.assertIn('failed its checksum', self.run_control('status').stdout)
        self.assertIn('com.fahadali.oneos.guest/.GuestUpdatesActivity',
                      (self.root / 'am.args').read_text())
        self.assertEqual(0, self.run_control('cancel').returncode)
        self.assertEqual('idle', self.run_control('status').stdout.strip())

    def test_timed_out_installer_leaves_update_pending_and_reopens_ui(self):
        self.assertEqual(0, self.stage().returncode)
        (self.root / 'pm.timeout').touch()
        self.assertNotEqual(0, self.run_control('apply').returncode)
        self.assertIn('pending 156\nThe guest update installer timed out.',
                      self.run_control('status').stdout)
        self.assertIn('com.fahadali.oneos.guest/.GuestUpdatesActivity',
                      (self.root / 'am.args').read_text())
        self.assertFalse((self.folder / 'lock').exists())
        self.assertEqual([], list(self.localtmp.iterdir()))
        (self.root / 'pm.timeout').unlink()
        self.assertEqual(0, self.run_control('apply').returncode)


if __name__ == '__main__':
    unittest.main()
