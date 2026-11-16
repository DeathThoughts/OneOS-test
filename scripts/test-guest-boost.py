#!/usr/bin/env python3
"""The guest boost (oneos_switch.sh): touch and screen-on CPU boost from the host.

S23 SM-S911B, ColorOS 17 (session cba68d07cbd3acef, 2026-11-15): ColorOS's ORMS
needs OPPO's perf HAL ("cannot connect to orms core service!"), so the guest
had no screen-on, touch, scroll or launch boost, while One UI asked Samsung's
HyPer HAL for TATouchBoost on every touch. These tests render the loop's
double-quoted `sh -c` string the way the switch's shell does, point /sys and
/proc at a fake tree, stub getevent and sleep, and run the real loop: each
`sleep` of the main loop is one second (one round); the touch reader's sleeps
return at once and its getevent waits for a touch a round asks for.
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()
RETURN = (ROOT / 'switcher/assets/return_to_oneui.sh').read_text()

START = '    $BB sh -c "\n    : oneos_guest_boost\n'
END = '\n" </dev/null >/dev/null 2>&1 &\n    echo "Guest boost: on'
OUTER_START = 'rm -f "$STAGE/boost.active"\nif [ "${ONEOS_GUEST_BOOST:-1}" = 1 ]; then\n'
OUTER_END = '    echo "Guest boost: off (ONEOS_GUEST_BOOST=0)"\nfi\n'
RET_START = 'pkill -9 -f "oneos_guest_boost" 2>/dev/null || true\n'
RET_END = '    echo "Guest boost: stopped, values put back:${gb:- none saved}"\nfi\n'

# policy: (cpus, top, available frequencies). 50% of 2016000 is 1008000: 960000
# is nearest; 70% is 1411200: 1401600.
POLICIES = {
    'policy0': ('0 1 2', 2016000, '300000 576000 768000 960000 1075200 1401600 1785600 2016000'),
    'policy3': ('3 4 5 6', 2803200, '499200 902400 1420800 1612800 1996800 2803200'),
    'policy7': ('7', 3360000, '595200 1612800 1708800 2457600 3360000'),
}
MIN0 = {'policy0': 300000, 'policy3': 499200, 'policy7': 595200}
T50 = {'policy0': 960000, 'policy3': 1420800, 'policy7': 1708800}
T70 = {'policy0': 1401600, 'policy3': 1996800, 'policy7': 2457600}

DEVICES = '''I: Bus=0019 Vendor=0001 Product=0001 Version=0100
N: Name="gpio_keys"
H: Handlers=kbd event0
B: KEY=8000 1000000000000 0

I: Bus=0000 Vendor=0000 Product=0000 Version=0000
N: Name="sec_touchpad"
H: Handlers=event5
B: ABS=3

I: Bus=0000 Vendor=0000 Product=0000 Version=0000
N: Name="sec_touchscreen"
H: Handlers=event3
B: KEY=400 0 0 0 0
B: ABS=2e0800000000003

'''


def fake_root(text, root):
    """/sys/... and /proc/... (not /proc/sys/'s /sys/) under root."""
    return re.sub(r'(?<![\w.])/(sys|proc)/', str(root).replace('\\', '\\\\') + r'/\1/', text)


def block(text, start, end, keep_start=False):
    a = text.index(start) + (0 if keep_start else len(start))
    b = text.index(end, a)
    return text[a:b]


class GuestBoostTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oneos-boost-')
        self.r = Path(self.temp.name)
        self.stage = self.r / 'stage'
        (self.stage / 'data').mkdir(parents=True)
        (self.stage / 'guest_mntns').write_text('1\n')
        for name, (cpus, top, av) in POLICIES.items():
            d = self.cpufreq(name)
            d.mkdir(parents=True)
            (d / 'cpuinfo_max_freq').write_text(f'{top}\n')
            (d / 'scaling_max_freq').write_text(f'{top}\n')
            (d / 'scaling_min_freq').write_text(f'{MIN0[name]}\n')
            (d / 'scaling_available_frequencies').write_text(av + ' \n')
            (d / 'related_cpus').write_text(cpus + '\n')
        bl = self.r / 'sys/class/backlight/panel0-backlight'
        bl.mkdir(parents=True)
        (bl / 'brightness').write_text('200\n')
        (self.r / 'proc/bus/input').mkdir(parents=True)
        (self.r / 'proc/bus/input/devices').write_text(DEVICES)
        (self.r / 'proc/sys/kernel/random').mkdir(parents=True)
        (self.r / 'proc/sys/kernel/random/boot_id').write_text('boot-1\n')

    def tearDown(self):
        self.temp.cleanup()

    def cpufreq(self, name):
        return self.r / 'sys/devices/system/cpu/cpufreq' / name

    def walt(self):
        d = self.r / 'proc/sys/walt/input_boost'
        d.mkdir(parents=True)
        (d / 'input_boost_freq').write_text('0\t0\t0\t0\t0\t0\t0\t0\n')
        (d / 'input_boost_ms').write_text('40\n')
        return d

    def render(self, heat_cap='1'):
        render = subprocess.run(
            ['sh', '-c', 'printf "%s" "' + block(SWITCH, START, END) + '"'],
            env={**os.environ, 'BB': 'busybox', 'STAGE': str(self.stage), 'HEAT_CAP': heat_cap},
            capture_output=True, text=True)
        self.assertEqual(0, render.returncode, render.stderr)
        return render.stdout

    def run_loop(self, rounds, heat_cap='1'):
        """rounds: shell text per main-loop second; `touch_now` is a touch."""
        for n, text in enumerate(rounds, 1):
            (self.r / f'round{n}').write_text(text + '\n')
        loop = fake_root(self.render(heat_cap), self.r)
        r = self.r
        snap = ' '.join(f'$(cat {self.cpufreq(p)}/scaling_min_freq)' for p in POLICIES)
        stubs = f'''
MAIN=$$
getevent() {{
    : > {r}/ready
    while [ ! -f {r}/touch ]; do
        [ -f {self.stage}/data/oneos_rec_stop ] && return 1
        command sleep 0.01
    done
    rm -f {r}/ready
    rm -f {r}/touch
    echo "$*" >> {r}/getevent_args
}}
touch_now() {{
    while [ ! -f {r}/ready ]; do command sleep 0.01; done
    : > {r}/touch
    while [ -f {r}/touch ] || [ ! -f {r}/ready ]; do command sleep 0.01; done
    echo "T {snap}" >> {r}/snap
}}
ROUND=0
sleep() {{
    [ "$(exec sh -c 'echo $PPID')" = "$MAIN" ] || return 0
    echo "{snap}" >> {r}/snap
    ROUND=$((ROUND + 1))
    if [ -f {r}/round$ROUND ]; then . {r}/round$ROUND; else : > {self.stage}/data/oneos_rec_stop; fi
}}
'''
        done = subprocess.run(['sh', '-c', stubs + loop], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        snaps = (self.r / 'snap').read_text().splitlines() if (self.r / 'snap').exists() else []
        return snaps, (self.stage / 'boost.log').read_text()

    @staticmethod
    def line(t):
        return ' '.join(str(t[p]) for p in POLICIES)

    def mins(self):
        return ' '.join(self.cpufreq(p).joinpath('scaling_min_freq').read_text().strip() for p in POLICIES)

    def test_the_rendered_loop_is_valid_shell(self):
        for hc in ('0', '1'):
            check = subprocess.run(['sh', '-n', '-c', self.render(hc)], capture_output=True, text=True)
            self.assertEqual(0, check.returncode, check.stderr)

    def test_walt_nodes_set_and_restored(self):
        d = self.walt()
        snaps, log = self.run_loop(['', ''])
        self.assertIn('mechanism: the kernel\'s input boost', log)
        # policy7, the prime cluster, gets 0; the other CPUs their 50% step.
        self.assertIn('input_boost_freq 0 0 0 0 0 0 0 0 -> 960000 960000 960000 1420800 1420800 1420800 1420800 0, '
                      'input_boost_ms 40 -> 120', log)
        self.assertFalse((self.r / 'getevent_args').exists(), 'no touch reader with the kernel boost')
        self.assertEqual('0 0 0 0 0 0 0 0', ' '.join((d / 'input_boost_freq').read_text().split()))
        self.assertEqual('40', (d / 'input_boost_ms').read_text().strip())
        self.assertEqual(self.line(MIN0), self.mins())
        self.assertFalse((self.stage / 'boost.before').exists())
        self.assertIn('stopped (the return started): every value put back', log)

    def test_walt_held_off_while_hot(self):
        d = self.walt()
        lv = self.stage / 'heat_cap.level'
        lv.write_text('0\n')
        seen = self.r / 'seen'
        rounds = [f'echo 2 > {lv}',
                  f'cat {d}/input_boost_freq >> {seen}; echo 0 > {lv}',
                  f'cat {d}/input_boost_freq >> {seen}']
        _, log = self.run_loop(rounds)
        got = [' '.join(l.split()) for l in seen.read_text().splitlines()]
        self.assertEqual(['0 0 0 0 0 0 0 0', '960000 960000 960000 1420800 1420800 1420800 1420800 0'], got)
        self.assertIn("the heat cap holds a level: the kernel's input boost off", log)
        self.assertIn('held off 1 s for heat', log)

    def test_touch_raises_to_the_50_step_and_restores(self):
        snaps, log = self.run_loop(['touch_now', '', ''])
        self.assertIn('mechanism: the touch reader on /dev/input/event3 sec_touchscreen', log)
        self.assertEqual(self.line(MIN0), snaps[0])
        self.assertEqual('T ' + self.line(T50), snaps[1], 'raised at once')
        self.assertEqual(self.line(T50), snaps[2], 'held for the second after the touch')
        self.assertEqual(self.line(MIN0), snaps[3], 'back after a second with no touch')
        self.assertEqual('-c 1 -q /dev/input/event3', (self.r / 'getevent_args').read_text().split('\n')[0])
        self.assertIn('touch boosts 1, touches skipped for heat 0', log)

    def test_continuous_touching_keeps_it_raised(self):
        snaps, log = self.run_loop(['touch_now', 'touch_now', 'touch_now', '', ''])
        main = [s for s in snaps if not s.startswith('T ')]
        self.assertEqual([self.line(MIN0), self.line(T50), self.line(T50), self.line(T50), self.line(MIN0)], main[:5])
        self.assertIn('touch boosts 1,', log, 'one boost, held')

    def test_never_above_scaling_max_freq(self):
        (self.cpufreq('policy3') / 'scaling_max_freq').write_text('1000000\n')
        snaps, _ = self.run_loop(['touch_now', ''])
        self.assertEqual('T 960000 1000000 1708800', snaps[1])
        self.assertEqual('1000000', (self.cpufreq('policy3') / 'scaling_max_freq').read_text().strip())
        self.assertEqual(self.line(MIN0), self.mins())

    def test_scaling_max_freq_is_never_written(self):
        self.assertNotIn('scaling_max_freq 2', block(SWITCH, START, END))
        self.assertNotIn('> \\$1/scaling_max_freq', block(SWITCH, START, END))
        self.assertNotIn('/scaling_max_freq 2>/dev/null\n', block(SWITCH, START, END).replace('< \\$1/scaling_max_freq', ''))

    def test_heat_level_over_0_no_boost(self):
        (self.stage / 'heat_cap.level').write_text('1\n')
        snaps, log = self.run_loop(['touch_now', ''])
        self.assertEqual('T ' + self.line(MIN0), snaps[1])
        self.assertIn('touch boosts 0, touches skipped for heat 1', log)

    def test_heat_cap_off_still_boosts(self):
        (self.stage / 'heat_cap.level').write_text('3\n')
        snaps, _ = self.run_loop(['touch_now', ''], heat_cap='0')
        self.assertEqual('T ' + self.line(T50), snaps[1])

    def test_heat_coming_on_ends_a_boost(self):
        lv = self.stage / 'heat_cap.level'
        lv.write_text('0\n')
        snaps, _ = self.run_loop([f'touch_now; echo 2 > {lv}', ''])
        self.assertEqual('T ' + self.line(T50), snaps[1])
        self.assertEqual(self.line(MIN0), snaps[2])

    def test_screen_on_70_for_2_s(self):
        bl = self.r / 'sys/class/backlight/panel0-backlight/brightness'
        bl.write_text('0\n')
        snaps, log = self.run_loop([f'echo 150 > {bl}', '', '', ''])
        self.assertEqual([self.line(MIN0), self.line(T70), self.line(T70), self.line(MIN0)], snaps[:4])
        self.assertIn('screen-on boosts 1, skipped for heat 0', log)

    def test_screen_on_while_hot_skipped(self):
        bl = self.r / 'sys/class/backlight/panel0-backlight/brightness'
        bl.write_text('0\n')
        (self.stage / 'heat_cap.level').write_text('2\n')
        snaps, log = self.run_loop([f'echo 150 > {bl}', ''])
        self.assertEqual(self.line(MIN0), snaps[1])
        self.assertIn('screen-on boosts 0, skipped for heat 1', log)

    def test_no_touchscreen_screen_on_only(self):
        (self.r / 'proc/bus/input/devices').write_text(DEVICES.split('\n\n')[0] + '\n\n')
        _, log = self.run_loop([''])
        self.assertIn('the screen-on boost only', log)
        self.assertFalse((self.r / 'getevent_args').exists())

    def test_namespace_gone_stops_and_restores(self):
        snaps, log = self.run_loop(['touch_now', f'rm {self.stage}/guest_mntns', 'echo never'])
        self.assertIn('stopped (no guest namespace record)', log)
        self.assertEqual(self.line(MIN0), self.mins())
        self.assertFalse((self.stage / 'boost.active').exists())

    def test_heat_cap_writes_its_level(self):
        self.assertIn('[ \\"\\$new\\" = \\"\\$lvl\\" ] || echo \\$new > $STAGE/heat_cap.level', SWITCH)
        self.assertIn('[ $HEAT_CAP = 1 ] && echo 0 > $STAGE/heat_cap.level', SWITCH)


class GuestBoostKnobTest(unittest.TestCase):
    def outer(self, knob):
        with tempfile.TemporaryDirectory(prefix='oneos-boost-knob-') as t:
            env = {**os.environ, 'STAGE': t}
            env.pop('ONEOS_GUEST_BOOST', None)
            if knob is not None:
                env['ONEOS_GUEST_BOOST'] = knob
            script = f'bbstub() {{ echo "$1 $2" >> {t}/started; }}\nBB=bbstub\n' + block(SWITCH, OUTER_START, OUTER_END, True) + OUTER_END + 'wait\n'
            done = subprocess.run(['sh', '-c', script], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, done.returncode, done.stderr)
            return done.stdout, os.path.exists(f'{t}/started'), os.path.exists(f'{t}/boost.active')

    def test_knob_0_starts_nothing(self):
        out, started, active = self.outer('0')
        self.assertFalse(started)
        self.assertFalse(active)
        self.assertIn('Guest boost: off (ONEOS_GUEST_BOOST=0)', out)

    def test_unset_is_on(self):
        out, started, active = self.outer(None)
        self.assertTrue(started)
        self.assertTrue(active)
        self.assertIn('Guest boost: on', out)

    def test_knob_validated(self):
        line = next(l for l in SWITCH.splitlines() if l.startswith('case "${ONEOS_GUEST_BOOST:-1}"'))
        for v, ok in (('0', True), ('1', True), ('2', False), ('yes', False)):
            done = subprocess.run(['sh', '-c', 'refuse() { exit 7; }\n' + line],
                                  env={**os.environ, 'ONEOS_GUEST_BOOST': v}, capture_output=True, text=True)
            self.assertEqual(0 if ok else 7, done.returncode, v)


class ReturnRestoresTest(unittest.TestCase):
    def run_return(self, before, boot='boot-1'):
        with tempfile.TemporaryDirectory(prefix='oneos-boost-ret-') as t:
            r = Path(t)
            s = r / 'stage'
            s.mkdir()
            pol = r / 'sys/devices/system/cpu/cpufreq/policy0'
            pol.mkdir(parents=True)
            (pol / 'scaling_min_freq').write_text('960000\n')
            w = r / 'proc/sys/walt/input_boost'
            w.mkdir(parents=True)
            (w / 'input_boost_freq').write_text('960000 0\n')
            (w / 'input_boost_ms').write_text('120\n')
            (r / 'proc/sys/kernel/random').mkdir(parents=True)
            (r / 'proc/sys/kernel/random/boot_id').write_text(boot + '\n')
            evil = r / 'evil'
            evil.write_text('keep\n')
            (s / 'boost.active').write_text('')
            (s / 'boost.gpid').write_text('999999\n')
            (s / 'boost.before').write_text(before.format(r=r))
            text = (RET_START + block(RETURN, RET_START, RET_END) + RET_END)
            text = fake_root(text, r)
            done = subprocess.run(['sh', '-c', 'pkill() { :; }\nS=' + str(s) + '\n' + text],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(0, done.returncode, done.stderr)
            return (done.stdout, (pol / 'scaling_min_freq').read_text().strip(),
                    (w / 'input_boost_freq').read_text().strip(), (w / 'input_boost_ms').read_text().strip(),
                    evil.read_text().strip(), sorted(p.name for p in s.iterdir()))

    BEFORE = ('boot boot-1\n'
              'node {r}/sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq 300000\n'
              'node {r}/proc/sys/walt/input_boost/input_boost_freq 0 0\n'
              'node {r}/proc/sys/walt/input_boost/input_boost_ms 40\n'
              'node {r}/evil 1\n')

    def test_return_puts_every_value_back(self):
        out, mn, wf, wm, evil, left = self.run_return(self.BEFORE)
        self.assertEqual(('300000', '0 0', '40', 'keep'), (mn, wf, wm, evil))
        self.assertEqual([], left)
        self.assertIn('Guest boost: stopped, values put back: policy0=300000 input_boost_freq=0 0 input_boost_ms=40', out)

    def test_another_boots_file_is_not_applied(self):
        out, mn, wf, wm, evil, left = self.run_return(self.BEFORE, boot='boot-2')
        self.assertEqual(('960000', '960000 0', '120'), (mn, wf, wm))
        self.assertEqual([], left)
        self.assertIn('none saved', out)


if __name__ == '__main__':
    unittest.main()
