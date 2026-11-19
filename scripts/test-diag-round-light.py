#!/usr/bin/env python3
"""The session watcher's display diagnostics rounds stay light once the guest is up (oneos_switch.sh).

ColorOS native audit 2026-11-16 (S25 Ultra sessions bceaef29fb128e94 and 4d2e95c67f065941): with
Full session logs off the rounds still froze the UI while it booted. The 20 s round ran 45 s
(05:17:20 -> 05:18:05) and pushed the 40 s round out; the 40/90 rounds took debuggerd -j and
native debuggerd of SystemUI and the launcher (every thread stopped), an ls -l of every process's
fds and 15 s of getevent on the touchscreen; and the thread listings forked a cat (sed, cut, cat)
per thread.

The round's background subshell is taken out of the watcher's quoted block and unquoted the way
the outer shell does it ($STAGE, $BB and ${ONEOS_FULL_LOGS:-1} written in, \\$ and \\" unescaped,
as test-watcher-diag-guard.py does), its touchscreen glob pointed at a fake /sys/class/input, and
run in the foreground with $BB, dumpsys, getprop, logcat, pidof, getevent and ls stubbed (each
logs its arguments). Checked:
- Full logs off and the home screen up: no debuggerd, no fd scan, no getevent, one line in
  heavy_skipped.txt; the rest of the round (user0, windows, thread listings) still runs.
- Full logs off, home not reached and the 40 s round: skipped too, with a reason of its own (a
  normal boot has no home screen at 40 s: S25 Ultra sessions db19aee410bd79ff and
  75e538ac0ed725df froze SystemUI and the touchscreen 03:15:53 to 03:16:36).
- Full logs off, home not reached and the 90 s (or final) round: all of them run, nothing skipped.
- Full logs on: all of them run in every round and nothing is skipped.
- A SurfaceFlinger that never registered still gets its native dump with Full logs off.
- The 20 s round has none of them, and no skip file.
- The start and end lines of the round are in auto_return.log.
- thread_list and the libtimer/POSIX timer scan, run on a fake /proc tree (names with spaces and
  with ") "), give what the old cat/sed loops gave, and thread_list agrees with the old loop on
  a live process.
"""
from pathlib import Path
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SWITCH = ROOT / 'switcher/assets/oneos_switch.sh'
ROUND_START = '( read -r dpid _ < /proc/self/stat && echo \\$dpid > $STAGE/diag.pid\n'
ROUND_END = ') </dev/null >/dev/null 2>&1 &'
SKIPPED = 'debuggerd stacks, touch fd scan and getevent skipped: Full logs off and the home screen was up'
SKIPPED_40 = ('debuggerd stacks, touch fd scan and getevent skipped: Full logs off; a normal boot has no home '
              'screen yet at 40 s, the 90 s round takes them if it still has none')
TOUCH_GLOB = '/sys/class/input/event*'

# The listings as they were before the change, with only the proc root made a variable.
OLD_THREADS = '''for tk in "$P"/$1/task/*; do
    echo "$(cat $tk/comm) $(sed 's/.*) //' $tk/stat | cut -d' ' -f1) $(cat $tk/wchan)"
done
'''
OLD_TIMER_READ = 'c=$(cat $tk/comm 2>/dev/null)'


def round_text(src):
    """The diagnostics round's subshell, from its diag.pid line through its closing parenthesis."""
    a = src.index(ROUND_START)
    assert src.count(ROUND_START) == 1
    lines = src[a:].split('\n')
    for n, l in enumerate(lines):
        if l.strip() == ROUND_END:
            break
    else:
        raise AssertionError('the end of the diagnostics round is not where the test expects it')
    # Run in the foreground: drop the trailing & (and the redirections, to see errors).
    return '\n'.join(lines[:n]) + '\n)\n'


def unquote(block, stage, bb, full_logs):
    block = block.replace('${ONEOS_FULL_LOGS:-1}', full_logs)
    block = re.sub(r'(?<!\\)\$STAGE\b', stage, block)
    block = re.sub(r'(?<!\\)\$BB\b', bb, block)
    left = re.findall(r'(?<!\\)\$[A-Za-z_{(]', block)
    assert not left, left
    return re.sub(r'\\([$"\\`])', r'\1', block)


def function_text(src, name):
    """The generated function `name() { ... }`, unquoted (it has no generation-time variable)."""
    a = src.index(name + '() {\n')
    indent = src.rfind('\n', 0, a) + 1
    pad = src[indent:a]
    b = src.index('\n' + pad + '}\n', a) + len(pad) + 3
    return unquote(src[a:b], '/stage', '/bb', '1')


