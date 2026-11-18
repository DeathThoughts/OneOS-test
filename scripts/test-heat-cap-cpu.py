#!/usr/bin/env python3
"""The heat cap (oneos_switch.sh) caps on skin and the CPU's own thermal zones.

S23 SM-S911B, ColorOS 17 (2026-11-15): skin sat at 44.6 C, just under 45,
while cpu-1-3 read 88 C and policy0/policy3 stayed at their top clock. Under
Curve B (S25 Ultra coloros17-findx9u-in-1105, sessions db475ad5017133c4 and
a12bf19e1931e81a), skin enters immediately at 45.0 (80%), 46.5 (65%), and
48.0 C (50%) and waits three readings 2 C under to step down; CPU thermal zones
never exceed level 2 (level 3 is skin >= 48.0 C only), entering level 1 at
>= 82 C for three readings and level 2 at >= 90 C for four readings, and
stepping down on three readings 4 C under (< 78, < 86 C). Up-holds and
down-holds log a "held" line; run_loop leaves those out unless asked for them.
Samsung's AP sensor (mName=AP) is logged beside skin and the CPU and nothing
else (sessions bceaef29fb128e94, c2d30e95cde9775b, 2c804690b9171656).
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

# The S23/S25 zone types, Exynos and Tensor's, and zones that are not the CPU.
ZONES = ('cpu-0-0', 'cpu-1-1-1', 'cpu-1-3', 'cpuss-2', 'BIG', 'gpuss-0', 'ddr', 'battery', 'skin-msm-therm', 'mdmss-0', 'nspss-0')


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
        cfl_dir = self.r / 'sys/devices/system/cpu/cpufreq_limit'
        cfl_dir.mkdir(parents=True)
        (cfl_dir / 'cpufreq_max_limit').write_text('-1\n')

    def tearDown(self):
        self.temp.cleanup()

    def round_script(self, skin, zones, ap=None, camera=None, cfl=None):
        lines = [f"printf '%s' '{'' if skin is None else skin}' > {self.stage}/skin",
                 f"printf '%s' '{'' if ap is None else ap}' > {self.stage}/ap"]
        for i, ty in enumerate(ZONES):
            lines.append(f"echo {zones.get(ty, 40000)} > {self.r}/sys/class/thermal/thermal_zone{i}/temp")
        if camera is True:
            lines.append(f": > {self.stage}/camera_on")
        elif camera is False:
            lines.append(f"rm -f {self.stage}/camera_on")
        if cfl is not None:
            lines.append(f"echo {cfl} > {self.r}/sys/devices/system/cpu/cpufreq_limit/cpufreq_max_limit")
        return '\n'.join(lines) + '\n'

    def run_loop(self, rounds, cpu='1', held=False, apu=None, thermal_map='0', cfl='-1', low_power='0', camera=False):
        """rounds: (skin C or None, {zone type: millidegrees}[, AP C, camera, cfl]) per 10 s round.
        apu: an APU temperature listed before AP every round, which must not be read as AP."""
        (self.r / 'sys/devices/system/cpu/cpufreq_limit/cpufreq_max_limit').write_text(f'{cfl}\n')
        if camera:
            (self.stage / 'camera_on').write_text('')
        else:
            (self.stage / 'camera_on').unlink(missing_ok=True)
        for n, rnd in enumerate(rounds, 1):
            (self.r / f'round{n}').write_text(self.round_script(*rnd))
        render = subprocess.run(
            ['sh', '-c', 'printf "%s" "' + heat_cap_block() + '"'],
            env={**os.environ, 'BB': 'busybox', 'STAGE': str(self.stage), 'HEAT_CAP': '1',
                 'HEAT_CAP_CPU': cpu, 'GUEST_THERMAL_MAP': thermal_map, 'HOST_LOW_POWER': low_power},
            capture_output=True, text=True)
        self.assertEqual(0, render.returncode, render.stderr)
        loop = render.stdout.replace('/sys/', str(self.r) + '/sys/')
        stubs = f'''
pidof() {{
    case "$1" in
        com.sec.android.app.camera) [ -f {self.stage}/camera_on ] && echo 5678 ;;
        *) echo 1234 ;;
    esac
}}
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
    echo "$(cat {self.r}/sys/devices/system/cpu/cpufreq_limit/cpufreq_max_limit) $([ -f {self.stage}/cpufreq_max_limit.released ] && cat {self.stage}/cpufreq_max_limit.released || echo -)" >> {self.stage}/cfl_trace
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

    def cfl(self):
        return (self.r / 'sys/devices/system/cpu/cpufreq_limit/cpufreq_max_limit').read_text().strip()

    def cfl_trace(self):
        """cpufreq_max_limit and cpufreq_max_limit.released (or '-') after each round."""
        return (self.stage / 'cfl_trace').read_text().splitlines()

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

    def test_cpu_82_is_level_1(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 81900})] * 3)
        self.assertEqual(['skin 40.0 C, cpu 81.9 C (cpu-1-3): no cap'], [l.split(' ', 1)[1] for l in log])
        self.assertEqual('2016000', self.freq())
        self.fresh()
        log = self.run_loop([(40.0, {'cpu-1-3': 82000})] * 3)
        self.assertIn('skin 40.0 C, cpu 82.0 C (cpu-1-3): no cap', log[0])
        self.assertIn('skin 40.0 C, cpu 82.0 C (cpu-1-3), level 0 to 1: 80%', log[1])
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
        self.assertIn('cpu 88.0 C (cpu-1-3), level 0 to 1: 80%', log[1])
        self.assertIn('stopped: full clock', log[2])

    def test_two_readings_running_no_longer_cap(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 60000})] + [(40.0, {'cpu-1-3': 88000})] * 2)
        self.assertEqual(['skin 40.0 C, cpu 60.0 C (cpu-1-3): no cap'], [l.split(' ', 1)[1] for l in log])
        self.assertEqual('2016000', self.freq())

    def test_the_held_line(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 70200}), (40.0, {'cpu-1-3': 83100}),
                             (40.0, {'cpu-1-3': 104300}), (40.0, {'cpu-1-3': 91000}),
                             (40.0, {'cpu-1-3': 60000})], held=True)
        rest = [l.split(' ', 1)[1] for l in log]
        self.assertEqual(['skin 40.0 C, cpu 70.2 C (cpu-1-3): no cap',
                          'cpu 83.1 C (cpu-1-3) held: needs three readings over 82 C (before: 70.2, 0.0)',
                          'cpu 104.3 C (cpu-1-3) held: needs four readings over 90 C (before: 83.1, 70.2, 0.0)',
                          'cpu 91.0 C (cpu-1-3) held: needs four readings over 90 C (before: 104.3, 83.1, 70.2)'],
                         rest[:4])
        self.assertIn('skin 40.0 C, cpu 91.0 C (cpu-1-3), level 0 to 1: 80%:', rest[4])
        self.assertEqual('cpu 60.0 C (cpu-1-3) held: needs three readings under 78 C (before: 91.0, 104.3)', rest[5])
        self.assertIn('stopped: full clock:', rest[6])
        self.assertRegex(log[1], r'^\d\d:\d\d:\d\d cpu ')

    def test_no_held_line_on_quiet_or_agreed_readings(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 60000})] * 2 + [(40.0, {'cpu-1-3': 86000})] * 4, held=True)
        held = [l for l in log if ' held: ' in l]
        self.assertEqual(2, len(held), log)
        self.assertIn('level 0 to 1: 80%', log[3])
        self.assertEqual(5, len(log), log)

    def test_up_only_as_far_as_all_readings(self):
        # 83, 83 then 91 x4: level 1 on tick 3 (all three >= 82), level 2 on tick 6 (all four >= 90).
        log = self.run_loop([(40.0, {'cpu-1-3': 83000})] * 2 + [(40.0, {'cpu-1-3': 91000})] * 4)
        self.assertIn('cpu 91.0 C (cpu-1-3), level 0 to 1: 80%', log[1])
        self.assertIn('cpu 91.0 C (cpu-1-3), level 1 to 2: 65%', log[2])
        self.assertEqual(['0 ', '0 ', '1 ', '1 ', '1 ', '2 '], [l[:2] for l in self.trace()])

    def test_skin_and_cpu_step_down_hysteresis_requires_three_readings(self):
        # Skin: L3 at 48.0, two 40.0 readings hold at L3, third 40.0 restores full clock.
        log = self.run_loop([(48.0, {}), (40.0, {}), (40.0, {}), (40.0, {})], held=True)
        self.assertIn('level 0 to 3: 50%', log[0])
        self.assertIn('skin 40.0 C held: needs three readings under 43 C', log[1])
        self.assertIn('skin 40.0 C held: needs three readings under 43 C', log[2])
        self.assertIn('level 3 to 0: full clock', log[3])
        self.assertEqual(['3 ', '3 ', '3 ', '0 '], [l[:2] for l in self.trace()])

        # Skin L2 boundary: 46.5 -> 44.5 stays L2; 44.4 x3 steps down to L1.
        self.fresh()
        log = self.run_loop([(46.5, {})] + [(44.5, {})] * 3 + [(44.4, {})] * 3, held=True)
        self.assertEqual(['2 ', '2 ', '2 ', '2 ', '2 ', '2 ', '1 '], [l[:2] for l in self.trace()])
        self.assertIn('skin 44.4 C held: needs three readings under 44.5 C', log[1])
        self.assertIn('skin 44.4 C, cpu 40.0 C (cpu-0-0), level 2 to 1: 80%', log[3])

        # Skin warm bounce resets consecutive cool count.
        self.fresh()
        self.run_loop([(48.0, {}), (40.0, {}), (40.0, {}), (46.0, {}), (40.0, {}), (40.0, {}), (40.0, {})])
        self.assertEqual(['3 ', '3 ', '3 ', '3 ', '3 ', '3 ', '0 '], [l[:2] for l in self.trace()])

        # CPU: L2 at 92.0 x4, two 70.0 readings hold at L2, third 70.0 restores full clock.
        self.fresh()
        log = self.run_loop([(40.0, {'cpu-1-3': 92000})] * 4 + [(40.0, {'cpu-1-3': 70000})] * 3, held=True)
        self.assertIn('level 0 to 1: 80%', log[4])
        self.assertIn('level 1 to 2: 65%', log[5])
        self.assertIn('cpu 70.0 C (cpu-1-3) held: needs three readings under 78 C', log[6])
        self.assertIn('cpu 70.0 C (cpu-1-3) held: needs three readings under 78 C', log[7])
        self.assertIn('level 2 to 0: full clock', log[8])
        self.assertEqual(['0 ', '0 ', '1 ', '2 ', '2 ', '2 ', '0 '], [l[:2] for l in self.trace()])

    def test_a_missed_skin_reading_holds_the_skin_level(self):
        # dumpsys timed out twice: the level used to drop to the CPU level alone
        # (1,0,0,1: full clock for two ticks, then capped again).
        self.run_loop([(46.0, {}), (None, {}), (None, {}), (44.0, {})])
        self.assertEqual(['1 ', '1 ', '1 ', '1 '], [l[:2] for l in self.trace()])

        # The skin history is left alone on a missed tick: 46.5 (L2), 44.0 (held,
        # first of three), a miss, then 44.0 and 44.0 are the second and third.
        self.fresh()
        log = self.run_loop([(46.5, {}), (44.0, {}), (None, {}), (44.0, {}), (44.0, {})], held=True)
        self.assertEqual(['2 ', '2 ', '2 ', '2 ', '1 '], [l[:2] for l in self.trace()])
        self.assertIn('level 2 to 1: 80%', log[-2])

        # A miss at level 0 stays level 0, and a missed skin with a hot CPU still
        # caps on the CPU alone.
        self.fresh()
        log = self.run_loop([(None, {})] * 2)
        self.assertEqual(['cpu 40.0 C (cpu-0-0): no cap'], [l.split(' ', 1)[1] for l in log])
        self.assertEqual(['0 ', '0 '], [l[:2] for l in self.trace()])
        self.fresh()
        self.run_loop([(46.0, {'cpu-1-3': 92000}), (None, {'cpu-1-3': 92000}), (None, {'cpu-1-3': 92000}),
                       (None, {'cpu-1-3': 92000})])
        self.assertEqual(['1 ', '1 ', '1 ', '2 '], [l[:2] for l in self.trace()])

    def test_skin_still_takes_one_reading(self):
        log = self.run_loop([(46.4, {})])
        self.assertIn('skin 46.4 C, cpu 40.0 C (cpu-0-0), level 0 to 1: 80%', log[0])
        self.fresh()
        log = self.run_loop([(46.5, {})])
        self.assertIn('skin 46.5 C, cpu 40.0 C (cpu-0-0), level 0 to 2: 65%', log[0])
        self.fresh()
        log = self.run_loop([(44.9, {})])
        self.assertIn('skin 44.9 C, cpu 40.0 C (cpu-0-0): no cap', log[0])

    def test_the_s23_report_caps_at_80(self):
        log = self.run_loop([(44.6, {'cpu-1-3': 88000, 'cpuss-2': 79200, 'cpu-0-0': 84000})] * 3)
        self.assertIn('skin 44.6 C, cpu 88.0 C (cpu-1-3), level 0 to 1: 80%', log[1])
        self.assertIn('policy0=1612800/2016000 policy3=2242560/2803200', log[1])
        self.assertIn('full clock', log[-1], 'the return gives the clocks back')

    def test_hysteresis(self):
        # 90 C x4 -> L2; 86.0 C (= 90 - 4) stays L2; 85.9 C x3 (< 86) steps down to L1.
        log = self.run_loop([(40.0, {'cpu-1-3': 90000})] * 4
                            + [(40.0, {'cpu-1-3': 86000})] * 3
                            + [(40.0, {'cpu-1-3': 85900})] * 3)
        self.assertIn('level 0 to 1: 80%', log[1])
        self.assertIn('level 1 to 2: 65%', log[2])
        self.assertIn('cpu 85.9 C (cpu-1-3), level 2 to 1: 80%', log[3])
        self.assertEqual(5, len(log), log)
        self.assertEqual(['0 ', '0 ', '1 ', '2 ', '2 ', '2 ', '2 ', '2 ', '2 ', '1 '],
                         [l[:2] for l in self.trace()])

    def test_core_105_spike_at_41_skin_caps_at_level_2_never_3(self):
        log = self.run_loop([(41.0, {'cpu-1-1-1': 105000})] * 6)
        self.assertIn('cpu 105.0 C (cpu-1-1-1), level 0 to 1: 80%', log[1])
        self.assertIn('cpu 105.0 C (cpu-1-1-1), level 1 to 2: 65%', log[2])
        self.assertIn('stopped: full clock', log[3])
        self.assertEqual(4, len(log), log)
        self.assertFalse(any('50%' in l or 'to 3:' in l or 'level 3' in l for l in log), log)
        self.assertEqual(['0 ', '0 ', '1 ', '2 ', '2 ', '2 '], [l[:2] for l in self.trace()])

    def test_skin_48_reaches_level_3(self):
        log = self.run_loop([(48.0, {'cpu-1-3': 60000})])
        self.assertIn('skin 48.0 C, cpu 60.0 C (cpu-1-3), level 0 to 3: 50%', log[0])
        self.assertTrue(self.trace()[0].startswith('3 '), self.trace())

    def test_skin_higher_than_cpu_wins(self):
        log = self.run_loop([(48.5, {'cpu-1-3': 76000})])
        self.assertIn('skin 48.5 C, cpu 76.0 C (cpu-1-3), level 0 to 3: 50%', log[0])

    def test_no_skin_but_cpu_still_caps(self):
        log = self.run_loop([(None, {'cpuss-2': 83000})] * 3)
        self.assertIn('cpu 83.0 C (cpuss-2), level 0 to 1: 80%', log[1])
        self.assertNotIn('skin', log[1])
        self.assertIn('policy0=1612800/2016000', log[1])

    def test_exynos_zone_names_count(self):
        log = self.run_loop([(40.0, {'BIG': 82500})] * 3)
        self.assertIn('cpu 82.5 C (BIG), level 0 to 1', log[1])

    def test_knob_off_ignores_the_cpu(self):
        log = self.run_loop([(44.6, {'cpu-1-3': 95000})] * 4, cpu='0')
        self.assertEqual('skin 44.6 C: no cap', log[0].split(' ', 1)[1])
        self.assertEqual('2016000', self.freq())
        header = (self.stage / 'heat_cap.log').read_text()
        self.assertIn('ONEOS_HEAT_CAP_CPU=0', header)
        self.assertEqual([], self.run_loop([(None, {'cpu-1-3': 95000})] * 4, cpu='0'))

    def test_other_zones_are_ignored(self):
        log = self.run_loop([(40.0, {'gpuss-0': 95000, 'ddr': 90000, 'battery': 99000, 'skin-msm-therm': 97000,
                                     'mdmss-0': 96000, 'nspss-0': 94000, 'cpu-1-3': 60000})])
        self.assertEqual('skin 40.0 C, cpu 60.0 C (cpu-1-3): no cap', log[0].split(' ', 1)[1])
        self.assertEqual('2016000', self.freq())

    def test_bad_readings_are_ignored(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 'abc', 'cpuss-2': 200000, 'cpu-0-0': -5000, 'BIG': 70000})])
        self.assertEqual('skin 40.0 C, cpu 70.0 C (BIG): no cap', log[0].split(' ', 1)[1])

    def test_ap_is_logged_beside_skin_and_cpu(self):
        log = self.run_loop([(44.7, {'cpu-1-3': 60000}, 49.6)] + [(44.7, {'cpu-1-3': 104300}, 49.6)] * 4)
        self.assertEqual('skin 44.7 C, ap 49.6 C, cpu 60.0 C (cpu-1-3): no cap', log[0].split(' ', 1)[1])
        self.assertIn('skin 44.7 C, ap 49.6 C, cpu 104.3 C (cpu-1-3), level 0 to 1: 80%', log[1])
        self.assertIn('skin 44.7 C, ap 49.6 C, cpu 104.3 C (cpu-1-3), level 1 to 2: 65%', log[2])
        self.assertIn('stopped: full clock', log[3])

    def test_ap_in_the_held_line(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 70200}, 48.0), (40.0, {'cpu-1-3': 74800}, 49.1),
                             (40.0, {'cpu-1-3': 104300}, 51.2)], held=True)
        rest = [l.split(' ', 1)[1] for l in log]
        self.assertEqual('cpu 104.3 C (cpu-1-3), ap 51.2 C held: needs four readings over 90 C (before: 74.8, 70.2, 0.0)',
                         rest[1])

    def test_ap_leaving_a_level(self):
        log = self.run_loop([(40.0, {'cpu-1-3': 86000}, 52.0)] * 3 + [(40.0, {'cpu-1-3': 60000}, 48.5)] * 3)
        self.assertIn('skin 40.0 C, ap 48.5 C, cpu 60.0 C (cpu-1-3), level 1 to 0: full clock', log[2])

    def test_ap_with_no_skin(self):
        log = self.run_loop([(None, {'cpuss-2': 83000}, 50.3)] * 3)
        self.assertIn('ap 50.3 C, cpu 83.0 C (cpuss-2), level 0 to 1: 80%', log[1])
        self.assertNotIn('skin', log[1])

    def test_no_ap_reading_leaves_it_out(self):
        log = self.run_loop([(44.7, {'cpu-1-3': 60000})] + [(44.7, {'cpu-1-3': 104300})] * 3, held=True)
        self.assertTrue(log)
        for l in log:
            self.assertNotRegex(l, r'\bap [0-9]')
        self.assertIn('skin 44.7 C, cpu 104.3 C (cpu-1-3), level 0 to 1: 80%', log[4])

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
                  (47.5, {'cpu-1-3': 91900}), (50.5, {'cpu-1-3': 92000}), (42.0, {'cpu-1-3': 77000}),
                  (40.0, {'cpu-1-3': 70000}), (40.0, {'cpu-1-3': 70000})]
        aps = [30.0, 99.0, 49.6, None, 120.5, 53.1, 48.0, 75.0]

        without = self.run_loop(rounds, held=True)
        trace_without = self.trace()
        self.fresh()
        with_ap = self.run_loop([r + (a,) for r, a in zip(rounds, aps)], held=True)
        self.assertEqual(len(rounds), len(trace_without))
        self.assertEqual(trace_without, self.trace())
        self.assertTrue(any(l.startswith('3 ') for l in trace_without), trace_without)
        self.assertTrue(trace_without[-1].startswith('0 '), trace_without)
        self.assertTrue(any(' held: ' in l for l in without), without)
        self.assertNotEqual(without, with_ap, 'the AP readings were logged')
        strip = lambda ls: [re.sub(r'ap [0-9.]+ C, |, ap [0-9.]+ C', '', l.split(' ', 1)[1]) for l in ls]
        self.assertEqual(strip(without), strip(with_ap))

    def test_the_header_names_the_cpu_thresholds(self):
        self.run_loop([(40.0, {})])
        header = (self.stage / 'heat_cap.log').read_text()
        self.assertIn('from 45.0, 46.5, 48.0 C skin every CPU policy and the GPU at 80, 65, 50% of their top clock (three readings running 2 C under to step down)', header)
        self.assertIn('82, 90 C on the hottest CPU thermal zone for levels 1 and 2 only (three and four readings running to go up, three 4 C under to step down)', header)

    def test_cam_limit_releases_at_level_1_and_2_after_camera_exits(self):
        # Level 1: held at 1958400 while camera runs, released once camera exits, no duplicate on tick 3.
        log = self.run_loop([(45.5, {}, None, True), (45.5, {}, None, False), (45.5, {}, None, False)],
                            cfl='1958400')
        self.assertEqual(['1958400 -', '-1 1958400', '-1 1958400'], self.cfl_trace())
        self.assertEqual('-1', self.cfl())
        self.assertEqual('1958400', (self.stage / 'cpufreq_max_limit.released').read_text().strip())
        gone = [l for l in log if 'camera gone' in l]
        self.assertEqual(1, len(gone), log)
        self.assertIn('camera gone: cpufreq_max_limit 1958400 -> -1', gone[0])
        self.assertIn('level 0 to 1: 80%', log[0])
        self.assertNotIn('cpufreq_max_limit=', log[-1])

        # Level 2: leftover cpufreq_max_limit is also released when camera is gone.
        self.fresh()
        log = self.run_loop([(46.5, {})], cfl='1958400', camera=False)
        self.assertEqual(['-1 1958400'], self.cfl_trace())
        self.assertEqual('-1', self.cfl())
        self.assertEqual('1958400', (self.stage / 'cpufreq_max_limit.released').read_text().strip())
        self.assertIn('camera gone: cpufreq_max_limit 1958400 -> -1', log[0])
        self.assertIn('level 0 to 2: 65%', log[1])
        self.assertNotIn('cpufreq_max_limit=', log[-1])

    def test_low_power_leaves_cam_limit_untouched(self):
        log = self.run_loop([(45.5, {})], cfl='1958400', low_power='1', camera=False)
        self.assertEqual(['1958400 -'], self.cfl_trace())
        self.assertEqual('1958400', self.cfl())
        self.assertFalse((self.stage / 'cpufreq_max_limit.released').exists())
        self.assertFalse(any('camera gone' in l for l in log), log)


if __name__ == '__main__':
    unittest.main()
