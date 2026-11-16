#!/usr/bin/env python3
"""The heat cap (oneos_switch.sh) also caps on the CPU's own thermal zones.

S23 SM-S911B, ColorOS 17 (2026-11-15): skin 44.6 C, just under the cap's 45,
while cpu-1-3 read 88 C and policy0/policy3 stayed at their top clock. These
tests render the loop's double-quoted `sh -c` string the way the switch's
shell does, point /sys at a fake tree, stub the guest's thermal service, and
run the real loop for a few rounds (each `sleep` moves to the next round).
A CPU level goes up only on two readings running at or above its threshold
(review by the mlcraze5 fork, 2026-11-16), so most cases read twice.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()

START = '    $BB sh -c "\n    : oneos_heat_cap\n'
END = '\n" </dev/null >/dev/null 2>&1 &\n    [ $HEAT_CAP = 1 ] && '

# The S23's zone types (report), Exynos and Tensor's, and zones that are not the CPU.
ZONES = ('cpu-0-0', 'cpu-1-3', 'cpuss-2', 'BIG', 'gpuss-0', 'ddr', 'battery', 'skin-msm-therm', 'mdmss-0', 'nspss-0')


def heat_cap_block():
    a = SWITCH.index(START) + len('    $BB sh -c "')
    b = SWITCH.index(END, a)
    return SWITCH[a:b]


class HeatCapCpuTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oneos-heatcpu-')
        self.r = Path(self.temp.name)
        self.stage = self.r / 'stage'
        (self.stage / 'data').mkdir(parents=True)
        (self.stage / 'guest_mntns').write_text('1\n')
        for name, hw in (('policy0', 2016000), ('policy3', 2803200), ('policy7', 3360000)):
            d = self.r / 'sys/devices/system/cpu/cpufreq' / name
            d.mkdir(parents=True)
            (d / 'cpuinfo_max_freq').write_text(f'{hw}\n')
            (d / 'scaling_max_freq').write_text(f'{hw}\n')
        for i, ty in enumerate(ZONES):
            z = self.r / f'sys/class/thermal/thermal_zone{i}'
            z.mkdir(parents=True)
            (z / 'type').write_text(ty + '\n')
            (z / 'temp').write_text('40000\n')

    def tearDown(self):
        self.temp.cleanup()

    def round_script(self, skin, zones):
        lines = [f"printf '%s' '{'' if skin is None else skin}' > {self.stage}/skin"]
        for i, ty in enumerate(ZONES):
            lines.append(f"echo {zones.get(ty, 40000)} > {self.r}/sys/class/thermal/thermal_zone{i}/temp")
        return '\n'.join(lines) + '\n'

    def run_loop(self, rounds, cpu='1'):
        """rounds: (skin C or None, {zone type: millidegrees}) per 10 s round."""
        for n, (skin, zones) in enumerate(rounds, 1):
            (self.r / f'round{n}').write_text(self.round_script(skin, zones))
        render = subprocess.run(
            ['sh', '-c', 'printf "%s" "' + heat_cap_block() + '"'],
            env={**os.environ, 'BB': 'busybox', 'STAGE': str(self.stage), 'HEAT_CAP': '1',
                 'HEAT_CAP_CPU': cpu, 'GUEST_THERMAL_MAP': '0', 'HOST_LOW_POWER': '0'},
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
    n=$((n + 1))
    if [ -f {self.r}/round$n ]; then . {self.r}/round$n; else : > {self.stage}/data/oneos_rec_stop; fi
}}
. {self.r}/round1
'''
        done = subprocess.run(['sh', '-c', stubs + loop], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        return [l for l in (self.stage / 'heat_cap.log').read_text().splitlines() if not l.startswith('#')]

    def freq(self, name='policy0'):
        return (self.r / 'sys/devices/system/cpu/cpufreq' / name / 'scaling_max_freq').read_text().strip()

    def test_the_rendered_loop_is_valid_shell(self):
        for cpu in ('0', '1'):
            render = subprocess.run(
                ['sh', '-c', 'printf "%s" "' + heat_cap_block() + '"'],
                env={**os.environ, 'BB': 'busybox', 'STAGE': str(self.stage), 'HEAT_CAP': '1',
                     'HEAT_CAP_CPU': cpu, 'GUEST_THERMAL_MAP': '1', 'HOST_LOW_POWER': '0'},
                capture_output=True, text=True)
            self.assertEqual(0, render.returncode, render.stderr)
            check = subprocess.run(['sh', '-n', '-c', render.stdout], capture_output=True, text=True)
            self.assertEqual(0, check.returncode, check.stderr)

    def test_cpu_76_is_level_1(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 76000})] * 2)
        self.assertIn('skin 40.0 C, cpu 76.0 C (cpu-1-3): no cap', log[0])
        self.assertIn('skin 40.0 C, cpu 76.0 C (cpu-1-3), level 0 to 1: 80%', log[1])
        self.assertIn('policy0=1612800/2016000', log[1])

    def test_one_spike_does_not_cap(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 88000}), (40.0, {'cpu-1-3': 60000}),
                             (40.0, {'cpu-1-3': 88000}), (40.0, {'cpu-1-3': 60000})])
        self.assertEqual(['skin 40.0 C, cpu 88.0 C (cpu-1-3): no cap'], [l.split(' ', 1)[1] for l in log])
        self.assertEqual('2016000', self.freq())

    def test_two_readings_running_cap(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 60000}), (40.0, {'cpu-1-3': 88000}), (40.0, {'cpu-1-3': 88000})])
        self.assertEqual(3, len(log), log)
        self.assertIn('no cap', log[0])
        self.assertIn('cpu 88.0 C (cpu-1-3), level 0 to 3: 50%', log[1])
        self.assertIn('stopped: full clock', log[2])

    def test_up_only_as_far_as_both_readings(self):
        # 81 then 86: level 2 (both reach 80), then 3 once 85 is read twice
        log = self.run_loop([(40.0, {'cpu-1-3': 81000}), (40.0, {'cpu-1-3': 86000}), (40.0, {'cpu-1-3': 86000})])
        self.assertIn('cpu 86.0 C (cpu-1-3), level 0 to 2: 65%', log[1])
        self.assertIn('cpu 86.0 C (cpu-1-3), level 2 to 3: 50%', log[2])

    def test_leaving_takes_one_reading(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 86000})] * 2 + [(40.0, {'cpu-1-3': 81000})])
        self.assertIn('level 0 to 3: 50%', log[1])
        self.assertIn('cpu 81.0 C (cpu-1-3), level 3 to 2: 65%', log[2])

    def test_skin_still_takes_one_reading(self):
        log = self.run_loop([(47.5, {})])
        self.assertIn('skin 47.5 C, cpu 40.0 C (cpu-0-0), level 0 to 2: 65%', log[0])

    def test_the_s23_report_caps_at_50(self):
        log = self.run_loop([(44.6, {'cpu-1-3': 88000, 'cpuss-2': 79200, 'cpu-0-0': 84000})] * 2)
        self.assertIn('skin 44.6 C, cpu 88.0 C (cpu-1-3), level 0 to 3: 50%', log[1])
        self.assertIn('policy0=1008000/2016000 policy3=1401600/2803200', log[1])
        self.assertIn('full clock', log[-1], 'the return gives the clocks back')

    def test_hysteresis(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 85000})] * 2 + [(40.0, {'cpu-1-3': 83000}), (40.0, {'cpu-1-3': 81000})])
        self.assertIn('level 0 to 3: 50%', log[1])
        self.assertIn('cpu 81.0 C (cpu-1-3), level 3 to 2: 65%', log[2])
        self.assertEqual(4, len(log), log)

    def test_skin_higher_than_cpu_wins(self):
        log = self.run_loop([(50.5, {'cpu-1-3': 76000})])
        self.assertIn('skin 50.5 C, cpu 76.0 C (cpu-1-3), level 0 to 3: 50%', log[0])

    def test_no_skin_but_cpu_still_caps(self):
        log = self.run_loop([(None, {'cpuss-2': 81000})] * 2)
        self.assertIn('cpu 81.0 C (cpuss-2), level 0 to 2: 65%', log[1])
        self.assertNotIn('skin', log[1])
        self.assertIn('policy0=1310400/2016000', log[1])

    def test_exynos_zone_names_count(self):
        log = self.run_loop([(40.0, {'BIG': 80500})] * 2)
        self.assertIn('cpu 80.5 C (BIG), level 0 to 2', log[1])

    def test_knob_off_ignores_the_cpu(self):
        log = self.run_loop([(44.6, {'cpu-1-3': 88000})], cpu='0')
        self.assertEqual('skin 44.6 C: no cap', log[0].split(' ', 1)[1])
        self.assertEqual('2016000', self.freq())
        header = (self.stage / 'heat_cap.log').read_text()
        self.assertIn('ONEOS_HEAT_CAP_CPU=0', header)
        self.assertEqual([], self.run_loop([(None, {'cpu-1-3': 88000})], cpu='0'))

    def test_other_zones_are_ignored(self):
        log = self.run_loop([(40.0, {'gpuss-0': 95000, 'ddr': 90000, 'battery': 99000, 'skin-msm-therm': 97000,
                                     'mdmss-0': 96000, 'nspss-0': 94000, 'cpu-1-3': 60000})])
        self.assertEqual('skin 40.0 C, cpu 60.0 C (cpu-1-3): no cap', log[0].split(' ', 1)[1])
        self.assertEqual('2016000', self.freq())

    def test_bad_readings_are_ignored(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 'abc', 'cpuss-2': 200000, 'cpu-0-0': -5000, 'BIG': 70000})])
        self.assertEqual('skin 40.0 C, cpu 70.0 C (BIG): no cap', log[0].split(' ', 1)[1])

    def test_the_header_names_the_cpu_thresholds(self):
        self.run_loop([(40.0, {})])
        self.assertIn('75, 80, 85 C on the hottest CPU thermal zone (two readings running to go up, 3 C hysteresis)',
                      (self.stage / 'heat_cap.log').read_text())


if __name__ == '__main__':
    unittest.main()
