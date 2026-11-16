#!/usr/bin/env python3
"""The session sampler (oneos_switch.sh) adds forks/s and ctxt/s to cpu_top.log.

S25 Ultra session bceaef29fb128e94 and S23 session 4d2e95c67f065941: 150-300%
sys CPU while the phone was nearly idle (05:18:59: user 71%, sys 239%, idle
482%), with the six busiest processes adding up to little. Each cpu_top.log
line now ends with the new processes and context switches per second since
the line before, from /proc/stat over /proc/uptime. These tests render the
sampler's double-quoted `sh -c` string the way the switch's shell does, point
/proc/stat and /proc/uptime at fake files, stub top, and run the real loop for
a few rounds (each `sleep` moves to the next round), under every shell here.
"""
import os
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

TOP = '''Tasks: 812 total,   1 running, 811 sleeping,   0 stopped,   0 zombie
800%cpu  71%user   0%nice 239%sys 482%idle   0%iow   8%irq   0%sirq   0%host
%CPU   PID NAME
 12.0  1234 system_server
  4.0  2345 busybox
'''


def sampler_block():
    a = SWITCH.index(START) + len('$BB sh -c "')
    b = SWITCH.index(END, a)
    return SWITCH[a:b]


class SessionSamplerForksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oneos-sampler-')
        self.r = Path(self.temp.name)
        self.stage = self.r / 'stage'
        (self.stage / 'data').mkdir(parents=True)
        (self.stage / 'guest_mntns').write_text('1\n')
        (self.r / 'proc').mkdir()
        (self.r / 'top.txt').write_text(TOP)

    def tearDown(self):
        self.temp.cleanup()

    def render(self):
        render = subprocess.run(['sh', '-c', 'printf "%s" "' + sampler_block() + '"'],
                                env={**os.environ, 'BB': 'busybox', 'STAGE': str(self.stage)},
                                capture_output=True, text=True)
        self.assertEqual(0, render.returncode, render.stderr)
        return render.stdout

    def run_loop(self, rounds, shell='sh'):
        """rounds: (uptime s, processes, ctxt) per round; None for a missing /proc/stat."""
        for n, rd in enumerate(rounds, 1):
            if rd is None:
                script = f'rm -f {self.r}/proc/stat\n'
            else:
                up, forks, ctxt = rd
                script = (f"echo '{up} 1000.00' > {self.r}/proc/uptime\n"
                          f"printf 'cpu  1 2 3 4\\nctxt {ctxt}\\nbtime 1\\nprocesses {forks}\\nprocs_running 1\\n'"
                          f" > {self.r}/proc/stat\n")
            (self.r / f'round{n}').write_text(script)
        fake = f' {self.r}/proc/uptime {self.r}/proc/stat 2>/dev/null'
        loop = self.render()
        self.assertEqual(1, loop.count(' /proc/uptime /proc/stat 2>/dev/null'), 'the sampler reads /proc/stat elsewhere')
        loop = loop.replace(' /proc/uptime /proc/stat 2>/dev/null', fake)
        stubs = f'''
pidof() {{ :; }}
timeout() {{ cat {self.r}/top.txt; }}
n=1
sleep() {{
    n=$((n + 1))
    if [ -f {self.r}/round$n ]; then . {self.r}/round$n; else : > {self.stage}/data/oneos_rec_stop; fi
}}
. {self.r}/round1
'''
        argv = shell.split() + ['-c', stubs + loop]
        done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        text = (self.stage / 'cpu_top.log').read_text()
        return text, [l for l in text.splitlines() if not l.startswith('#')]

    def test_the_rendered_sampler_is_valid_shell(self):
        loop = self.render()
        for shell in SHELLS:
            check = subprocess.run(shell.split() + ['-n', '-c', loop], capture_output=True, text=True)
            self.assertEqual(0, check.returncode, (shell, check.stderr))

    def test_rates_per_second_since_the_line_before(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                # 30 s apart: 3000 forks and 1500000 switches = 100/s and 50000/s;
                # ctxt past 2^31 and 2^32, beyond 32-bit shell arithmetic.
                _, log = self.run_loop([(1000.00, 500000, 4294000000), (1030.00, 503000, 4295500000),
                                        (1045.50, 503031, 4295500031)], shell)
                self.assertEqual(3, len(log), log)
                self.assertTrue(log[0].endswith(' | forks/s - ctxt/s -'), log[0])
                self.assertTrue(log[1].endswith(' | forks/s 100 ctxt/s 50000'), log[1])
                self.assertTrue(log[2].endswith(' | forks/s 2 ctxt/s 2'), log[2])
                # The busiest processes stay where they were, before the rates.
                self.assertIn('239%sys', log[1])
                self.assertIn('| 12.0 system_server 4.0 busybox | forks/s', log[1])
                (self.stage / 'data/oneos_rec_stop').unlink()

    def test_no_proc_stat_gives_a_dash_and_starts_again(self):
        _, log = self.run_loop([(1000.0, 10, 20), None, (1060.0, 70, 80), (1090.0, 100, 110)])
        self.assertEqual(4, len(log), log)
        self.assertTrue(log[1].endswith(' | forks/s - ctxt/s -'), log[1])
        self.assertTrue(log[2].endswith(' | forks/s - ctxt/s -'), log[2])
        self.assertTrue(log[3].endswith(' | forks/s 1 ctxt/s 1'), log[3])

    def test_the_header_names_the_rates(self):
        text, _ = self.run_loop([(1000.0, 10, 20)])
        self.assertIn('then forks/s and ctxt/s since the line before (/proc/stat processes, ctxt)', text.splitlines()[0])


if __name__ == '__main__':
    unittest.main()
