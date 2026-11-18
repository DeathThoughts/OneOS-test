#!/usr/bin/env python3
"""The heat cap (oneos_switch.sh) lifts and caps the GPU's ceiling with the CPU's.

The heat cap's loop is a double-quoted `sh -c` string inside oneos_switch.sh.
These tests render that string the way the switch's shell does, keep its
preamble (helpers up to the loop), point /sys at a fake tree and run the
helpers, so a quoting slip in the string fails here instead of on a phone.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()
RETURN = (ROOT / 'switcher/assets/return_to_oneui.sh').read_text()
FOLD8 = ROOT / 'switcher/assets/fold8_coloros.sh'

START = '    $BB sh -c "\n    : oneos_heat_cap\n'
END = '\n" </dev/null >/dev/null 2>&1 &\n    [ $HEAT_CAP = 1 ] && '


def heat_cap_block():
    a = SWITCH.index(START) + len('    $BB sh -c "')
    b = SWITCH.index(END, a)
    return SWITCH[a:b]


class HeatCapGpuTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oneos-heatcap-')
        self.r = Path(self.temp.name)
        self.stage = self.r / 'stage'
        self.stage.mkdir()
        for name, hw, cur in (('policy0', 2016000, 1555200), ('policy3', 2803200, 1651200),
                              ('policy7', 3360000, 1977600)):
            d = self.r / 'sys/devices/system/cpu/cpufreq' / name
            d.mkdir(parents=True)
            (d / 'cpuinfo_max_freq').write_text(f'{hw}\n')
            (d / 'scaling_max_freq').write_text(f'{cur}\n')
        kgsl = self.r / 'sys/class/kgsl/kgsl-3d0'
        kgsl.mkdir(parents=True)
        (kgsl / 'gpu_available_frequencies').write_text('719000000 680000000 545000000 348000000 220000000\n')
        gpu = self.r / 'sys/kernel/gpu'
        gpu.mkdir(parents=True)
        (gpu / 'gpu_max_clock').write_text('220\n')

    def tearDown(self):
        self.temp.cleanup()

    def inner(self):
        """The loop's script as the switch's shell hands it to `sh -c`, up to the loop."""
        render = subprocess.run(
            ['sh', '-c', 'printf "%s" "' + heat_cap_block() + '"'],
            env={**os.environ, 'BB': 'busybox', 'STAGE': str(self.stage), 'HEAT_CAP': '1',
                 'GUEST_THERMAL_MAP': '0', 'HOST_LOW_POWER': '0'},
            capture_output=True, text=True)
        self.assertEqual(0, render.returncode, render.stderr)
        text = render.stdout.replace('/sys/', str(self.r) + '/sys/')
        return text[:text.index('    lvl=0; first=1;')]

    def run_inner(self, commands):
        done = subprocess.run(['sh', '-c', self.inner() + '\n' + commands], capture_output=True, text=True)
        self.assertEqual(0, done.returncode, done.stderr)
        return done.stdout

    def gpu(self):
        return (self.r / 'sys/kernel/gpu/gpu_max_clock').read_text().strip()

    def test_the_rendered_loop_is_valid_shell(self):
        render = subprocess.run(
            ['sh', '-c', 'printf "%s" "' + heat_cap_block() + '"'],
            env={**os.environ, 'BB': 'busybox', 'STAGE': str(self.stage), 'HEAT_CAP': '1',
                 'GUEST_THERMAL_MAP': '1', 'HOST_LOW_POWER': '0'},
            capture_output=True, text=True)
        self.assertEqual(0, render.returncode, render.stderr)
        check = subprocess.run(['sh', '-n', '-c', render.stdout], capture_output=True, text=True)
        self.assertEqual(0, check.returncode, check.stderr)

    def test_a_stale_gpu_vote_counts_as_a_cap(self):
        out = self.run_inner('gpu_capped && echo capped; echo "top=$GTOP"')
        self.assertIn('capped', out)
        self.assertIn('top=719', out)

    def test_full_lifts_cpu_and_gpu_and_keeps_one_uis_vote(self):
        self.run_inner('full "test"')
        self.assertEqual('719', self.gpu())
        for name, hw in (('policy0', '2016000'), ('policy3', '2803200'), ('policy7', '3360000')):
            got = (self.r / 'sys/devices/system/cpu/cpufreq' / name / 'scaling_max_freq').read_text().strip()
            self.assertEqual(hw, got)
        self.assertEqual('220', (self.stage / 'gpu_max_clock.before').read_text().strip())
        log = (self.stage / 'heat_cap.log').read_text()
        self.assertIn('gpu=719/719', log)
        self.assertIn('policy7=3360000/3360000', log)

    def test_power_saving_keeps_one_uis_gpu_vote(self):
        render = subprocess.run(
            ['sh', '-c', 'printf "%s" "' + heat_cap_block() + '"'],
            env={**os.environ, 'BB': 'busybox', 'STAGE': str(self.stage), 'HEAT_CAP': '1',
                 'GUEST_THERMAL_MAP': '0', 'HOST_LOW_POWER': '1'},
            capture_output=True, text=True)
        text = render.stdout.replace('/sys/', str(self.r) + '/sys/')
        text = text[:text.index('    lvl=0; first=1;')]
        subprocess.run(['sh', '-c', text + '\nfull "test"; c=""; gpu_at 65'], check=True)
        self.assertEqual('348', self.gpu(), 'a level still caps; the lift to full does not')
        (self.r / 'sys/kernel/gpu/gpu_max_clock').write_text('220\n')
        subprocess.run(['sh', '-c', text + '\nfull "test"'], check=True)
        self.assertEqual('220', self.gpu())

    def test_levels_pick_the_highest_step_under_the_percentage(self):
        for pct, want in (('80', '545'), ('65', '348'), ('50', '348')):
            self.run_inner(f'c=""; gpu_at {pct}; echo "$c"')
            self.assertEqual(want, self.gpu(), pct)

    def test_below_the_lowest_step_takes_the_lowest(self):
        (self.r / 'sys/class/kgsl/kgsl-3d0/gpu_available_frequencies').write_text('719000000 600000000\n')
        self.run_inner('c=""; gpu_at 50')
        self.assertEqual('600', self.gpu())

    def test_the_first_vote_is_kept_once(self):
        self.run_inner('c=""; gpu_at 65; gpu_at 100')
        self.assertEqual('220', (self.stage / 'gpu_max_clock.before').read_text().strip())

    def test_no_gpu_node_is_left_alone(self):
        (self.r / 'sys/kernel/gpu/gpu_max_clock').unlink()
        out = self.run_inner('gpu_capped && echo capped; c=""; gpu_at 100; echo "c=$c"; full "test"')
        self.assertNotIn('capped', out)
        self.assertIn('c=', out)
        self.assertFalse((self.stage / 'gpu_max_clock.before').exists())

    def test_the_return_gives_the_gpu_its_top_step(self):
        self.assertIn('if [ -f $S/gpu_max_clock.before ] && [ -w $GMC ]; then', RETURN)
        self.assertIn('fold8_coloros.sh" gpu-top', RETURN)
        top = subprocess.run(
            ['sh', '-c', FOLD8.read_text().replace('/sys/', str(self.r) + '/sys/'), 'fold8', 'gpu-top'],
            env={**os.environ, 'STAGE': str(self.stage)}, capture_output=True, text=True)
        self.assertEqual('719', top.stdout.strip(), top.stderr)

    def _run_s25_loop(self, rounds):
        kgsl = self.r / 'sys/class/kgsl/kgsl-3d0/gpu_available_frequencies'
        kgsl.write_text('1200000000 1050000000 900000000 734000000 525000000 350000000\n')
        (self.r / 'sys/kernel/gpu/gpu_max_clock').write_text('1200\n')
        for p in (self.r / 'sys/devices/system/cpu/cpufreq').iterdir():
            (p / 'scaling_max_freq').write_text((p / 'cpuinfo_max_freq').read_text())
        (self.stage / 'data').mkdir(exist_ok=True)
        (self.stage / 'guest_mntns').write_text('1\n')
        tz = self.r / 'sys/class/thermal/thermal_zone0'
        tz.mkdir(parents=True)
        (tz / 'type').write_text('cpu-1-1-1\n')
        (tz / 'temp').write_text('40000\n')
        for n, (skin, cpu_mc) in enumerate(rounds, 1):
            (self.r / f'round{n}').write_text(
                f"printf '%s' '{skin}' > {self.stage}/skin\n"
                f"echo {cpu_mc} > {tz}/temp\n")
        render = subprocess.run(
            ['sh', '-c', 'printf "%s" "' + heat_cap_block() + '"'],
            env={**os.environ, 'BB': 'busybox', 'STAGE': str(self.stage), 'HEAT_CAP': '1',
                 'HEAT_CAP_CPU': '1', 'GUEST_THERMAL_MAP': '0', 'HOST_LOW_POWER': '0'},
            capture_output=True, text=True)
        self.assertEqual(0, render.returncode, render.stderr)
        loop = render.stdout.replace('/sys/', str(self.r) + '/sys/')
        stubs = f'''
pidof() {{ echo 1234; }}
timeout() {{
    s=$(cat {self.stage}/skin 2>/dev/null)
    echo 'Current temperatures from HAL:'
    [ -n "$s" ] && echo "	Temperature{{mValue=$s, mType=3, mName=SKIN, mStatus=0}}"
    echo 'Current cooling devices from HAL:'
}}
n=1
sleep() {{
    echo "$(cat {self.stage}/heat_cap.level) $(cat {self.r}/sys/kernel/gpu/gpu_max_clock)" >> {self.stage}/gpu_trace
    n=$((n + 1))
    if [ -f {self.r}/round$n ]; then . {self.r}/round$n; else : > {self.stage}/data/oneos_rec_stop; fi
}}
. {self.r}/round1
'''
        done = subprocess.run(['sh', '-c', stubs + loop], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        return ((self.stage / 'heat_cap.log').read_text(),
                (self.stage / 'gpu_trace').read_text().splitlines())

    def test_header_lists_curve_b_skin_thresholds(self):
        log, _ = self._run_s25_loop([(40.0, 40000)])
        self.assertIn('45, 47.5, 50 C skin', log)
        self.assertIn('82, 90 C on the hottest CPU thermal zone', log)

    def test_s25_core_105_spike_caps_gpu_at_734_never_525(self):
        log, trace = self._run_s25_loop([(41.0, 105000)] * 5)
        self.assertEqual(['0 1200', '0 1200', '1 900', '2 734', '2 734'], trace)
        self.assertIn('gpu=734/1200', log)
        self.assertNotIn('gpu=525/1200', log)
        self.assertNotIn('50%:', log)

    def test_s25_skin_50_drops_gpu_to_525_and_hysteresis_restores_1200(self):
        log, trace = self._run_s25_loop([(50.0, 40000), (40.0, 40000), (40.0, 40000), (40.0, 40000)])
        self.assertEqual(['3 525', '3 525', '3 525', '0 1200'], trace)
        self.assertIn('level 0 to 3: 50%:', log)
        self.assertIn('gpu=525/1200', log)
        self.assertIn('level 3 to 0: full clock:', log)
        self.assertIn('gpu=1200/1200', log)


if __name__ == '__main__':
    unittest.main()
