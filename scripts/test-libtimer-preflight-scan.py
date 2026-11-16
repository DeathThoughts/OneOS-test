#!/usr/bin/env python3
"""oneos_switch.sh's libtimer.so preflight refusal finds the same process as the
old readlink-per-process loop, without a fork per process (S25 Ultra session
c2d30e95cde9775b: 9 s of a 15 s preflight on ~1500 processes). Runs the real
snippet against a fake proc tree in every shell present, with the system and
busybox ls/awk, and on the live /proc."""
from pathlib import Path
import os, shutil, subprocess, tempfile, textwrap, time, unittest
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()
START = SCRIPT.index('libtimer_exe() {')
END = SCRIPT.index('\nfi\n', SCRIPT.index('lt=$(libtimer_exe /proc)')) + 4
SNIPPET = SCRIPT[START:END]
NEW = (SNIPPET.replace('lt=$(libtimer_exe /proc)', 'lt=$(libtimer_exe "$P")')
       .replace('for ex in /proc/[0-9]*/exe', 'for ex in "$P"/[0-9]*/exe'))
assert NEW.count('"$P"') == 2
FALLBACK = 'preflight: process scan fell back to readlink (ls gave no link targets)'
REFUSE = 'refuse() { echo "PREFLIGHT REFUSED: $*"; exit 2; }\n'
# The loop as it was before the change, with only the proc root made a variable.
OLD = r'''for ex in "$P"/[0-9]*/exe; do
    t=$(readlink "$ex" 2>/dev/null) || continue
    case "$t" in
        /data/app/*/lib/*/libtimer.so)
            a=${t#/data/app/}; a=${a#*/}; a=${a%%/lib/*}; a=${a%%-*}
            refuse "the app $a runs a kernel tool (libtimer.so, pid $(echo "$ex" | cut -d/ -f3)) that crashed the kernel of S24 phones during OneOS sessions. It starts again after every restart, so restarting does not help: uninstall $a (Settings, Apps; the phone may restart once while its tool closes), then switch again" ;;
    esac
done
'''
LIBTIMER = '/data/app/~~xyz==/com.foo.bar-abc==/lib/arm64/libtimer.so'
OTHERS = {
    '1': '/system/bin/init',
    '12': '/system/bin/app_process64',
    '345': '/data/app/~~q==/com.other.app-z==/lib/arm64/libfoo.so',
    '346': '/data/app/~~q==/com.other.app-z==/libtimer.so',          # no lib/ folder
    '347': '/data/local/tmp/lib/arm64/libtimer.so',
    '348': LIBTIMER + ' (deleted)',
    '349': '/data/app/~~q==/com.other.app-z==/lib/arm64/libtimer.so.bak',
    '350': '/system/bin/sh -> x',                                    # an arrow in the target
}
SHELLS = [s for s in ('mksh', 'busybox', 'bash', 'dash', 'sh') if shutil.which(s)]


def tools(d):
    """PATH variants: the system's ls and awk, and busybox's."""
    out = [('system', None)]
    if shutil.which('busybox'):
        b = Path(d) / 'bb'
        b.mkdir()
        for n in ('ls', 'awk'):
            (b / n).write_text(f'#!/bin/sh\nexec busybox {n} "$@"\n')
            (b / n).chmod(0o755)
        out.append(('busybox', str(b)))
    return out


def run(shell, code, proc, path=None):
    argv = [shell, 'sh'] if shell == 'busybox' else [shell]
    # A fixture runs as ./proc from its parent, so the old loop's cut -d/ -f3
    # finds the pid in the third field as it does in /proc/PID/exe.
    proc = Path(proc)
    cwd, rel = (None, str(proc)) if str(proc) == '/proc' else (proc.parent, './' + proc.name)
    env = {**os.environ, 'P': rel}
    if path:
        env['PATH'] = path + os.pathsep + env['PATH']
    r = subprocess.run(argv + ['-uc', REFUSE + code + 'echo no refusal\n'], capture_output=True, text=True, env=env, cwd=cwd)
    return r.returncode, r.stdout.strip()


def fixture(d, hits):
    root = Path(d) / 'proc'
    for pid, target in {**OTHERS, **hits}.items():
        (root / pid).mkdir(parents=True)
        os.symlink(target, root / pid / 'exe')
    (root / '2').mkdir()                      # kernel thread: no exe to read
    (root / 'self').mkdir()
    os.symlink(LIBTIMER, root / 'self' / 'exe')  # not a pid
    return root


