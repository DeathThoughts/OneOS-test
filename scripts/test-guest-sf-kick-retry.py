#!/usr/bin/env python3
"""The SurfaceFlinger kick retry in guest_start.sh against a fake kick.

The block is cut from the real script by its text, as test-dt2w-keeper.py does,
then run with sh. Its /system/bin/sh and /data/oneos_sf_kick.sh are localised to
a temporary directory. The fake kick counts its runs, fails N times (with "no
physical displays listed yet", a partial "no modes" output or a not-found error),
then lists Display 42; in the gated scenario it holds the background retry until
the test has seen the foreground return. sleep is a no-op here, so the 30 tries
take no time. The slow-kick test swaps date/sleep/timeout for stubs on a virtual
clock, so it proves the 10 s foreground timeout and the 60 s deadline without
waiting for either.
"""
from pathlib import Path
import os
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
START_SH = (ROOT / 'switcher/assets/guest_start.sh').read_text()

START = ('if [ -e /system/framework/oplus-services.jar ]; then\n'
         '    :\nelif [ "${ONEOS_GUEST_SF_PIN:-1}" = 0 ]; then')
END = '\n# OneOS: screen recording from the boot animation on'


def between(text, start, end):
    return start + text.split(start, 1)[1].split(end, 1)[0]


BLOCK = between(START_SH, START, END)

PRELUDE = r'''
sleep() { :; }
timeout() { shift; "$@"; }
dumpsys() {
    command cat <<'DUMP'
Display 42
debugDisplayModeSetByBackdoor=true
  displayManagerPolicy={defaultMode=1, allowedModes=1:120:0:0}
DUMP
}
'''

# A virtual epoch for the slow-kick test: every timeout and sleep advances the
# clock by its argument, so the real deadline arithmetic runs in milliseconds.
SLOW_PRELUDE = r'''
clock_advance() {
    t=$(command cat "$R/clock")
    command echo "$((t + $1))" > "$R/clock"
}
date() {
    case "$1" in
        +%s) command cat "$R/clock" ;;
        *) command date "$@" ;;
    esac
}
sleep() {
    command echo "${1%%.*}" >> "$R/sleeps"
    clock_advance "${1%%.*}"
}
timeout() {
    secs=$1
    shift
    command echo "$secs $*" >> "$R/timeouts"
    case "$*" in
        *oneos_sf_kick.sh*)
            # Run the fake kick so its counter still records the attempt, but
            # discard its output and report the timeout the guest would see.
            "$@" >/dev/null 2>&1
            clock_advance "$secs"
            return 124 ;;
        *) "$@" ;;
    esac
}
'''

FAKE_KICK = '''#!/bin/sh
# The fake /data/oneos_sf_kick.sh: count runs, fail FAILS times, then list a display.
n=$(command cat "$R/count" 2>/dev/null)
case "$n" in ''|*[!0-9]*) n=0 ;; esac
n=$((n + 1))
command echo "$n" > "$R/count"
if [ "$n" -le "$FAILS" ]; then
    if [ "${GATE:-0}" = 1 ] && [ "$n" -gt 1 ] && [ ! -e "$R/release" ]; then
        while [ ! -e "$R/release" ]; do command sleep 0.02; done
    fi
    case "${FAIL_KIND:-nodisplay}" in
        partial)
            command echo "display 0: no modes in the dump, left alone"
            command echo "no physical displays listed yet" ;;
        notfound)
            command echo "sh: /data/oneos_sf_kick.sh: not found" >&2 ;;
        *)
            command echo "no physical displays listed yet" ;;
    esac
else
    command echo "display 42: through mode 2 (60 Hz) to mode 1, pinned at 120 Hz: Result: Parcel(00000000)"
fi
'''


