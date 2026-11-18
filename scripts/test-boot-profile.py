#!/usr/bin/env python3
"""Profiles rebuilt for OneOS's patched jars (guest_prepare.sh copies the stock jars, guest_start.sh runs profman).

S25 Ultra, ColorOS 17 coloros17-findx9u-in-1105, sessions db19aee410bd79ff and 75e538ac0ed725df:
SystemUI's Looper/MessageQueue frames ran from boot.vdex under nterp_helper, one android.* method was
in boot.oat. ART keys a profile by dex checksum, so odrefresh's speed-profile compile found no profile
data for the patched jars.

Both blocks are cut out of the real scripts and run under a temporary root with fake profman,
odrefresh and busybox ($BB records its calls; mount --bind is a hard link). Absolute paths of the guest
are rewritten to the temporary root. Checked:
- guest_prepare.sh: new key copies the stock jars and writes stock.list and pending; `K ok` and
  `K failed` copy nothing and remove stale files; a still-covered jar (same inode) is skipped; a
  symlinked oneos_prof is refused (exit 3); ONEOS_GUEST_BOOT_PROFILE=0 does nothing;
- guest_start.sh: success writes `K ok`, makes the profiles, binds them and removes stock and
  pending; a failing profman writes `K failed` and binds nothing; --force-compile runs only when
  $ADC/oneos_prof differs; its failure undoes the binds and writes `K failed`.

Session db475ad5017133c4 (the same phone and image): boot-image.prof failed (exit 255) and the two
good system server profiles were thrown away with it. Now one made profile is enough for `K ok`:
the made ones are kept and bound, the failed one's files are removed and its target stays the
image's; only when none was made is the key `K failed`. A failure appends profman's own logcat
lines (only when there is a logcat), and the boot class path's entry count, patched jars and any
jar names shared by two entries are logged before the boot profile is made. The key's scheme is
v2, so the phone's "K failed" from v1 is built once more.
"""
from pathlib import Path
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
PREP = (ROOT / 'switcher/assets/guest_prepare.sh').read_text()
START = (ROOT / 'switcher/assets/guest_start.sh').read_text()


def cut(src, a, b):
    assert src.count(a) == 1, a
    i = src.index(a)
    j = src.index(b, i)
    return src[i:j]


NOSYM = cut(PREP, 'no_symlink() {', '\n}\n') + '\n}\n'
PREP_BLOCK = cut(PREP, 'PF_DIR="$STAGE/data/oneos_prof"\n', 'if [ -f "$STAGE/services_patched.jar" ]; then\n    $BB umount')
START_BLOCK = cut(START, '    P=/data/oneos_prof\n', '    t0=$(date +%s)\n    timeout 300 "$ODR"')


def shells():
    out = []
    for p in [shutil.which('busybox')] + sorted(glob.glob('/nix/store/*busybox*/bin/busybox')):
        if p and os.access(p, os.X_OK):
            out.append(('busybox sh', [p, 'sh']))
            break
    else:
        print('busybox not found: busybox sh is not tested', file=sys.stderr)
    for name in ('dash', 'mksh', 'bash'):
        p = shutil.which(name)
        if p:
            out.append((name, [p]))
    return out


SHELLS = shells()

FAKE_BB = r'''#!/bin/sh
echo "bb $*" >> "$CALLS"
case "$1" in
mount) case "$2" in --bind) ln -f "$3" "$4" ;; esac ;;
umount|chcon) ;;
*) exec "$@" ;;
esac
'''
FAKE_PROFMAN = r'''#!/bin/sh
echo "profman $*" >> "$CALLS"
[ -f "$FAILP" ] && exit 1
[ -f "$FAILBOOT" ] && case "$*" in *--output-profile-type=boot*) exit 255 ;; esac
dump=0; for a in "$@"; do
  case "$a" in --dump-classes-and-methods) dump=1 ;; --reference-profile-file=*) ref=${a#*=} ;; esac
done
if [ $dump = 1 ]; then echo "HSP Lfoo;"; echo "HSP Lbar;"; else echo prof > "$ref"; fi
'''
FAKE_LOGCAT = r'''#!/bin/sh
echo "logcat $*" >> "$CALLS"
echo "11-18 03:33:38.123 E/profman ( 4321): Failed to add dex file to boot image profile"
'''
FAKE_ODR = r'''#!/bin/sh
echo "odrefresh $*" >> "$CALLS"
[ -f "$FAILODR" ] && exit 81
mkdir -p "$ADC/dalvik-cache/arm64"; echo art > "$ADC/dalvik-cache/arm64/boot.art"
exit 80
'''


