#!/usr/bin/env python3
"""The session sampler (oneos_switch.sh) reads the window blur state every 5 min, not every 30 s.

ColorOS native audit 2026-11-16, S25 Ultra, F12: every 30 s the sampler ran
`dumpsys window windows` in the guest only to grep mBlurEnabled (it takes
WindowManager's lock, a big Java dump for one flag that rarely changes) and
`settings get global disable_window_blurs`. Both are now read on the first pass
with a guest system_server and every 10th after it, and on a new
system_server; the lines in between keep the last values, so
each diag_series.txt line still has the blur fields. These tests render the sampler's
double-quoted `sh -c` string the way the switch's shell does, stub pidof,
timeout (the guest calls and top) and sleep (each one moves to the next pass),
and run the real loop for a number of passes, under every shell here.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()

START = '$BB sh -c "\n    echo \\$\\$ > /sys/fs/cgroup/cgroup.procs 2>/dev/null\n    F=$STAGE/diag_series.txt\n'
END = '\n" </dev/null >/dev/null 2>&1 &\necho "Session sampler: '

SHELLS = [s for s in ('sh', 'dash', 'busybox sh', 'mksh') if shutil.which(s.split()[0])]

TOP = '''800%cpu  71%user   0%nice 239%sys 482%idle   0%iow   8%irq   0%sirq   0%host
%CPU   PID NAME
 12.0  1234 system_server
'''
THERMAL = '''Thermal Status: 1
Current temperatures from HAL:
\tTemperature{mValue=38.5, mType=3, mName=SKIN, mStatus=0}
'''
LINE = re.compile(r'^\d\d:\d\d:\d\d thermal=(\S+) skin=(\S+)( policy\S+)* blur=(\S+) disable_window_blurs=(\S+) battery=\S+%,\S+ gpu_hz=(\S+) gpu_cap_mhz=(\S+)$')


def sampler_block():
    a = SWITCH.index(START) + len('$BB sh -c "')
    b = SWITCH.index(END, a)
    return SWITCH[a:b]


class SessionSamplerBlurTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oneos-sampler-blur-')
        self.r = Path(self.temp.name)
        self.stage = self.r / 'stage'
        (self.stage / 'data').mkdir(parents=True)
        (self.stage / 'guest_mntns').write_text('1\n')
        (self.r / 'top.txt').write_text(TOP)
        (self.r / 'thermal.txt').write_text(THERMAL)

    def tearDown(self):
        self.temp.cleanup()

    def render(self):
        render = subprocess.run(['sh', '-c', 'printf "%s" "' + sampler_block() + '"'],
                                env={**os.environ, 'BB': 'busybox', 'STAGE': str(self.stage)},
                                capture_output=True, text=True)
        self.assertEqual(0, render.returncode, render.stderr)
        return (render.stdout
                .replace('/sys/class/kgsl/kgsl-3d0/devfreq/cur_freq', str(self.r / 'gpu_hz'))
                .replace('/sys/kernel/gpu/gpu_max_clock', str(self.r / 'gpu_cap_mhz')))

    def run_loop(self, passes, changes, shell='sh'):
        """changes: {pass: {'pid': ..., 'blur': ..., 'dw': ...}} applied before that pass; '' for no system_server."""
        for f in ('window_calls', 'settings_calls', 'thermal_calls'):
            (self.r / f).unlink(missing_ok=True)
        (self.stage / 'data/oneos_rec_stop').unlink(missing_ok=True)
        for n in range(1, passes + 1):
            script = ''.join(f"printf '%s' '{v}' > {self.r}/{k}\n" for k, v in changes.get(n, {}).items())
            (self.r / f'round{n}').write_text(script)
        r = self.r
        stubs = f'''
pidof() {{ cat {r}/pid 2>/dev/null; }}
timeout() {{
    case "$*" in
        *'/system/bin/dumpsys window windows') echo $round >> {r}/window_calls; echo "    mBlurEnabled=$(cat {r}/blur)";;
        *'/system/bin/settings get global disable_window_blurs') echo $round >> {r}/settings_calls; cat {r}/dw;;
        *'/system/bin/dumpsys thermalservice') echo $round >> {r}/thermal_calls; cat {r}/thermal.txt;;
        *) cat {r}/top.txt;;
    esac
}}
round=1
sleep() {{
    round=$((round + 1))
    if [ -f {r}/round$round ]; then . {r}/round$round; else : > {self.stage}/data/oneos_rec_stop; fi
}}
. {r}/round1
'''
        done = subprocess.run(shell.split() + ['-c', stubs + self.render()], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        text = (self.stage / 'diag_series.txt').read_text()

        def calls(f):
            p = self.r / f
            return [int(x) for x in p.read_text().split()] if p.exists() else []
        lines = [l for l in text.splitlines() if not l.startswith('#')]
        self.assertEqual(passes, len(lines), text)
        got = []
        for l in lines:
            m = LINE.match(l)
            self.assertIsNotNone(m, l)
            got.append((m.group(4), m.group(5)))
        return text, got, calls('window_calls'), calls('settings_calls'), calls('thermal_calls')

    def test_gpu_values_are_read_when_available(self):
        (self.r / 'gpu_hz').write_text('719000000\n')
        (self.r / 'gpu_cap_mhz').write_text('680\n')
        log, *_ = self.run_loop(1, {1: {'pid': ''}})
        self.assertIn('gpu_hz=719000000 gpu_cap_mhz=680\n', log)

    def test_unavailable_gpu_values_are_marked_missing(self):
        log, *_ = self.run_loop(1, {1: {'pid': ''}})
        self.assertIn('gpu_hz=- gpu_cap_mhz=-\n', log)

    def test_the_rendered_sampler_is_valid_shell(self):
        loop = self.render()
        for shell in SHELLS:
            check = subprocess.run(shell.split() + ['-n', '-c', loop], capture_output=True, text=True)
            self.assertEqual(0, check.returncode, (shell, check.stderr))

    def test_window_dump_on_passes_1_and_11_and_the_last_value_between(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                # The blur goes off before pass 5 and the setting with it: the lines keep
                # what pass 1 read until pass 11 reads again.
                _, got, window, settings, thermal = self.run_loop(
                    12, {1: {'pid': '4242', 'blur': 'true', 'dw': '0'}, 5: {'blur': 'false', 'dw': '1'}}, shell)
                self.assertEqual([1, 11], window)
                self.assertEqual([1, 11], settings)
                # The thermal service is still read on every pass.
                self.assertEqual(list(range(1, 13)), thermal)
                self.assertEqual([('true', '0')] * 10 + [('false', '1')] * 2, got)

    def test_thermal_and_skin_every_line(self):
        text, _, _, _, _ = self.run_loop(3, {1: {'pid': '4242', 'blur': 'true', 'dw': '0'}})
        for l in [l for l in text.splitlines() if not l.startswith('#')]:
            self.assertIn(' thermal=1 skin=38.5', l)

    def test_first_read_waits_for_the_guest_system_server(self):
        # Until the guest's system_server runs, only the kernel's values: blur "-".
        _, got, window, settings, _ = self.run_loop(14, {1: {'pid': '', 'blur': 'true', 'dw': '0'}, 3: {'pid': '4242'}})
        self.assertEqual([3, 13], window)
        self.assertEqual([3, 13], settings)
        self.assertEqual([('-', '-')] * 2 + [('true', '0')] * 12, got)

    def test_a_new_system_server_is_read_at_once(self):
        # A soft reboot of the guest: a new pid on pass 4, and none on pass 8 (no stale value then).
        _, got, window, _, _ = self.run_loop(
            10, {1: {'pid': '4242', 'blur': 'true', 'dw': '0'}, 4: {'pid': '5353', 'blur': 'false'},
                 8: {'pid': ''}, 9: {'pid': '6464', 'blur': 'true'}})
        self.assertEqual([1, 4, 9], window)
        self.assertEqual([('true', '0')] * 3 + [('false', '0')] * 4 + [('-', '-')] + [('true', '0')] * 2, got)

    def test_the_header_says_blur_is_read_every_5_min(self):
        text, _, _, _, _ = self.run_loop(1, {1: {'pid': '4242', 'blur': 'true', 'dw': '0'}})
        header = text.splitlines()[0]
        self.assertIn('OneOS session sampler, one line every 30 s:', header)
        self.assertIn('mBlurEnabled, disable_window_blurs (both read every 5 min), battery %,status', header)


if __name__ == '__main__':
    unittest.main()