class KickRetry(unittest.TestCase):
    @staticmethod
    def counted(root):
        p = root / 'count'
        return int(p.read_text()) if p.exists() else 0

    @staticmethod
    def processes_with(token):
        # The background subshell keeps the sh -c command line (and this comment),
        # so its /proc entry is what proves that the retry chain ended.
        needle = token.encode()
        pids = []
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if needle in (entry / 'cmdline').read_bytes():
                    pids.append(int(entry.name))
            except OSError:
                pass
        return pids

    def stop_retry(self, root, token):
        # A failed assertion must not leave the background loop behind.
        (root / 'release').touch()
        for pid in self.processes_with(token):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def run_block(self, fails, gate=False, fail_kind='nodisplay', slow=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        kick = root / 'oneos_sf_kick.sh'
        kick.write_text(FAKE_KICK)
        os.chmod(kick, 0o755)
        if slow:
            (root / 'clock').write_text('1000000\n')
        token = f'oneos-sf-retry-{os.getpid()}-{time.monotonic_ns()}'
        code = (PRELUDE + (SLOW_PRELUDE if slow else '') + BLOCK.replace('/system/bin/sh', 'sh')
                .replace('/data/oneos_sf_kick.sh', str(kick)) +
                '\ncommand echo FOREGROUND_DONE\n# ' + token + '\n')
        env = dict(os.environ, R=str(root), FAILS=str(fails), GATE='1' if gate else '0',
                   FAIL_KIND=fail_kind, ONEOS_GUEST_SF_PIN='1')
        out_path = root / 'out'
        with out_path.open('w') as out:
            proc = subprocess.Popen(['/bin/sh', '-c', code], env=env, stdout=out,
                                    stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 5
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if proc.poll() is None:
            proc.kill()
            proc.wait()
            self.stop_retry(root, token)
            self.fail('the foreground block did not return: the boot would be blocked')
        self.assertEqual(0, proc.returncode)
        if gate:
            # The retry chain is waiting in the fake kick; the foreground still returned.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and self.counted(root) < 2:
                time.sleep(0.01)
            if self.counted(root) < 2 or not self.processes_with(token):
                self.stop_retry(root, token)
                self.fail('the background retry did not start while the foreground returned')
            (root / 'release').touch()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            text = out_path.read_text()
            if 'FOREGROUND_DONE' in text and not self.processes_with(token):
                break
            time.sleep(0.01)
        else:
            text = out_path.read_text()
            self.stop_retry(root, token)
            self.fail(f'the background retry did not finish: {text}')
        timeouts = (root / 'timeouts').read_text().splitlines() if (root / 'timeouts').exists() else []
        sleeps = (root / 'sleeps').read_text().splitlines() if (root / 'sleeps').exists() else []
        return {'out': out_path.read_text().splitlines(), 'count': self.counted(root),
                'timeouts': timeouts, 'sleeps': sleeps}

    def test_first_kick_logs_the_output_and_the_policy(self):
        got = self.run_block(fails=0)
        self.assertEqual(1, got['count'])
        self.assertIn('[Pivoted Root] SF render rate: display 42: through mode 2 (60 Hz) to mode 1, '
                      'pinned at 120 Hz: Result: Parcel(00000000)', got['out'])
        self.assertIn('[Pivoted Root] SF debugDisplayModeSetByBackdoor=true', got['out'])
        self.assertIn('[Pivoted Root] SF 42 displayManagerPolicy={defaultMode=1, allowedModes=1:120:0:0}',
                      got['out'])
        self.assertFalse(any('display listed on retry' in line for line in got['out']))

    def test_retry_lists_the_display_and_logs_the_policy(self):
        got = self.run_block(fails=3, gate=True)
        self.assertEqual(4, got['count'])
        lines = got['out']
        kick = ('[Pivoted Root] SF render rate: display 42: through mode 2 (60 Hz) to mode 1, '
                'pinned at 120 Hz: Result: Parcel(00000000)')
        retry = '[Pivoted Root] SF render rate: display listed on retry 3/30'
        policy = '[Pivoted Root] SF 42 displayManagerPolicy={defaultMode=1, allowedModes=1:120:0:0}'
        self.assertIn('[Pivoted Root] SF render rate: no physical displays listed yet', lines)
        self.assertIn(kick, lines)
        self.assertIn(retry, lines)
        self.assertIn('[Pivoted Root] SF debugDisplayModeSetByBackdoor=true', lines)
        self.assertIn(policy, lines)
        self.assertLess(lines.index(kick), lines.index(retry))
        self.assertLess(lines.index(retry), lines.index(policy))
        self.assertEqual(1, sum(line.endswith('no physical displays listed yet') for line in lines))

    def test_partial_or_error_output_is_not_success(self):
        cases = (
            ('partial', '[Pivoted Root] SF render rate: display 0: no modes in the dump, left alone'),
            ('notfound', '[Pivoted Root] SF render rate: sh: /data/oneos_sf_kick.sh: not found'),
        )
        for kind, failed_line in cases:
            with self.subTest(kind=kind):
                got = self.run_block(fails=1, fail_kind=kind)
                lines = got['out']
                retry = '[Pivoted Root] SF render rate: display listed on retry 1/30'
                self.assertEqual(2, got['count'])
                self.assertIn(failed_line, lines)
                self.assertIn(retry, lines)
                # The failed kick must not have logged a policy as if it had worked.
                self.assertFalse(any('displayManagerPolicy' in line or
                                     'debugDisplayModeSetByBackdoor' in line
                                     for line in lines[:lines.index(retry)]))

    def test_the_30th_retry_is_left_to_the_panel_keeper(self):
        got = self.run_block(fails=999)
        self.assertEqual(31, got['count'])
        lines = got['out']
        final = ('[Pivoted Root] SF render rate: no physical displays after 60 s '
                 '(30/30 retries); left to the panel keeper')
        policy = '[Pivoted Root] SF 42 displayManagerPolicy={defaultMode=1, allowedModes=1:120:0:0}'
        self.assertIn(final, lines)
        self.assertIn('[Pivoted Root] SF debugDisplayModeSetByBackdoor=true', lines)
        self.assertIn(policy, lines)
        self.assertLess(lines.index(policy), lines.index(final))
        self.assertEqual(1, sum(line.endswith('no physical displays listed yet') for line in lines))
        self.assertFalse(any('display listed on retry' in line for line in lines))

    def test_a_hung_kick_is_bounded_and_the_retry_stops_at_the_deadline(self):
        start = time.monotonic()
        got = self.run_block(fails=999, slow=True)
        self.assertLess(time.monotonic() - start, 5)
        lines = got['out']
        final = ('[Pivoted Root] SF render rate: no physical displays after 60 s '
                 '(5/30 retries); left to the panel keeper')
        policy = '[Pivoted Root] SF 42 displayManagerPolicy={defaultMode=1, allowedModes=1:120:0:0}'
        # The foreground kick returned (run_block fails otherwise); the background
        # chain then made 5 ten-second kicks with 2 s sleeps between them: 60 s.
        self.assertEqual(6, got['count'])
        self.assertIn(final, lines)
        self.assertIn(policy, lines)
        self.assertLess(lines.index(policy), lines.index(final))
        kicks = [int(line.split()[0]) for line in got['timeouts'] if 'oneos_sf_kick.sh' in line]
        self.assertEqual([10] * 6, kicks)
        sleeps = [int(sec) for sec in got['sleeps']]
        self.assertEqual(5, len(sleeps))
        self.assertTrue(all(0 < sec <= 2 for sec in sleeps))
        # Foreground + five 10 s retries + five 2 s sleeps end exactly at 60 s.
        self.assertEqual(60, sum(kicks[1:]) + sum(sleeps))

    def test_the_coloros_skip_and_the_pin_switch_are_unchanged(self):
        self.assertIn('if [ -e /system/framework/oplus-services.jar ]; then', BLOCK)
        self.assertIn('elif [ "${ONEOS_GUEST_SF_PIN:-1}" = 0 ]; then', BLOCK)
        self.assertIn('(ONEOS_GUEST_SF_PIN=0)', BLOCK)
        self.assertLess(BLOCK.index('oplus-services.jar'), BLOCK.index('oneos_sf_kick.sh'))

    def test_the_retry_is_2s_30_tries_60s_and_one_chain(self):
        self.assertIn('sleep 2', BLOCK)
        self.assertIn('while [ "$i" -lt 30 ]', BLOCK)
        self.assertIn('deadline=$(( $(date +%s) + 60 ))', BLOCK)
        self.assertIn('timeout "$left"', BLOCK)
        self.assertIn('timeout 10 /system/bin/sh /data/oneos_sf_kick.sh', BLOCK)
        self.assertIn('timeout 10 dumpsys SurfaceFlinger', BLOCK)
        self.assertIn('after 60 s', BLOCK)
        self.assertIn('after 60 s ($i/30 retries)', BLOCK)
        self.assertEqual(2, BLOCK.count('*"pinned at"*'))
        self.assertNotIn('*"no physical displays listed yet"*) continue', BLOCK)
        self.assertEqual(2, BLOCK.count('/data/oneos_sf_kick.sh'))
        # One background chain, by its comment (a bare ") &" count is fragile).
        self.assertEqual(1, BLOCK.count('One chain only'))


if __name__ == '__main__':
    unittest.main()