def sh_script(path, body):
    Path(path).write_text(body)
    Path(path).chmod(0o755)


class PrepareBlock(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        d = Path(self.d)
        self.stage, self.rootfs = d / 'stage', d / 'rootfs'
        (self.stage / 'data').mkdir(parents=True)
        for sub in ('system/framework', 'system_ext/framework', 'system/product/framework', 'system/etc'):
            (self.rootfs / sub).mkdir(parents=True)
        (self.rootfs / 'system/etc/boot-image.prof').write_text('bip')
        self.add_jar('framework', 'framework.jar', 'system/framework')
        self.add_jar('services', 'services.jar', 'system/framework')
        self.prof = self.stage / 'data/oneos_prof'

    def add_jar(self, n, fname, sub, stock='stockbytes'):
        (self.stage / (n + '_patched.jar')).write_text('patched-' + n)
        (self.rootfs / sub / fname).write_text(stock + n)

    def run_block(self, shell, env=None):
        script = ('STAGE=%s; ROOTFS=%s; GUEST_SYSTEM_EXT=%s/system_ext; GUEST_PRODUCT=%s/system/product\n%s\n%s'
                  % (self.stage, self.rootfs, self.rootfs, self.rootfs, NOSYM, PREP_BLOCK))
        e = dict(os.environ, **(env or {}))
        return subprocess.run(shell + ['-c', script], capture_output=True, text=True, timeout=60, env=e)

    def key_of(self, shell):
        r = self.run_block(shell)
        self.assertEqual(0, r.returncode, r.stderr)
        return (self.prof / 'pending').read_text().strip()

    def test_copies_stock_jars_on_a_new_key(self):
        for name, sh in SHELLS:
            with self.subTest(name):
                shutil.rmtree(self.prof, ignore_errors=True)
                r = self.run_block(sh)
                self.assertEqual(0, r.returncode, r.stderr)
                self.assertEqual('stockbytesframework', (self.prof / 'stock/framework.jar').read_text())
                self.assertEqual('stockbytesservices', (self.prof / 'stock/services.jar').read_text())
                self.assertEqual('framework.jar /system/framework\n'.replace(' /system/framework', ' /system/framework/framework.jar') +
                                 'services.jar /system/framework/services.jar\n', (self.prof / 'stock.list').read_text())
                self.assertRegex((self.prof / 'pending').read_text(), r'^\d+-\d+\n$')
                self.assertEqual(0o700, os.stat(self.prof).st_mode & 0o777)
                self.assertEqual(0o644, os.stat(self.prof / 'pending').st_mode & 0o777)
                self.assertIn('2 stock jars copied', r.stdout)

    def test_current_or_failed_key_copies_nothing(self):
        for name, sh in SHELLS:
            for state in ('ok', 'failed'):
                with self.subTest(name + state):
                    shutil.rmtree(self.prof, ignore_errors=True)
                    k = self.key_of(sh)
                    (self.prof / 'key').write_text('%s %s\n' % (k, state))
                    self.assertTrue((self.prof / 'stock').is_dir())  # stale, from the run above
                    r = self.run_block(sh)
                    self.assertEqual(0, r.returncode, r.stderr)
                    self.assertFalse((self.prof / 'stock').exists())
                    self.assertFalse((self.prof / 'pending').exists())
                    self.assertFalse((self.prof / 'stock.list').exists())
                    self.assertIn('current' if state == 'ok' else 'not retried', r.stdout)

    def test_changed_jar_gets_a_new_key(self):
        sh = SHELLS[0][1]
        k = self.key_of(sh)
        (self.prof / 'key').write_text(k + ' failed\n')
        (self.stage / 'services_patched.jar').write_text('a longer patched services')
        self.assertNotEqual(k, self.key_of(sh))

    def test_still_covered_jar_is_skipped(self):
        for name, sh in SHELLS:
            with self.subTest(name):
                shutil.rmtree(self.prof, ignore_errors=True)
                stock = self.rootfs / 'system/framework/services.jar'
                stock.unlink()
                os.link(self.stage / 'services_patched.jar', stock)
                r = self.run_block(sh)
                self.assertEqual(0, r.returncode, r.stderr)
                self.assertIn('services.jar is still covered', r.stdout)
                self.assertFalse((self.prof / 'stock/services.jar').exists())
                self.assertTrue((self.prof / 'stock/framework.jar').exists())
                self.assertEqual('framework.jar /system/framework/framework.jar\n', (self.prof / 'stock.list').read_text())

    def test_jar_found_in_two_dirs_or_none_is_skipped(self):
        sh = SHELLS[0][1]
        (self.rootfs / 'system_ext/framework/framework.jar').write_text('x')
        self.add_jar('lonely', 'other.jar', 'system/framework')
        r = self.run_block(sh)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn('framework.jar has no single stock jar (2 found)', r.stdout)
        self.assertIn('lonely.jar has no single stock jar (0 found)', r.stdout)
        self.assertEqual('services.jar /system/framework/services.jar\n', (self.prof / 'stock.list').read_text())

    def test_stock_jar_in_system_ext(self):
        sh = SHELLS[0][1]
        self.add_jar('oplus-framework', 'oplus-framework.jar', 'system_ext/framework')
        r = self.run_block(sh)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn('oplus-framework.jar /system_ext/framework/oplus-framework.jar\n', (self.prof / 'stock.list').read_text())

    def test_no_jars_writes_nothing(self):
        sh = SHELLS[0][1]
        for p in self.stage.glob('*_patched.jar'):
            p.unlink()
        r = self.run_block(sh)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertFalse(self.prof.exists())

    def test_symlinked_state_dir_is_refused(self):
        for name, sh in SHELLS:
            with self.subTest(name):
                victim = Path(self.d, 'victim')
                shutil.rmtree(victim, ignore_errors=True)
                victim.mkdir()
                shutil.rmtree(self.prof, ignore_errors=True)
                if self.prof.is_symlink():
                    self.prof.unlink()
                self.prof.symlink_to(victim)
                r = self.run_block(sh)
                self.assertEqual(3, r.returncode, r.stdout + r.stderr)
                self.assertIn('is a symlink', r.stdout)
                self.assertEqual([], list(victim.iterdir()))
                self.prof.unlink()

    def test_symlinked_stock_dir_is_refused(self):
        sh = SHELLS[0][1]
        victim = Path(self.d, 'victim')
        victim.mkdir()
        self.prof.mkdir()
        (self.prof / 'stock').symlink_to(victim)
        r = self.run_block(sh)
        self.assertEqual(3, r.returncode, r.stdout + r.stderr)
        self.assertEqual([], list(victim.iterdir()))

    def test_key_scheme_is_v2(self):
        # The phone holds "<key> failed" from v1 (session db475ad5017133c4); v2 builds once more.
        self.assertIn("""|| echo none) v2"\n""", PREP_BLOCK)
        self.assertNotIn(' v1"', PREP_BLOCK)

    def test_kill_switch(self):
        for name, sh in SHELLS:
            with self.subTest(name):
                r = self.run_block(sh, {'ONEOS_GUEST_BOOT_PROFILE': '0'})
                self.assertEqual(0, r.returncode, r.stderr)
                self.assertIn('off (ONEOS_GUEST_BOOT_PROFILE=0)', r.stdout)
                self.assertFalse(self.prof.exists())


class StartBlock(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)
        d = self.d
        for sub in ('bin', 'data/oneos_prof/stock', 'sys/etc', 'sys/framework', 'sys/ext/framework', 'adc'):
            (d / sub).mkdir(parents=True)
        self.calls = d / 'calls'
        self.calls.touch()
        sh_script(d / 'bin/bb', FAKE_BB)
        sh_script(d / 'bin/profman', FAKE_PROFMAN)
        sh_script(d / 'bin/odrefresh', FAKE_ODR)
        self.boot = d / 'sys/etc/boot-image.prof'
        self.boot.write_text('image boot profile')
        self.fw, self.svc, self.other = (d / 'sys/framework/framework.jar', d / 'sys/framework/services.jar',
                                         d / 'sys/framework/ext.jar')
        for j in (self.fw, self.svc, self.other):
            j.write_text('patched ' + j.name)
        self.svc_prof = d / 'sys/framework/services.jar.prof'
        self.svc_prof.write_text('image services profile')
        p = d / 'data/oneos_prof'
        for n in ('framework', 'services'):
            (p / 'stock' / (n + '.jar')).write_text('stock ' + n)
        (p / 'stock.list').write_text('framework.jar /system/framework/framework.jar\nservices.jar /system/framework/services.jar\n')
        (p / 'pending').write_text('K1\n')
        self.p = p
        self.adc = d / 'adc'

    def run_block(self, shell, fail_profman=False, fail_odr=False, kill=None, fail_boot=False, logcat=False, bcp=None):
        d = self.d
        block = (START_BLOCK.replace('/data/oneos_prof', str(d / 'data/oneos_prof'))
                 .replace('/data/odrefresh.log', str(d / 'odrefresh.log'))
                 .replace('/system/etc/boot-image.prof', str(self.boot))
                 .replace('/apex/com.android.art/bin/profman', str(d / 'bin/profman')))
        script = ('BB=%s/bin/bb; ADC=%s; ODR=%s/bin/odrefresh\n'
                  'BOOTCLASSPATH=%s:%s; SYSTEMSERVERCLASSPATH=%s; STANDALONE_SYSTEMSERVER_JARS=\n'
                  'timeout() { shift; "$@"; }\n%s\n') % (d, self.adc, d, self.fw, self.other, self.svc, block)
        if bcp is not None:
            script = script.replace('BOOTCLASSPATH=%s:%s;' % (self.fw, self.other), 'BOOTCLASSPATH=%s;' % ':'.join(map(str, bcp)))
        env = dict(os.environ, CALLS=str(self.calls), ADC=str(self.adc), FAILP=str(d / 'failp') if fail_profman else str(d / 'nofail'),
                   FAILODR=str(d / 'failodr') if fail_odr else str(d / 'nofail'),
                   FAILBOOT=str(d / 'failboot') if fail_boot else str(d / 'nofail'))
        if fail_boot:
            (d / 'failboot').touch()
        if logcat:
            (d / 'lcbin').mkdir(exist_ok=True)
            sh_script(d / 'lcbin/logcat', FAKE_LOGCAT)
            env['PATH'] = '%s:%s' % (d / 'lcbin', env['PATH'])
        if fail_profman:
            (d / 'failp').touch()
        if fail_odr:
            (d / 'failodr').touch()
        if kill is not None:
            env['ONEOS_GUEST_BOOT_PROFILE'] = kill
        r = subprocess.run(shell + ['-c', script], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(0, r.returncode, r.stderr)
        return r

    def calls_text(self):
        return self.calls.read_text()

    def test_success(self):
        for name, sh in SHELLS:
            with self.subTest(name):
                self.setUp()
                r = self.run_block(sh)
                self.assertEqual('K1 ok\n', (self.p / 'key').read_text())
                for f in ('boot-image.prof', 'services.jar.prof'):
                    self.assertTrue((self.p / 'out' / f).is_file(), f)
                self.assertFalse((self.p / 'stock').exists())
                self.assertFalse((self.p / 'stock.list').exists())
                self.assertFalse((self.p / 'pending').exists())
                # bound: the targets now are the produced files
                self.assertEqual(os.stat(self.boot).st_ino, os.stat(self.p / 'out/boot-image.prof').st_ino)
                self.assertEqual(os.stat(self.svc_prof).st_ino, os.stat(self.p / 'out/services.jar.prof').st_ino)
                c = self.calls_text()
                self.assertIn('bb chcon u:object_r:system_file:s0 %s/out/boot-image.prof' % self.p, c)
                self.assertIn('bb mount -o remount,bind,ro %s' % self.boot, c)
                self.assertIn('--output-profile-type=boot', c)
                # the dump reads the stock jars, the create the patched ones
                self.assertIn('--apk=%s/stock/framework.jar --dex-location=%s' % (self.p, self.fw), c)
                self.assertIn('--create-profile-from=%s/out/boot-image.txt --apk=%s --dex-location=%s' % (self.p, self.fw, self.fw), c)
                self.assertIn('odrefresh --force-compile', c)
                self.assertEqual('K1\n', (self.adc / 'oneos_prof').read_text())
                log = (self.d / 'data/oneos_prof.log').read_text()
                self.assertRegex(log, r'boot-image\.prof: 2 entries with the image.s own jars, 2 with OneOS.s jars as they are \(what ART used so far\), 2 in the rebuilt profile')
                self.assertIn('services.jar.prof:', log)
                self.assertIn('boot profiles: 2 profiles ok', r.stdout)

    def test_failing_profman_binds_nothing(self):
        for name, sh in SHELLS:
            with self.subTest(name):
                self.setUp()
                r = self.run_block(sh, fail_profman=True)
                self.assertEqual('K1 failed\n', (self.p / 'key').read_text())
                self.assertFalse((self.p / 'out').exists())
                self.assertFalse((self.p / 'stock').exists())
                self.assertFalse((self.p / 'pending').exists())
                self.assertEqual('image boot profile', self.boot.read_text())
                self.assertNotIn('mount', self.calls_text())
                self.assertNotIn('odrefresh', self.calls_text())
                self.assertFalse((self.adc / 'oneos_prof').exists())

    def test_all_failed_says_failed(self):
        sh = SHELLS[0][1]
        r = self.run_block(sh, fail_profman=True)
        self.assertEqual('K1 failed\n', (self.p / 'key').read_text())
        self.assertFalse((self.p / 'out').exists())
        self.assertIn('profiles failed (0 made) in ', (self.d / 'data/oneos_prof.log').read_text())
        self.assertIn('boot profiles: 0 profiles failed in ', r.stdout)

    def test_one_failed_keeps_the_ones_made(self):
        # Session db475ad5017133c4: boot-image.prof exit 255, services.jar.prof made.
        for name, sh in SHELLS:
            with self.subTest(name):
                self.setUp()
                r = self.run_block(sh, fail_boot=True)
                self.assertEqual('K1 ok\n', (self.p / 'key').read_text())
                self.assertTrue((self.p / 'out/services.jar.prof').is_file())
                self.assertFalse((self.p / 'out/boot-image.prof').exists())
                self.assertEqual([], sorted(glob.glob(str(self.p / 'out/*.txt'))))
                for f in ('stock', 'stock.list', 'pending'):
                    self.assertFalse((self.p / f).exists(), f)
                # B2: only the made one is bound; boot-image.prof stays the image's
                self.assertEqual('image boot profile', self.boot.read_text())
                self.assertNotEqual(os.stat(self.boot).st_ino, os.stat(self.p / 'out/services.jar.prof').st_ino)
                self.assertEqual(os.stat(self.svc_prof).st_ino, os.stat(self.p / 'out/services.jar.prof').st_ino)
                c = self.calls_text()
                self.assertNotIn('bb mount --bind %s/out/boot-image.prof' % self.p, c)
                self.assertIn('bb mount --bind %s/out/services.jar.prof %s' % (self.p, self.svc_prof), c)
                self.assertIn('odrefresh --force-compile', c)
                self.assertEqual('K1\n', (self.adc / 'oneos_prof').read_text())
                log = (self.d / 'data/oneos_prof.log').read_text()
                self.assertIn('boot-image.prof: create with the patched jars failed (exit 255)', log)
                self.assertIn('profiles ok (1 made, 1 failed: boot-image) in ', log)
                self.assertIn('boot profiles: 1 profiles made, 1 failed (boot-image) in ', r.stdout)
                # the next boot binds the same one again and builds nothing
                self.calls.write_text('')
                self.run_block(sh)
                c = self.calls_text()
                self.assertNotIn('profman', c)
                self.assertNotIn('out/boot-image.prof', c)
                self.assertIn('bb mount --bind %s/out/services.jar.prof' % self.p, c)

    def test_failure_appends_profmans_logcat_lines(self):
        for name, sh in SHELLS:
            with self.subTest(name):
                self.setUp()
                self.run_block(sh, fail_boot=True, logcat=True)
                log = (self.d / 'data/oneos_prof.log').read_text()
                self.assertIn("boot-image.prof: profman's log:\n", log)
                self.assertIn('E/profman ( 4321): Failed to add dex file to boot image profile\n', log)
                self.assertEqual(1, self.calls_text().count('logcat -d -b main,system -v time -s profman'))

    def test_logcat_only_on_failure(self):
        sh = SHELLS[0][1]
        self.run_block(sh, logcat=True)
        self.assertEqual('K1 ok\n', (self.p / 'key').read_text())
        self.assertNotIn('logcat', self.calls_text())
        self.assertNotIn("profman's log", (self.d / 'data/oneos_prof.log').read_text())

    def test_logcat_capture_is_guarded(self):
        self.assertIn('if command -v logcat >/dev/null 2>&1; then', START_BLOCK)
        if shutil.which('logcat'):
            self.skipTest('this host has a logcat')
        for name, sh in SHELLS:
            with self.subTest(name):
                self.setUp()
                self.run_block(sh, fail_boot=True)
                self.assertEqual('K1 ok\n', (self.p / 'key').read_text())
                log = (self.d / 'data/oneos_prof.log').read_text()
                self.assertNotIn('logcat', log)
                self.assertNotIn("profman's log", log)

    def test_boot_class_path_names_logged(self):
        sh = SHELLS[0][1]
        self.run_block(sh)
        log = (self.d / 'data/oneos_prof.log').read_text()
        self.assertIn('boot class path: 2 entries, 1 patched: framework.jar\n', log)
        self.assertIn('boot class path jar names: all distinct (2)\n', log)
        self.assertNotIn('sharing a name', log)

    def test_boot_class_path_shared_names_logged(self):
        for name, sh in SHELLS:
            with self.subTest(name):
                self.setUp()
                twin = self.d / 'sys/ext/framework/framework.jar'
                twin.write_text('another framework')
                self.run_block(sh, bcp=[self.fw, self.other, twin])
                log = (self.d / 'data/oneos_prof.log').read_text()
                self.assertIn('boot class path: 3 entries, 2 patched: framework.jar framework.jar\n', log)
                self.assertIn('boot class path jars sharing a name: %s %s\n' % (self.fw, twin), log)
                self.assertNotIn('all distinct', log)

    def test_success_leaves_no_text_dumps(self):
        for name, sh in SHELLS:
            with self.subTest(name):
                self.setUp()
                self.run_block(sh)
                self.assertEqual('K1 ok\n', (self.p / 'key').read_text())
                self.assertEqual([], sorted(glob.glob(str(self.p / 'out/*.txt'))))
                self.assertTrue((self.p / 'out/boot-image.prof').is_file())

    def test_missing_profman_closes_the_key(self):
        for name, sh in SHELLS:
            with self.subTest(name):
                self.setUp()
                (self.d / 'bin/profman').unlink()
                self.run_block(sh)
                self.assertEqual('K1 failed\n', (self.p / 'key').read_text())
                for f in ('stock', 'stock.list', 'pending', 'out'):
                    self.assertFalse((self.p / f).exists(), f)
                self.assertEqual('image boot profile', self.boot.read_text())
                self.assertNotIn('mount', self.calls_text())
                self.assertNotIn('odrefresh', self.calls_text())
                self.assertIn("no profman at", (self.d / 'data/oneos_prof.log').read_text())

    def test_force_compile_only_when_key_differs(self):
        sh = SHELLS[0][1]
        self.run_block(sh)
        self.assertEqual(1, self.calls_text().count('odrefresh --force-compile'))
        # next boot: fresh mount namespace (rebind of the unchanged files), artifacts current
        self.calls.write_text('')
        self.run_block(sh)
        c = self.calls_text()
        self.assertIn('bb mount --bind', c)
        self.assertNotIn('odrefresh', c)
        self.assertNotIn('profman', c)
        # another key: compiled again
        (self.p / 'key').write_text('K2 ok\n')
        self.calls.write_text('')
        self.run_block(sh)
        self.assertEqual(1, self.calls_text().count('odrefresh --force-compile'))
        self.assertEqual('K2\n', (self.adc / 'oneos_prof').read_text())

    def test_failed_force_compile_undoes_the_binds(self):
        for name, sh in SHELLS:
            with self.subTest(name):
                self.setUp()
                r = self.run_block(sh, fail_odr=True)
                self.assertEqual('K1 failed\n', (self.p / 'key').read_text())
                self.assertFalse((self.p / 'out').exists())
                self.assertFalse((self.adc / 'oneos_prof').exists())
                c = self.calls_text()
                self.assertIn('bb umount -l %s' % self.boot, c)
                self.assertIn('bb umount -l %s' % self.svc_prof, c)
                self.assertIn('force-compile with the rebuilt profiles failed (exit 81)', (self.d / 'data/oneos_prof.log').read_text())
                self.assertIn('force-compile with the rebuilt profiles failed (exit 81)', (self.d / 'odrefresh.log').read_text())
                self.assertIn('force-compile failed (exit 81)', r.stdout)
                # a later boot does not retry the failed key
                self.calls.write_text('')
                self.run_block(sh)
                self.assertNotIn('mount', self.calls_text())

    def test_no_profile_bound_removes_stale_marker(self):
        sh = SHELLS[0][1]
        shutil.rmtree(self.p / 'stock')
        (self.p / 'pending').unlink()
        (self.adc / 'oneos_prof').write_text('K0\n')
        self.run_block(sh)
        self.assertFalse((self.adc / 'oneos_prof').exists())
        self.assertNotIn('odrefresh', self.calls_text())

    def test_kill_switch(self):
        sh = SHELLS[0][1]
        (self.p / 'key').write_text('K1 ok\n')
        r = self.run_block(sh, kill='0')
        self.assertNotIn('profman', self.calls_text())
        self.assertNotIn('mount', self.calls_text())
        self.assertTrue((self.p / 'pending').exists())
        self.assertNotIn('boot profiles', r.stdout)


class Syntax(unittest.TestCase):
    def test_scripts_parse(self):
        for name, sh in SHELLS:
            for f in ('guest_prepare.sh', 'guest_start.sh'):
                r = subprocess.run(sh + ['-n', str(ROOT / 'switcher/assets' / f)], capture_output=True, text=True)
                self.assertEqual(0, r.returncode, '%s %s: %s' % (name, f, r.stderr))


if __name__ == '__main__':
    unittest.main()