def timer_scan_text(src):
    a = src.index('for tk in /proc/[0-9]*/task/[0-9]*; do\n')
    end = 'done > \\$D/timer_threads.txt 2>&1'
    b = src.index(end, a)
    return unquote(src[a:b] + 'done\n', '/stage', '/bb', '1')


def busybox():
    for p in [shutil.which('busybox')] + sorted(glob.glob('/nix/store/*busybox*/bin/busybox')):
        if p and os.access(p, os.X_OK):
            return p
    return None


def shells():
    out = []
    bb = busybox()
    if bb:
        out.append(('busybox sh', [bb, 'sh']))
    else:
        print('busybox not found (PATH or /nix/store): busybox sh is not tested', file=sys.stderr)
    for name in ('dash', 'mksh', 'bash', 'sh'):
        p = shutil.which(name)
        if p:
            out.append((name, [p]))
    return out


STUB = '''#!/bin/sh
echo "%s $*" >> "$CALLS"
%s
exit 0
'''


class Round(unittest.TestCase):
    """The whole round in the foreground, with stubs, in busybox sh (the phone's) or sh."""

    @classmethod
    def setUpClass(cls):
        cls.procs = [subprocess.Popen(['sleep', '120']) for _ in range(3)]

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()

    def run_round(self, full_logs, home, dname='40', sf_stuck=False):
        launcher, systemui, sf = (str(p.pid) for p in self.procs)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        stage, stubs, calls = Path(d, 'stage'), Path(d, 'bin'), Path(d, 'calls')
        stage.mkdir()
        stubs.mkdir()
        inp = Path(d, 'input/event3/device')
        inp.mkdir(parents=True)
        (inp / 'name').write_text('sec_touchscreen\n')
        (inp / 'enabled').write_text('1\n')
        Path(d, 'input/event4/device').mkdir(parents=True)
        Path(d, 'input/event4/device/name').write_text('gpio_keys\n')
        sfout = 'echo "find service: SurfaceFlinger"' if sf_stuck else ''
        stub_body = {
            'bb': 'case "$*" in *"dumpsys SurfaceFlinger"*) %s ;; esac' % sfout,
            'pidof': 'case "$1" in com.android.launcher) echo %s ;; com.android.systemui) echo %s ;; esac'
                     % (launcher, systemui),
        }
        for name in ('bb', 'dumpsys', 'getprop', 'logcat', 'pidof', 'getevent', 'ls'):
            f = stubs / name
            f.write_text(STUB % (name, stub_body.get(name, '')))
            f.chmod(0o755)
        text = unquote(round_text(SOURCE), str(stage), str(stubs / 'bb'), full_logs)
        self.assertEqual(1, text.count(TOUCH_GLOB))
        text = text.replace(TOUCH_GLOB, str(Path(d, 'input')) + '/event*')
        script = 'dname=%s; home=%s; SFP=%s; D=%s/diag_$dname\n%s' % (dname, home, sf, stage, text)
        bb = busybox()
        shell = [bb, 'sh'] if bb else ['sh']
        env = dict(os.environ, PATH='%s:%s' % (stubs, os.environ['PATH']), CALLS=str(calls))
        r = subprocess.run(shell + ['-c', script], capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(0, r.returncode, r.stderr)
        log = calls.read_text().split('\n') if calls.exists() else []
        diag = stage / ('diag_' + dname)
        return {
            'debuggerd -j': [l for l in log if l.startswith('bb ') and 'debuggerd -j' in l],
            'debuggerd native': [l for l in log if l.startswith('bb ') and re.search(r'debuggerd \d+$', l)
                                 and not l.endswith('debuggerd ' + sf)],
            'debuggerd sf': [l for l in log if l.startswith('bb ') and l.endswith('debuggerd ' + sf)],
            'fd scan': [l for l in log if l.startswith('ls ') and '/fd/' in l],
            'getevent': [l for l in log if l.startswith('getevent ')],
            'user0': [l for l in log if 'get-started-user-state' in l],
        }, diag, stage, (launcher, systemui, sf)

    def assert_heavy(self, got, diag, ran, reason=SKIPPED):
        for k in ('debuggerd -j', 'fd scan', 'getevent'):
            self.assertEqual(ran, bool(got[k]), k)
        # Both apps' native dumps (the SurfaceFlinger one is not asked for here).
        self.assertEqual(2 if ran else 0, len(got['debuggerd native']), got['debuggerd native'])
        self.assertEqual(2 if ran else 0, len(got['debuggerd -j']))
        self.assertEqual(['getevent -lt /dev/input/event3'] if ran else [], got['getevent'])
        skip = diag / 'heavy_skipped.txt'
        if ran:
            self.assertFalse(skip.exists())
        else:
            self.assertEqual(reason + '\n', skip.read_text())
            self.assertFalse((diag / 'stack_com.android.launcher.txt').exists())
            self.assertFalse((diag / 'native_com.android.systemui.txt').exists())
            self.assertFalse((diag / 'touch_events.txt').exists())

    def assert_light_rest(self, got, diag):
        # What stays in every 40/90/final round.
        self.assertEqual(1, len(got['user0']))
        for f in ('user0.txt', 'windows.txt', 'input.txt', 'timer_threads.txt', 'surfaceflinger.txt'):
            self.assertTrue((diag / f).exists(), f)
        self.assertEqual('/dev/input/event3 enabled=1\n', (diag / 'touch_holders.txt').read_text().split('\n', 1)[0] + '\n')
        for app in ('com.android.launcher', 'com.android.systemui'):
            first = (diag / ('threads_%s.txt' % app)).read_text().split('\n', 1)[0]
            self.assertRegex(first, r'^sleep [RSD] \S+$')

    def test_full_logs_off_home_up_is_light(self):
        got, diag, stage, _ = self.run_round('0', '1')
        self.assert_heavy(got, diag, ran=False)
        self.assert_light_rest(got, diag)
        self.assertEqual([], got['debuggerd sf'])
        self.assertFalse((stage / 'diag.pid').exists())

    def test_home_not_reached_at_40_with_full_logs_off_is_skipped(self):
        got, diag, _, _ = self.run_round('0', '0', dname='40')
        self.assert_heavy(got, diag, ran=False, reason=SKIPPED_40)
        self.assert_light_rest(got, diag)

    def test_home_not_reached_at_90_with_full_logs_off_runs_everything(self):
        got, diag, _, _ = self.run_round('0', '0', dname='90')
        self.assert_heavy(got, diag, ran=True)
        self.assert_light_rest(got, diag)

    def test_home_not_reached_in_the_final_round_runs_everything(self):
        got, diag, _, _ = self.run_round('0', '0', dname='final')
        self.assert_heavy(got, diag, ran=True)

    def test_home_up_at_90_with_full_logs_off_is_skipped(self):
        got, diag, _, _ = self.run_round('0', '1', dname='90')
        self.assert_heavy(got, diag, ran=False)
        self.assert_light_rest(got, diag)

    def test_full_logs_on_runs_everything(self):
        for dname in ('40', '90', 'final'):
            for home in ('0', '1'):
                with self.subTest(dname=dname, home=home):
                    got, diag, _, _ = self.run_round('1', home, dname=dname)
                    self.assert_heavy(got, diag, ran=True)
                    self.assert_light_rest(got, diag)

    def test_stuck_surfaceflinger_still_dumped(self):
        got, diag, _, pids = self.run_round('0', '1', sf_stuck=True)
        self.assert_heavy(got, diag, ran=False)
        self.assertEqual(1, len(got['debuggerd sf']), got['debuggerd sf'])
        first = (diag / 'threads_surfaceflinger.txt').read_text().split('\n', 1)[0]
        self.assertRegex(first, r'^sleep [RSD] \S+$')

    def test_round_20_has_none_of_it(self):
        got, diag, _, _ = self.run_round('0', '1', dname='20')
        for k in ('debuggerd -j', 'debuggerd native', 'fd scan', 'getevent', 'user0'):
            self.assertEqual([], got[k], k)
        self.assertFalse((diag / 'heavy_skipped.txt').exists())

    def test_start_and_end_logged(self):
        _, diag, stage, _ = self.run_round('0', '1', dname='90')
        log = (stage / 'auto_return.log').read_text().strip().split('\n')
        self.assertEqual(2, len(log), log)
        self.assertTrue(log[0].endswith(' display diagnostics 90 started'), log)
        self.assertTrue(log[1].endswith(' display diagnostics saved in %s' % diag), log)


def fake_proc(root):
    """pid 100 with four threads; 200 one libtimer.so thread; 300 a plain process."""
    tasks = {
        '100': [('100', 'main', 'S', 'do_epoll_wait'), ('101', 'POSIX timer 3', 'S', 'futex_wait_queue'),
                ('102', 'a) b', 'D', 'binder_wait_for_work'), ('103', ' lead and trail ', 'R', '0')],
        '200': [('200', 'libtimer.so', 'S', 'hrtimer_nanosleep')],
        '300': [('300', 'binder:300_2', 'S', '')],
    }
    for pid, ts in tasks.items():
        Path(root, pid).mkdir()
        Path(root, pid, 'cmdline').write_bytes(b'/system/bin/thing\0--arg\0')
        for tid, comm, state, wchan in ts:
            t = Path(root, pid, 'task', tid)
            t.mkdir(parents=True)
            (t / 'comm').write_text(comm + '\n')
            (t / 'stat').write_text('%s (%s) %s 1 %s 0 0 0 -1 4194560 12 0\n' % (tid, comm, state, pid))
            (t / 'wchan').write_text(wchan)  # no newline, as in the kernel's
    return tasks


class Fragments(unittest.TestCase):
    def test_thread_list_matches_the_old_loop(self):
        new = function_text(SOURCE, 'thread_list').replace('/proc/$1/task/*', '"$P"/$1/task/*')
        self.assertIn('"$P"/$1/task/*', new)
        self.assertNotIn('$(', new)
        with tempfile.TemporaryDirectory() as d:
            fake_proc(d)
            for sname, shell in shells():
                for pid in ('100', '200', '300'):
                    with self.subTest(shell=sname, pid=pid):
                        a = subprocess.run(shell + ['-c', 'P=%s; old() {\n%s}\nold %s' % (d, OLD_THREADS, pid)],
                                           capture_output=True, text=True, timeout=30)
                        b = subprocess.run(shell + ['-c', 'P=%s\n%s\nthread_list %s' % (d, new, pid)],
                                           capture_output=True, text=True, timeout=30)
                        self.assertEqual(('', ''), (a.stderr, b.stderr))
                        self.assertEqual(a.stdout, b.stdout)
            out = subprocess.run(shells()[0][1] + ['-c', 'P=%s\n%s\nthread_list 100' % (d, new)],
                                 capture_output=True, text=True, timeout=30).stdout
            self.assertEqual('main S do_epoll_wait\nPOSIX timer 3 S futex_wait_queue\n'
                             'a) b D binder_wait_for_work\n lead and trail  R 0\n', out)

    def test_thread_list_skips_a_thread_that_is_gone(self):
        new = function_text(SOURCE, 'thread_list').replace('/proc/$1/task/*', '"$P"/$1/task/*')
        with tempfile.TemporaryDirectory() as d:
            fake_proc(d)
            os.remove(Path(d, '100/task/102/comm'))
            for sname, shell in shells():
                with self.subTest(shell=sname):
                    b = subprocess.run(shell + ['-c', 'P=%s\n%s\nthread_list 100' % (d, new)],
                                       capture_output=True, text=True, timeout=30)
                    self.assertEqual('', b.stderr)
                    self.assertNotIn('binder_wait_for_work', b.stdout)
                    self.assertEqual(3, len(b.stdout.splitlines()))

    def test_thread_list_on_a_live_process(self):
        new = function_text(SOURCE, 'thread_list').replace('/proc/$1/task/*', '"$P"/$1/task/*')
        p = subprocess.Popen(['sleep', '60'])
        try:
            for sname, shell in shells():
                with self.subTest(shell=sname):
                    a = subprocess.run(shell + ['-c', 'P=/proc; old() {\n%s}\nold %d' % (OLD_THREADS, p.pid)],
                                       capture_output=True, text=True, timeout=30)
                    b = subprocess.run(shell + ['-c', 'P=/proc\n%s\nthread_list %d' % (new, p.pid)],
                                       capture_output=True, text=True, timeout=30)
                    self.assertEqual(a.stdout, b.stdout)
                    self.assertRegex(b.stdout, r'^sleep S \S+\n$')
        finally:
            p.kill()
            p.wait()

    def test_timer_scan_matches_the_old_loop(self):
        new = timer_scan_text(SOURCE)
        self.assertEqual(1, new.count('IFS= read -r c 2>/dev/null < $tk/comm || continue'))
        old = new.replace('IFS= read -r c 2>/dev/null < $tk/comm || continue', OLD_TIMER_READ)
        new, old = (t.replace('/proc/', '$P/') for t in (new, old))
        with tempfile.TemporaryDirectory() as d:
            fake_proc(d)
            for sname, shell in shells():
                with self.subTest(shell=sname):
                    a = subprocess.run(shell + ['-c', 'P=%s\n%s' % (d, old)], capture_output=True, text=True, timeout=30)
                    b = subprocess.run(shell + ['-c', 'P=%s\n%s' % (d, new)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(a.stdout, b.stdout)
                    lines = b.stdout.splitlines()
                    self.assertEqual(2, len(lines), lines)
                    self.assertTrue(lines[0].startswith('POSIX timer 3 tid=101 pid=100 uid='), lines)
                    self.assertTrue(lines[1].startswith('libtimer.so tid=200 pid=200 uid='), lines)
                    self.assertIn('cmd=/system/bin/thing --arg', lines[1])

    def test_no_fork_per_thread_left(self):
        self.assertNotIn("$(sed 's/.*) //' \\$tk/stat", SOURCE)
        self.assertNotIn('c=\\$(cat \\$tk/comm', SOURCE)


if __name__ == '__main__':
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        SWITCH = Path(sys.argv.pop(1))
    SOURCE = SWITCH.read_text()
    unittest.main()