class LibtimerScan(unittest.TestCase):
    def compare(self, hits, want):
        with tempfile.TemporaryDirectory() as d:
            root = fixture(d, hits)
            old = run('mksh', OLD, root); found = tools(d)
            self.assertEqual(old, want)
            for shell in SHELLS:
                for name, path in found:
                    with self.subTest(shell=shell, tools=name):
                        self.assertEqual(run(shell, NEW, root, path), old)

    def test_refuses_naming_app_and_pid(self):
        code, out = 2, ('PREFLIGHT REFUSED: the app com.foo.bar runs a kernel tool (libtimer.so, pid 4321)'
                        ' that crashed the kernel of S24 phones during OneOS sessions. It starts again after every'
                        ' restart, so restarting does not help: uninstall com.foo.bar (Settings, Apps; the phone may'
                        ' restart once while its tool closes), then switch again')
        self.compare({'4321': LIBTIMER}, (code, out))

    def test_first_in_proc_order(self):
        # Both loops refuse at the first match in glob (name) order: 1234 before 4321.
        hits = {'4321': LIBTIMER, '1234': '/data/app/~~a==/org.second-b==/lib/arm/libtimer.so'}
        with tempfile.TemporaryDirectory() as d:
            root = fixture(d, hits)
            self.assertIn('the app org.second runs', run('mksh', NEW, root)[1])
        self.compare(hits, run_old(hits))

    def test_spaces_in_target(self):
        self.compare({'77': '/data/app/~~a b==/com.sp ace-x==/lib/arm64/libtimer.so'}, run_old({'77': '/data/app/~~a b==/com.sp ace-x==/lib/arm64/libtimer.so'}))
        self.assertEqual(run_old({'77': '/data/app/~~a b==/com.sp ace-x==/lib/arm64/libtimer.so'})[0], 2)

    def test_no_libtimer_no_refusal(self):
        self.compare({}, (0, 'no refusal'))

    def test_no_readlink_fork(self):
        # A readlink that fails the test proves the scan never runs it.
        with tempfile.TemporaryDirectory() as d:
            root = fixture(d, {'4321': LIBTIMER})
            b = Path(d) / 'bin'
            b.mkdir()
            for n in ('readlink', 'cut'):
                (b / n).write_text(f'#!/bin/sh\necho {n} >> "{b}/forked.log"\nexit 1\n')
                (b / n).chmod(0o755)
            rc, out = run('mksh', NEW, root, str(b))
            self.assertEqual(rc, 2)
            self.assertIn('pid 4321', out)
            self.assertFalse((b / 'forked.log').exists())

    def test_fallback_loop_is_the_old_loop(self):
        body = NEW[NEW.index('    for ex in "$P"'):NEW.index('\nelif [ "$lt" != none ]')+1]
        self.assertEqual(textwrap.dedent(body), OLD)

    def test_ls_without_link_targets_falls_back(self):
        # A toybox ls that followed the links, showed them without " -> ", or
        # printed nothing at all must not skip the refusal: the old loop decides.
        stubs = {'no-arrows': 'for a; do echo "-rwxr-xr-x 1 root root 0 2026-11-16 05:52 $a"; done\n',
                 'nothing': 'exit 0\n', 'fails': 'echo "ls: bad option" >&2; exit 1\n'}
        for name, body in stubs.items():
            for hits in ({'4321': LIBTIMER}, {}):
                with self.subTest(ls=name, hits=bool(hits)), tempfile.TemporaryDirectory() as d:
                    root = fixture(d, hits)
                    b = Path(d) / 'bin'
                    b.mkdir()
                    (b / 'ls').write_text('#!/bin/sh\n' + body)
                    (b / 'ls').chmod(0o755)
                    old = run('mksh', OLD, root)
                    for shell in SHELLS:
                        with self.subTest(shell=shell):
                            rc, out = run(shell, NEW, root, str(b))
                            self.assertEqual(rc, old[0])
                            self.assertEqual(out, FALLBACK + '\n' + old[1])
                            if hits:
                                self.assertEqual(rc, 2)
                                self.assertIn('the app com.foo.bar runs a kernel tool (libtimer.so, pid 4321)', out)

    def test_normal_path_does_not_fall_back(self):
        for hits in ({'4321': LIBTIMER}, {}):
            with tempfile.TemporaryDirectory() as d:
                self.assertNotIn(FALLBACK, run('mksh', NEW, fixture(d, hits))[1])
        self.assertNotIn(FALLBACK, run('mksh', NEW, '/proc')[1])

    def test_live_proc(self):
        t0 = time.time(); old = run('mksh', OLD, '/proc'); t1 = time.time(); new = run('mksh', NEW, '/proc'); t2 = time.time()
        self.assertEqual(old, (0, 'no refusal'))
        self.assertEqual(new, old)
        print(f'\nlive /proc ({len([p for p in os.listdir("/proc") if p.isdigit()])} pids): old {t1 - t0:.3f} s, new {t2 - t1:.3f} s')

    def test_timing_lines_kept(self):
        a = SCRIPT.index('echo "preflight: oneos_switch.sh start $(date +%T)"')
        self.assertLess(a, START)
        self.assertEqual(SCRIPT[END:].lstrip().split('\n')[0], 'echo "preflight: process scan done $(date +%T)"')
        # Still a preflight refusal: before the first change to the phone.
        self.assertLess(END, SCRIPT.index('setenforce 0'))


def run_old(hits):
    with tempfile.TemporaryDirectory() as d:
        return run('mksh', OLD, fixture(d, hits))


if __name__ == '__main__':
    unittest.main()
