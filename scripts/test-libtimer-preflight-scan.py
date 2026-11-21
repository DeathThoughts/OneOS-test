#!/usr/bin/env python3
"""oneos_switch.sh's libtimer.so preflight (0.5.28): every libtimer.so program, live or "(deleted)",
wherever it runs from, is killed; the switch is refused only when one survives. The scan is one ls and
one awk, not a readlink fork per process (S25 Ultra session c2d30e95cde9775b: 9 s of a 15 s preflight on
~1500 processes), with the readlink loop only when ls gives no link lines. The in-session watcher uses
the same scan (lt_list), every 6th round. Runs the real snippets against a fake proc tree with kill
and sleep stubbed (no process is touched), in every shell present, with the system and busybox ls/awk."""
from pathlib import Path
import os, shutil, subprocess, tempfile, unittest
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()
START = SCRIPT.index('libtimer_exe() {')
END = SCRIPT.index('echo "preflight: process scan done $(date +%T)"\n') + len('echo "preflight: process scan done $(date +%T)"\n')
SNIPPET = SCRIPT[START:END].replace('libtimer_proc=/proc', 'libtimer_proc="$P"')
assert '"$P"' in SNIPPET
FALLBACK = 'preflight: process scan fell back to readlink (ls gave no link targets)'
PRE = ('refuse() { echo "PREFLIGHT REFUSED: $*"; exit 2; }\n'
       'sleep() { :; }\n'
       'kill() { echo "KILL $3"; [ "$STOP" = 1 ] && rm -rf "$P/$3"; return 0; }\n')
LIBTIMER = '/data/app/~~xyz==/com.foo.bar-abc==/lib/arm64/libtimer.so'
OTHERS = {
    '1': '/system/bin/init',
    '12': '/system/bin/app_process64',
    '345': '/data/app/~~q==/com.other.app-z==/lib/arm64/libfoo.so',
    '349': '/data/app/~~q==/com.other.app-z==/lib/arm64/libtimer.so.bak',
    '350': '/system/bin/sh -> x',                                    # an arrow in the target
}
SHELLS = [s for s in ('mksh', 'busybox', 'bash', 'dash', 'sh') if shutil.which(s)]


def tools(d):
    out = [('system', None)]
    if shutil.which('busybox'):
        b = Path(d) / 'bb'
        b.mkdir()
        for n in ('ls', 'awk'):
            (b / n).write_text(f'#!/bin/sh\nexec busybox {n} "$@"\n')
            (b / n).chmod(0o755)
        out.append(('busybox', str(b)))
    return out


