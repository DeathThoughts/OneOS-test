#!/usr/bin/env python3
"""The heat cap (oneos_switch.sh) also caps on the CPU's own thermal zones.

S23 SM-S911B, ColorOS 17 (2026-11-15): skin 44.6 C, just under the cap's 45,
while cpu-1-3 read 88 C and policy0/policy3 stayed at their top clock. These
tests render the loop's double-quoted `sh -c` string the way the switch's
shell does, point /sys at a fake tree, stub the guest's thermal service, and
run the real loop for a few rounds (each `sleep` moves to the next round).
A CPU level goes up only on three readings running at or above its threshold
(review by the mlcraze5 fork, 2026-11-16; three since the S25 Ultra's session
bceaef29fb128e94, where two-reading spikes of 94-106 C capped at 50%), so most
cases read three times. A reading held back logs a "held" line; run_loop
leaves those out unless asked for them. Samsung's AP sensor (mName=AP) is
logged beside skin and the CPU and nothing else (S25 Ultra, sessions
bceaef29fb128e94, c2d30e95cde9775b, 2c804690b9171656): a round may give it as
a third value.
"""
import os
import re
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

    def round_script(self, skin, zones, ap=None):
        lines = [f"printf '%s' '{'' if skin is None else skin}' > {self.stage}/skin",
                 f"printf '%s' '{'' if ap is None else ap}' > {self.stage}/ap"]
        for i, ty in enumerate(ZONES):
            lines.append(f"echo {zones.get(ty, 40000)} > {self.r}/sys/class/thermal/thermal_zone{i}/temp")
        return '\n'.join(lines) + '\n'

    def run_loop(self, rounds, cpu='1', held=False, apu=None, thermal_map='0'):
        """rounds: (skin C or None, {zone type: millidegrees}[, AP C]) per 10 s round.
        apu: an APU temperature listed before AP every round, which must not be read as AP."""
        for n, rnd in enumerate(rounds, 1):
            (self.r / f'round{n}').write_text(self.round_script(*rnd))
        render = subprocess.run(
            ['sh', '-c', 'printf "%s" "' + heat_cap_block() + '"'],
            env={**os.environ, 'BB': 'busybox', 'STAGE': str(self.stage), 'HEAT_CAP': '1',
                 'HEAT_CAP_CPU': cpu, 'GUEST_THERMAL_MAP': thermal_map, 'HOST_LOW_POWER': '0'},
            capture_output=True, text=True)
        self.assertEqual(0, render.returncode, render.stderr)
        loop = render.stdout.replace('/sys/', str(self.r) + '/sys/')
        stubs = f'''
pidof() {{ echo 1234; }}
timeout() {{
    s=$(cat {self.stage}/skin 2>/dev/null)
    echo 'Current temperatures from HAL:'
    a=$(cat {self.stage}/ap 2>/dev/null)
    {'' if apu is None else f'echo "	Temperature{{{{mValue={apu}, mType=0, mName=APU, mStatus=0}}}}"'}
    [ -n "$s" ] && echo "	Temperature{{mValue=$s, mType=3, mName=SKIN, mStatus=0}}"
    [ -n "$a" ] && echo "	Temperature{{mValue=$a, mType=0, mName=AP, mStatus=0}}"
    echo 'Current cooling devices from HAL:'
}}
n=1
sleep() {{
    echo "$(cat {self.stage}/heat_cap.level) $(cat {self.r}/sys/devices/system/cpu/cpufreq/policy*/scaling_max_freq | tr '\\n' ' ')" >> {self.stage}/trace
    n=$((n + 1))
    if [ -f {self.r}/round$n ]; then . {self.r}/round$n; else : > {self.stage}/data/oneos_rec_stop; fi
}}
. {self.r}/round1
'''
        done = subprocess.run(['sh', '-c', stubs + loop], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        return [l for l in (self.stage / 'heat_cap.log').read_text().splitlines()
                if not l.startswith('#') and (held or ' held: ' not in l)]

    def fresh(self):
        self.tearDown()
        self.setUp()

    def trace(self):
        """heat_cap.level and every policy's scaling_max_freq after each round."""
        return (self.stage / 'trace').read_text().splitlines()

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
        log = self.run_loop([(40.0, {'cpu-1-3': 76000})] * 3)
        self.assertIn('skin 40.0 C, cpu 76.0 C (cpu-1-3): no cap', log[0])
        self.assertIn('skin 40.0 C, cpu 76.0 C (cpu-1-3), level 0 to 1: 80%', log[1])
        self.assertIn('policy0=1612800/2016000', log[1])

    def test_one_spike_does_not_cap(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 88000}), (40.0, {'cpu-1-3': 60000}),
                             (40.0, {'cpu-1-3': 88000}), (40.0, {'cpu-1-3': 60000})])
        self.assertEqual(['skin 40.0 C, cpu 88.0 C (cpu-1-3): no cap'], [l.split(' ', 1)[1] for l in log])
        self.assertEqual('2016000', self.freq())

    def test_two_hot_readings_then_a_cool_one_do_not_cap(self):
        # The S25 Ultra's pattern (bceaef29fb128e94): 104 C, 98 C, then 72 C.
        log = self.run_loop([(44.0, {'cpu-1-3': 60000}), (44.0, {'cpu-1-3': 104300}),
                             (44.0, {'cpu-1-3': 98000}), (44.0, {'cpu-1-3': 72000})])
        self.assertEqual(['skin 44.0 C, cpu 60.0 C (cpu-1-3): no cap'], [l.split(' ', 1)[1] for l in log])
        self.assertEqual('2016000', self.freq())

    def test_three_readings_running_cap(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 60000})] + [(40.0, {'cpu-1-3': 88000})] * 3)
        self.assertEqual(3, len(log), log)
        self.assertIn('no cap', log[0])
        self.assertIn('cpu 88.0 C (cpu-1-3), level 0 to 3: 50%', log[1])
        self.assertIn('stopped: full clock', log[2])

    def test_two_readings_running_no_longer_cap(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 60000})] + [(40.0, {'cpu-1-3': 88000})] * 2)
        self.assertEqual(['skin 40.0 C, cpu 60.0 C (cpu-1-3): no cap'], [l.split(' ', 1)[1] for l in log])
        self.assertEqual('2016000', self.freq())

    def test_the_held_line(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 70200}), (40.0, {'cpu-1-3': 79100}),
                             (40.0, {'cpu-1-3': 104300}), (40.0, {'cpu-1-3': 60000})], held=True)
        rest = [l.split(' ', 1)[1] for l in log]
        self.assertEqual(['skin 40.0 C, cpu 70.2 C (cpu-1-3): no cap',
                          'cpu 79.1 C (cpu-1-3) held: needs three readings over 75 C (before: 70.2, 0.0)',
                          'cpu 104.3 C (cpu-1-3) held: needs three readings over 85 C (before: 79.1, 70.2)'], rest)
        self.assertRegex(log[1], r'^\d\d:\d\d:\d\d cpu ')

    def test_no_held_line_on_quiet_or_agreed_readings(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 60000})] * 2 + [(40.0, {'cpu-1-3': 86000})] * 4, held=True)
        held = [l for l in log if ' held: ' in l]
        self.assertEqual(2, len(held), log)
        self.assertIn('level 0 to 3: 50%', log[3])
        self.assertEqual(5, len(log), log)

    def test_up_only_as_far_as_all_three_readings(self):
        # 81, 81 then 86: level 2 (all three reach 80), then 3 once 85 is read three times
        log = self.run_loop([(40.0, {'cpu-1-3': 81000})] * 2 + [(40.0, {'cpu-1-3': 86000})] * 3)
        self.assertIn('cpu 86.0 C (cpu-1-3), level 0 to 2: 65%', log[1])
        self.assertIn('cpu 86.0 C (cpu-1-3), level 2 to 3: 50%', log[2])

    def test_leaving_takes_one_reading(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 86000})] * 3 + [(40.0, {'cpu-1-3': 81000})])
        self.assertIn('level 0 to 3: 50%', log[1])
        self.assertIn('cpu 81.0 C (cpu-1-3), level 3 to 2: 65%', log[2])

    def test_skin_still_takes_one_reading(self):
        log = self.run_loop([(47.5, {})])
        self.assertIn('skin 47.5 C, cpu 40.0 C (cpu-0-0), level 0 to 2: 65%', log[0])

    def test_the_s23_report_caps_at_50(self):
        log = self.run_loop([(44.6, {'cpu-1-3': 88000, 'cpuss-2': 79200, 'cpu-0-0': 84000})] * 3)
        self.assertIn('skin 44.6 C, cpu 88.0 C (cpu-1-3), level 0 to 3: 50%', log[1])
        self.assertIn('policy0=1008000/2016000 policy3=1401600/2803200', log[1])
        self.assertIn('full clock', log[-1], 'the return gives the clocks back')

    def test_hysteresis(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 85000})] * 3 + [(40.0, {'cpu-1-3': 83000}), (40.0, {'cpu-1-3': 81000})])
        self.assertIn('level 0 to 3: 50%', log[1])
        self.assertIn('cpu 81.0 C (cpu-1-3), level 3 to 2: 65%', log[2])
        self.assertEqual(4, len(log), log)

    def test_skin_higher_than_cpu_wins(self):
        log = self.run_loop([(50.5, {'cpu-1-3': 76000})])
        self.assertIn('skin 50.5 C, cpu 76.0 C (cpu-1-3), level 0 to 3: 50%', log[0])

    def test_no_skin_but_cpu_still_caps(self):
        log = self.run_loop([(None, {'cpuss-2': 81000})] * 3)
        self.assertIn('cpu 81.0 C (cpuss-2), level 0 to 2: 65%', log[1])
        self.assertNotIn('skin', log[1])
        self.assertIn('policy0=1310400/2016000', log[1])

    def test_exynos_zone_names_count(self):
        log = self.run_loop([(40.0, {'BIG': 80500})] * 3)
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

    def test_ap_is_logged_beside_skin_and_cpu(self):
        log = self.run_loop([(44.7, {'cpu-1-3': 60000}, 49.6)] + [(44.7, {'cpu-1-3': 104300}, 49.6)] * 3)
        self.assertEqual('skin 44.7 C, ap 49.6 C, cpu 60.0 C (cpu-1-3): no cap', log[0].split(' ', 1)[1])
        self.assertIn('skin 44.7 C, ap 49.6 C, cpu 104.3 C (cpu-1-3), level 0 to 3: 50%', log[1])
        self.assertIn('stopped: full clock', log[2])

    def test_ap_in_the_held_line(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 70200}, 48.0), (40.0, {'cpu-1-3': 74800}, 49.1),
                             (40.0, {'cpu-1-3': 104300}, 51.2)], held=True)
        rest = [l.split(' ', 1)[1] for l in log]
        self.assertEqual('cpu 104.3 C (cpu-1-3), ap 51.2 C held: needs three readings over 85 C (before: 74.8, 70.2)',
                         rest[1])

    def test_ap_leaving_a_level(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 86000}, 52.0)] * 3 + [(40.0, {'cpu-1-3': 60000}, 48.5)])
        self.assertIn('skin 40.0 C, ap 48.5 C, cpu 60.0 C (cpu-1-3), level 3 to 0: full clock', log[2])

    def test_ap_with_no_skin(self):
        log = self.run_loop([(None, {'cpuss-2': 81000}, 50.3)] * 3)
        self.assertIn('ap 50.3 C, cpu 81.0 C (cpuss-2), level 0 to 2: 65%', log[1])
        self.assertNotIn('skin', log[1])

    def test_no_ap_reading_leaves_it_out(self):
        log = self.run_loop([(44.7, {'cpu-1-3': 60000})] + [(44.7, {'cpu-1-3': 104300})] * 3, held=True)
        self.assertTrue(log)
        for l in log:
            self.assertNotRegex(l, r'\bap [0-9]')
        self.assertIn('skin 44.7 C, cpu 104.3 C (cpu-1-3), level 0 to 3: 50%', log[3])

    def test_apu_is_not_ap(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 60000})], apu=77.7)
        self.assertEqual('skin 40.0 C, cpu 60.0 C (cpu-1-3): no cap', log[0].split(' ', 1)[1])
        self.fresh()
        log = self.run_loop([(40.0, {'cpu-1-3': 60000}, 49.6)], apu=77.7)
        self.assertEqual('skin 40.0 C, ap 49.6 C, cpu 60.0 C (cpu-1-3): no cap', log[0].split(' ', 1)[1])

    def test_ap_in_the_guest_thermal_status_line(self):
        log = self.run_loop([(47.5, {}, 51.0)], thermal_map='1')
        self.assertTrue(any(l.split(' ', 1)[1].startswith('skin 47.5 C, ap 51.0 C: guest thermal status 1 ')
                            for l in log), log)

    def test_ap_changes_no_level(self):
        # The same skin and CPU rounds with AP readings (cool, hot, missing) and without:
        # the same levels, clocks and log lines once the ap part is taken out.
        rounds = [(40.0, {'cpu-1-3': 60000}), (44.7, {'cpu-1-3': 104300}), (46.0, {'cpu-1-3': 98000}),
                  (47.5, {'cpu-1-3': 91900}), (50.5, {'cpu-1-3': 86000}), (44.0, {'cpu-1-3': 81000}),
                  (None, {'cpu-1-3': 74800}), (40.0, {'cpu-1-3': 60000})]
        aps = [30.0, 99.0, 49.6, None, 120.5, 53.1, 48.0, 75.0]

        without = self.run_loop(rounds, held=True)
        trace_without = self.trace()
        self.fresh()
        with_ap = self.run_loop([r + (a,) for r, a in zip(rounds, aps)], held=True)
        self.assertEqual(len(rounds), len(trace_without))
        self.assertEqual(trace_without, self.trace())
        self.assertTrue(any(l.startswith('3 ') for l in trace_without), trace_without)
        self.assertNotEqual(without, with_ap, 'the AP readings were logged')
        strip = lambda ls: [re.sub(r'ap [0-9.]+ C, |, ap [0-9.]+ C', '', l.split(' ', 1)[1]) for l in ls]
        self.assertEqual(strip(without), strip(with_ap))

    def test_the_header_names_the_cpu_thresholds(self):
        self.run_loop([(40.0, {})])
        self.assertIn('75, 80, 85 C on the hottest CPU thermal zone (three readings running to go up, 3 C hysteresis)',
                      (self.stage / 'heat_cap.log').read_text())


if __name__ == '__main__':
    unittest.main()
