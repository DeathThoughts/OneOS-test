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


def maybe_bootstrap_twinity_ci_port():
    import os
    import shutil
    import sys
    # Keep the guest-manager unit suite independent of the multi-GB Twinity port.
    # Only a dedicated port job may opt in to rebuilding ROM images.
    if os.environ.get('ONEOS_BOOTSTRAP_TWINITY_PORT') != '1':
        return
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        return
    if (ROOT / 'app-android/packages/twinity-4c-s23.sha256').is_file():
        return
    if not (ROOT / 'scripts/build-twinity-port.py').is_file():
        return
    print('=== Bootstrapping Twinity ColorOS 17 CN (TWNTY 4.0c) port & overlays in CI ===', flush=True)
    subprocess.run([
        'sh', '-euc',
        'sudo rm -rf /usr/share/dotnet /usr/local/graalvm /opt/ghc /usr/local/share/boost '
        '/opt/hostedtoolcache/CodeQL /usr/local/share/powershell /usr/share/swift || true; '
        'sudo apt-get update; '
        'sudo apt-get install -y --no-install-recommends '
        'e2fsprogs erofs-utils zstd pigz zip unzip bc cpio lz4 patch attr '
        'android-sdk-libsparse-utils python3-venv python3-pip p7zip-full brotli; '
        'rm -rf mysticgsi; '
        'git clone --depth 1 https://github.com/MysticGSI/mysticgsi.git mysticgsi; '
        'git -C mysticgsi fetch --depth 1 origin 825b850dd8ddff11fdb8ed024034fc00e94b1451; '
        'git -C mysticgsi checkout 825b850dd8ddff11fdb8ed024034fc00e94b1451; '
        'chmod -R a+rx mysticgsi/tools; '
        'python3 -m venv mysticgsi/.venv; '
        'if [ -f mysticgsi/requirements.txt ]; then mysticgsi/.venv/bin/pip install --quiet -r mysticgsi/requirements.txt; fi',
    ], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/test-twinity-port.py')], cwd=ROOT, check=True)
    env = os.environ.copy()
    env['MYSTIC_PYTHON'] = str(ROOT / 'mysticgsi/.venv/bin/python')
    apktools = sorted((ROOT / 'mysticgsi/tools/apktool').glob('*.jar'))
    if apktools:
        env['APKTOOL'] = str(apktools[0])
    env['EXTRACT_EROFS'] = str(ROOT / 'mysticgsi/tools/bin/Linux/x86_64/extract.erofs')
    (ROOT / 'out').mkdir(exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/build-twinity-port.py')], cwd=ROOT, env=env, check=True)
    (ROOT / 'dist').mkdir(exist_ok=True)
    shutil.copyfile(
        ROOT / 'out/twinity-metadata/twinity-4c-manifest.json',
        ROOT / 'dist/twinity-4c-update-manifest.json',
    )
    shutil.rmtree(ROOT / 'mysticgsi', ignore_errors=True)
    shutil.rmtree(ROOT / 'out/overlays_twinity_4c_r1', ignore_errors=True)
    subprocess.run([
        sys.executable, str(ROOT / 'scripts/wire-twinity-port.py'),
        '--manifest', str(ROOT / 'out/twinity-metadata/twinity-4c-manifest.json'),
    ], cwd=ROOT, check=True)
    subprocess.run(['sh', '-n', 'app-android/app/src/full/assets/root/oneos_root.sh'], cwd=ROOT, check=True)
    subprocess.run(
        ['./gradlew', '--console=plain', ':core:model:test', ':core:root:testDebugUnitTest'],
        cwd=ROOT / 'app-android', check=True,
    )


if __name__ == '__main__':
    maybe_bootstrap_twinity_ci_port()
    unittest.main()
