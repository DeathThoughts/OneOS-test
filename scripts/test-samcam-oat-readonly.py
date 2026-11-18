#!/usr/bin/env python3
"""Samsung Camera compiles into the guest's /data/dalvik-cache (guest_prepare.sh).

S25 Ultra, coloros17-findx9u-in-1105, sessions db475ad5017133c4 and a12bf19e1931e81a
(2026-11-18): "com.sec.android.app.camera arm64 is run-from-apk ... result: Failure" in both
oneos_sysui_compile.log; dex2oat64 --oat-location=/system/system_ext/priv-app/SamsungCamera/oat/
arm64/SamsungCamera.odex died with SIGBUS (BUS_ADRERR in SwapAllocCompiledMethod), and artd.log
had "SELinux: setxattr failed: .../SamsungCamera/oat". The camera's folders exist only in the
system_ext overlay's 1 MiB tmpfs upper dir, and its APK was bound read-write from /data.

samcam_ro_dirs binds each APK's folder (from the same list samcam_bind uses) onto itself and
remounts it read-only, real folders only, before samcam_bind binds the APKs into them; a folder
whose read-only remount fails is unmounted again and left as before. samcam_ro_apks then remounts
the APK binds read-only (artd goes by the last mount above the APK that is not an overlay).
Nothing writes into those folders after them: samcam_bind only mounts, and no later step of
guest_prepare.sh names them. return_to_oneui.sh unmounts everything under the stage, deepest
first, so the new binds go with the rest.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'switcher/assets/guest_prepare.sh').read_text()
RETURN = (ROOT / 'switcher/assets/return_to_oneui.sh').read_text()


def function(name):
    return name + '() {' + SOURCE.split('\n' + name + '() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'


BUSYBOX = '''#!/bin/sh
c=$1; shift
if [ "$c" = mount ]; then echo "$*" >> "$CALLS"; else echo "$c $*" >> "$CALLS"; fi
[ "$c" = mount ] || exit 0
case "$1 $2" in
    "--bind $FAIL_BIND") exit 1 ;;
    "-o remount,bind,ro") [ "$4" = "$FAIL_RO" ] && exit 1 ;;
esac
exit 0
'''
LIST = ('sysext priv-app/SamsungCamera/SamsungCamera.apk /src/SamsungCamera.apk\n'
        'sysext app/OneOSSamFilterStub/OneOSSamFilterStub.apk /src/OneOSSamFilterStub.apk\n'
        'sysext framework/secimaging.jar /src/secimaging.jar\n'
        'sysext etc/permissions/oneos-samcam-secimaging.xml /src/oneos-samcam-secimaging.xml\n'
        'sys lib64/libexifa.camera.samsung.so /system/lib64/libexifa.camera.samsung.so\n'
        'sys cameradata /system/cameradata\n')


def run(fn, link=None, fail_bind='-', fail_ro='-', missing=None):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        mp = root / 'mp'
        for t in ('priv-app/SamsungCamera/SamsungCamera.apk', 'app/OneOSSamFilterStub/OneOSSamFilterStub.apk',
                  'framework/secimaging.jar', 'etc/permissions/oneos-samcam-secimaging.xml'):
            if missing and t.startswith(missing):
                continue
            (mp / t).parent.mkdir(parents=True, exist_ok=True)
            (mp / t).write_text('')
        if link:
            other = root / 'elsewhere'
            other.mkdir()
            (mp / link).rename(root / 'gone')
            os.symlink(other, mp / link)
        (root / 'list').write_text(LIST)
        bb = root / 'bb'
        bb.write_text(BUSYBOX)
        bb.chmod(0o755)
        env = dict(os.environ, BB=str(bb), SAMCAM_LIST=str(root / 'list'), CALLS=str(root / 'calls'),
                   FAIL_BIND=str(mp / fail_bind) if fail_bind != '-' else '-',
                   FAIL_RO=str(mp / fail_ro) if fail_ro != '-' else '-')
        out = subprocess.run(['/bin/sh', '-c', function(fn) + fn + ' "$1"', 'sh', str(mp)],
                             env=env, capture_output=True, text=True)
        calls = (root / 'calls').read_text().replace(str(mp) + '/', 'MP/').splitlines() if (root / 'calls').exists() else []
        return out.returncode, calls, out.stdout.replace(str(mp) + '/', 'MP/')


CAM, STUB = 'MP/priv-app/SamsungCamera', 'MP/app/OneOSSamFilterStub'


def ro(d):
    return ['--bind %s %s' % (d, d), '--make-rslave %s' % d, '-o remount,bind,ro %s %s' % (d, d)]


class ReadOnlyFolders(unittest.TestCase):
    def test_each_apk_folder_of_the_list_and_no_other(self):
        code, calls, out = run('samcam_ro_dirs')
        self.assertEqual(0, code)
        self.assertEqual(ro(CAM) + ro(STUB), calls)
        for d in (CAM, STUB):
            self.assertIn('Samsung Camera: %s read-only, so ART compiles it into /data/dalvik-cache '
                          '(the 1 MiB overlay upper dir made dex2oat die with SIGBUS)' % d, out)

    def test_only_real_folders(self):
        code, calls, _ = run('samcam_ro_dirs', link='app/OneOSSamFilterStub')
        self.assertEqual((0, ro(CAM)), (code, calls))
        code, calls, _ = run('samcam_ro_dirs', missing='priv-app/SamsungCamera')
        self.assertEqual((0, ro(STUB)), (code, calls))

    def test_a_refused_remount_is_undone_and_the_rest_goes_on(self):
        code, calls, out = run('samcam_ro_dirs', fail_ro='priv-app/SamsungCamera')
        self.assertEqual(0, code)
        self.assertEqual(ro(CAM) + ['-l ' + CAM] + ro(STUB), [c.replace('umount ', '') for c in calls])
        self.assertIn('Samsung Camera: %s left read-write (read-only bind refused)' % CAM, out)

    def test_a_refused_bind_unmounts_nothing(self):
        code, calls, out = run('samcam_ro_dirs', fail_bind='priv-app/SamsungCamera')
        self.assertEqual(0, code)
        self.assertEqual(['--bind %s %s' % (CAM, CAM)] + ro(STUB), calls)
        self.assertFalse([c for c in calls if c.startswith('umount')])
        self.assertIn('left read-write', out)

    def test_apk_binds_read_only(self):
        code, calls, _ = run('samcam_ro_apks')
        self.assertEqual(0, code)
        self.assertEqual(['-o remount,bind,ro /src/SamsungCamera.apk %s/SamsungCamera.apk' % CAM,
                          '-o remount,bind,ro /src/OneOSSamFilterStub.apk %s/OneOSSamFilterStub.apk' % STUB], calls)
        code, calls, out = run('samcam_ro_apks', fail_ro='priv-app/SamsungCamera/SamsungCamera.apk')
        self.assertEqual(0, code)
        self.assertIn('stays read-write (remount refused)', out)
        self.assertEqual(2, len(calls))

    def test_names_come_from_the_list(self):
        for fn in ('samcam_ro_dirs', 'samcam_ro_apks'):
            self.assertNotIn('SamsungCamera', function(fn))
            self.assertIn('$SAMCAM_LIST', function(fn))


class Order(unittest.TestCase):
    CALL = ('if mount | grep -q " $GUEST_SYSTEM_EXT type overlay" && samcam_ro_dirs "$GUEST_SYSTEM_EXT" &&\n'
            '               samcam_bind sysext "$GUEST_SYSTEM_EXT"; then SAMCAM_BOUND=1; samcam_ro_apks "$GUEST_SYSTEM_EXT"; else')

    def test_read_only_before_the_binds_and_after_the_mount_points(self):
        self.assertEqual(1, SOURCE.count(self.CALL))
        site = SOURCE.index(self.CALL)
        self.assertLess(SOURCE.index('samcam_mountpoints "$OV/up" sysext'), site)
        self.assertEqual(1, SOURCE.count('samcam_ro_dirs "'))
        self.assertEqual(1, SOURCE.count('samcam_ro_apks "'))

    def test_samcam_bind_only_mounts(self):
        body = function('samcam_bind')
        for w in ('mkdir', 'chcon', 'cp ', ': >', 'chmod', 'rm '):
            self.assertNotIn(w, body)

    def test_nothing_later_names_the_folders(self):
        site = SOURCE.index(self.CALL)
        later = SOURCE[site + len(self.CALL):]
        for n in ('SamsungCamera', 'OneOSSamFilterStub', '$OV/up/priv-app', '$OV/up/app'):
            self.assertNotIn(n, later)

    def test_the_return_unmounts_everything_under_the_stage(self):
        self.assertIn('''awk -v r="$STAGE_ROOT" 'index($2, r "/") == 1 {print $2}' /proc/mounts | sort -r''', RETURN)
        self.assertIn('ROOTFS="$STAGE/rootfs"', SOURCE)


if __name__ == '__main__':
    unittest.main()