def run(shell, code, proc, path=None, stop=True):
    argv = [shell, 'sh'] if shell == 'busybox' else [shell]
    proc = Path(proc)
    env = {**os.environ, 'P': './' + proc.name, 'STOP': '1' if stop else '0'}
    if path:
        env['PATH'] = path + os.pathsep + env['PATH']
    r = subprocess.run(argv + ['-uc', PRE + code + 'echo no refusal\n'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=proc.parent)
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


HITS = {'4321': LIBTIMER, '1234': LIBTIMER + ' (deleted)', '77': '/data/local/tmp/lib/arm64/libtimer.so'}


class LibtimerPreflight(unittest.TestCase):
    def each(self, hits, **kw):
        res = []
        with tempfile.TemporaryDirectory() as d:
            found = tools(d)
            for n, shell in enumerate(SHELLS):
                for name, path in found:
                    sub = Path(d) / f'r{n}{name}'   # a fresh tree each time: the kill stub removes what it kills
                    sub.mkdir()
                    res.append(run(shell, SNIPPET, fixture(sub, hits), path, **kw))
        return res

    def test_nothing_to_stop(self):
        for rc, out in self.each({}):
            self.assertEqual((rc, FALLBACK in out, 'KILL' in out), (0, False, False))
            self.assertTrue(out.endswith('no refusal'))

    def test_kills_live_deleted_and_any_folder(self):
        for rc, out in self.each(HITS):
            self.assertEqual(rc, 0, out)
            for pid in HITS:
                self.assertIn(f'KILL {pid}', out)
            self.assertNotIn('KILL 349', out)
            self.assertIn('the kernel tool stopped', out)

    def test_refuses_when_one_survives(self):
        for rc, out in self.each({'4321': LIBTIMER}, stop=False):
            self.assertEqual(rc, 2)
            self.assertIn('the app com.foo.bar runs a kernel tool (libtimer.so, pid 4321)', out)
            self.assertIn('it did not stop', out)

    def test_spaces_in_target(self):
        for rc, out in self.each({'77': '/data/app/~~a b==/com.sp ace-x==/lib/arm64/libtimer.so'}):
            self.assertEqual(rc, 0, out)
            self.assertIn('KILL 77', out)

    def test_no_readlink_fork(self):
        with tempfile.TemporaryDirectory() as d:
            root = fixture(d, {'4321': LIBTIMER})
            b = Path(d) / 'bin'
            b.mkdir()
            (b / 'readlink').write_text(f'#!/bin/sh\necho readlink >> "{b}/forked.log"\nexit 1\n')
            (b / 'readlink').chmod(0o755)
            rc, out = run('mksh' if shutil.which('mksh') else SHELLS[0], SNIPPET, root, str(b))
            self.assertEqual(rc, 0, out)
            self.assertIn('KILL 4321', out)
            self.assertFalse((b / 'forked.log').exists())

    def test_ls_without_link_targets_falls_back(self):
        stubs = {'no-arrows': 'for a; do echo "-rwxr-xr-x 1 root root 0 2026-11-16 05:52 $a"; done\n',
                 'nothing': 'exit 0\n', 'fails': 'echo "ls: bad option" >&2; exit 1\n'}
        for name, body in stubs.items():
            with self.subTest(ls=name), tempfile.TemporaryDirectory() as d:
                b = Path(d) / 'bin'
                b.mkdir()
                (b / 'ls').write_text('#!/bin/sh\n' + body)
                (b / 'ls').chmod(0o755)
                for n, shell in enumerate(SHELLS):
                    with self.subTest(shell=shell):
                        sub = Path(d) / f'r{n}'
                        sub.mkdir()
                        rc, out = run(shell, SNIPPET, fixture(sub, HITS), str(b))
                        self.assertEqual(rc, 0, out)
                        self.assertIn(FALLBACK, out)
                        for pid in HITS:
                            self.assertIn(f'KILL {pid}', out)

    def test_live_proc(self):
        rc, out = run(SHELLS[0], SNIPPET.replace('libtimer_proc="$P"', 'libtimer_proc=/proc'), Path('/proc'))
        self.assertEqual((rc, out.endswith('no refusal')), (0, True))

    def test_watcher_uses_the_same_scan_every_sixth_round(self):
        a = SCRIPT.index('lt_list() {')
        b = SCRIPT.index('\n        while [ \\$t -lt $RETURN_MAX ]; do', a)
        fn = SCRIPT[a:b].replace('\\\\', '\x00').replace('\\$', '$').replace('\\"', '"').replace('\x00', '\\')
        self.assertIn('% 6)) = 0', SCRIPT)
        self.assertIn('lt_list | while read', SCRIPT)
        self.assertNotIn('for ex in /proc/[0-9]*/exe; do\n                tt=\\$(readlink', SCRIPT[b:b + 4000])
        fn = fn.replace('/proc/[0-9]*/exe', './proc/[0-9]*/exe').replace('root=/proc/', 'root=./proc/')
        with tempfile.TemporaryDirectory() as d:
            fixture(d, HITS)
            for shell in SHELLS:
                with self.subTest(shell=shell):
                    argv = ([shell, 'sh'] if shell == 'busybox' else [shell]) + ['-c', fn + '\nlt_list\n']
                    r = subprocess.run(argv, capture_output=True, text=True, cwd=d)
                    self.assertEqual(sorted(l.split()[0] for l in r.stdout.splitlines()), sorted(HITS), r.stderr)

    def test_timing_lines_kept(self):
        self.assertLess(SCRIPT.index('echo "preflight: oneos_switch.sh start $(date +%T)"'), START)
        # Still a preflight refusal: before the first change to the phone.
        self.assertLess(END, SCRIPT.index('setenforce 0'))


if __name__ == '__main__':
    unittest.main()
