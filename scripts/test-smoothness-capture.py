#!/usr/bin/env python3
"""Smoothness and heat numbers for a before/after comparison (oneos_switch.sh).

S25 Ultra, coloros17-findx9u-in-1105, sessions db475ad5017133c4 and a12bf19e1931e81a (2026-11-18):
ColorOS's launcher draws through UniRenderSurface, so `dumpsys gfxinfo com.android.launcher` in
frames.txt counted 0 frames and nothing measured smoothness. Three additions, each tested here:

a) the 90 s diagnostics round (only that one) turns SurfaceFlinger's timestats on and clears them,
   resets gfxinfo for SystemUI, Settings and the launcher, and writes the reclaim lines of
   /proc/vmstat and the PSI lines into $D/smoothness.txt as a base; its dexopt.txt also lists
   Samsung Camera (run-from-apk in both sessions);
b) a return asked for (app, keys, a guest request) first writes $STAGE/diag_end/smoothness.txt:
   the timestats dump (20 layers) and their disable, the panel rate / missed frames / GPU memory
   lines of dumpsys SurfaceFlinger, the gfxinfo summaries and the same vmstat and PSI lines, all
   in one `timeout 3`, so the return waits 3 s at most; nothing without a surfaceflinger;
c) the heat cap loop writes one line every third 10 s tick to $STAGE/heat_series.log from the
   values that tick already read (heat_cap.log keeps its own lines, test-heat-cap-cpu.py).

The round is run as test-diag-round-light.py runs it (helpers loaded from there), the return's
capture is cut out of the watcher, unquoted and run with stubs, and the heat loop is run as
test-heat-cap-cpu.py runs it. The app's log collection takes $S/diag_*/*.txt and $S/*.log.
"""
from pathlib import Path
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


light = load('diag_round_light', 'test-diag-round-light.py')
light.SOURCE = light.SWITCH.read_text()
SOURCE = light.SOURCE
heat = load('heat_cap_cpu', 'test-heat-cap-cpu.py')
COLLECT = (ROOT / 'app-android/app/src/full/assets/logs/collect.list').read_text()
GENV = ' -m /system/bin/sh /data/oneos_genv.sh /system/bin/'
PKGS = ('com.android.systemui', 'com.android.settings', 'com.android.launcher')

END_START = ("                SFP=\\$(pidof surfaceflinger | awk '{print \\$1}')\n"
             "                if [ -n \\\"\\$SFP\\\" ]; then\n"
             "                    mkdir -p $STAGE/diag_end; s0=\\$(date +%s)\n")
END_STOP = '                fi\n                exec /system/bin/sh $STAGE/return_to_oneui.sh\n'


class BaseAt90(light.Round):
    def calls(self, stage):
        return (stage.parent / 'calls').read_text().split('\n')

    def test_the_90_s_round_sets_the_base(self):
        _, diag, stage, (_, _, sf) = self.run_round('0', '1', dname='90')
        calls = self.calls(stage)
        ts = 'bb nsenter -t %s%sdumpsys SurfaceFlinger --timestats -clear -enable' % (sf, GENV)
        self.assertEqual(1, calls.count(ts))
        for p in PKGS:
            self.assertEqual(1, calls.count('bb nsenter -t %s%sdumpsys gfxinfo %s reset' % (sf, GENV, p)), p)
        text = (diag / 'smoothness.txt').read_text()
        self.assertRegex(text.split('\n', 1)[0], r'^\d\d:\d\d:\d\d$')
        for k in ('pgscan_kswapd', 'pgsteal_kswapd', 'pswpin'):
            if re.search(r'^%s ' % k, Path('/proc/vmstat').read_text(), re.M):
                self.assertRegex(text, r'(?m)^%s \d+$' % k)
        for r in ('cpu', 'memory', 'io'):
            self.assertRegex(text, r'(?m)^%s: ' % r)

    def test_other_rounds_leave_the_counters_alone(self):
        for dname in ('40', 'final'):
            with self.subTest(dname=dname):
                _, diag, stage, _ = self.run_round('0', '1', dname=dname)
                calls = self.calls(stage)
                self.assertFalse([c for c in calls if '--timestats' in c or 'gfxinfo' in c and ' reset' in c])
                self.assertFalse((diag / 'smoothness.txt').exists())

    def test_dexopt_lists_samsung_camera(self):
        _, _, stage, (_, _, sf) = self.run_round('0', '1', dname='90')
        self.assertIn('bb nsenter -t %s%spm art dump com.sec.android.app.camera' % (sf, GENV), self.calls(stage))


# Only this file's tests: the inherited ones run in test-diag-round-light.py.
for _n in [n for n in dir(light.Round) if n.startswith('test_')]:
    if _n not in BaseAt90.__dict__:
        setattr(BaseAt90, _n, None)


def end_block():
    assert SOURCE.count(END_START) == 1
    a = SOURCE.index(END_START)
    b = SOURCE.index(END_STOP, a)
    return SOURCE[a:b] + '                fi\n'


class EndCapture(unittest.TestCase):
    def run_end(self, sf='4321', slow=False):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        stage, stubs, calls = d / 'stage', d / 'bin', d / 'calls'
        stage.mkdir()
        stubs.mkdir()
        bb = ('#!/bin/sh\necho "bb $*" >> "$CALLS"\n'
              'case "$*" in\n'
              '  *"--timestats -dump"*) echo "timestats: 120 frames" ;;\n'
              '  *"dumpsys SurfaceFlinger") %s; printf "  refresh 120.00 Hz: x\\nmissed frame count: 3\\n'
              'other\\nTotal imported by gralloc: 9MB\\nTotal GPU memory usage: 5MB\\n" ;;\n'
              '  *"gfxinfo"*) printf "Total frames rendered: 10\\nJanky frames: 1 (10.00%%%%)\\n50th percentile: 5ms\\n'
              'Profile data in ms:\\n" ;;\n'
              'esac\n') % ('sleep 10' if slow else ':')
        (stubs / 'bb').write_text(bb)
        (stubs / 'pidof').write_text('#!/bin/sh\n[ "$1" = surfaceflinger ] && [ -n "$SF" ] && echo "$SF"\nexit 0\n')
        for f in ('bb', 'pidof'):
            (stubs / f).chmod(0o755)
        text = light.unquote(end_block(), str(stage), str(stubs / 'bb'), '1')
        self.assertEqual(1, text.count('timeout 3 /system/bin/sh -c '))
        text = text.replace('timeout 3 /system/bin/sh -c ', 'timeout 3 sh -c ')
        env = dict(os.environ, PATH='%s:%s' % (stubs, os.environ['PATH']), CALLS=str(calls), SF=sf)
        t0 = time.monotonic()
        r = subprocess.run(['sh', '-c', text], capture_output=True, text=True, timeout=30, env=env)
        took = time.monotonic() - t0
        self.assertEqual(0, r.returncode, r.stderr)
        log = calls.read_text().split('\n') if calls.exists() else []
        return stage, log, r.stdout, took

    def test_capture_order_and_content(self):
        stage, log, out, _ = self.run_end()
        want = ['bb nsenter -t 4321%sdumpsys SurfaceFlinger --timestats -dump -maxlayers 20' % GENV,
                'bb nsenter -t 4321%sdumpsys SurfaceFlinger --timestats -disable' % GENV,
                'bb nsenter -t 4321%sdumpsys SurfaceFlinger' % GENV] + \
               ['bb nsenter -t 4321%sdumpsys gfxinfo %s' % (GENV, p) for p in PKGS]
        self.assertEqual(want, [l for l in log if l])
        text = (stage / 'diag_end/smoothness.txt').read_text()
        self.assertIn('timestats: 120 frames', text)
        self.assertIn('  refresh 120.00 Hz: x\nmissed frame count: 3\nTotal imported by gralloc: 9MB\n'
                      'Total GPU memory usage: 5MB\n', text)
        self.assertNotIn('other', text)
        for p in PKGS:
            self.assertIn('== %s\nTotal frames rendered: 10\nJanky frames: 1 (10.00%%)\n50th percentile: 5ms\n' % p, text)
        self.assertNotIn('Profile data', text)
        for r in ('cpu', 'memory', 'io'):
            self.assertRegex(text, r'(?m)^%s: ' % r)
        self.assertRegex(out, r'smoothness at the end: \d+ s \(%s/diag_end/smoothness.txt\)' % re.escape(str(stage)))

    def test_bounded_by_3_s(self):
        stage, _, out, took = self.run_end(slow=True)
        self.assertLess(took, 6)
        self.assertIn('timestats: 120 frames', (stage / 'diag_end/smoothness.txt').read_text())
        self.assertIn('smoothness at the end:', out)

    def test_no_surfaceflinger_no_capture(self):
        stage, log, out, _ = self.run_end(sf='')
        self.assertEqual([], [l for l in log if l])
        self.assertFalse((stage / 'diag_end').exists())
        self.assertEqual('', out)

    def test_source_place(self):
        # In the request branch, after the thermal/battery lines, right before its exec.
        a = SOURCE.index('echo \\"\\$(date) guest requested \'\\$req\' after \\${t}s, returning to One UI\\"')
        b = SOURCE.index(END_START)
        c = SOURCE.index(END_STOP, b)
        self.assertLess(a, b)
        self.assertNotIn('exec /system/bin/sh $STAGE/return_to_oneui.sh', SOURCE[a:b])
        self.assertEqual(1, end_block().count('timeout '))
        self.assertIn('db475ad5017133c4', SOURCE[b - 600:b])
        # Cleared at the switch with the other captures, collected by the diag glob.
        self.assertIn('"$STAGE/diag_return" \\\n       "$STAGE/diag_end"\n', SOURCE)
        self.assertIn('for f in "$S"/diag_*/*; do', COLLECT)


class HeatSeries(heat.HeatCapCpuTest):
    def series(self, rounds, cfl='2246400'):
        d = self.r / 'sys/devices/system/cpu/cpufreq_limit'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'cpufreq_max_limit').write_text(cfl + '\n')
        # camera=True: Samsung Camera is running, so cam_limit leaves the cfl alone.
        log = self.run_loop(rounds, cfl=cfl, camera=True)
        lines = (self.stage / 'heat_series.log').read_text().splitlines()
        return log, lines

    def test_every_third_tick(self):
        hot = (46.0, {'cpu-1-3': 83000}, 44.5)
        log, lines = self.series([hot] * 7)
        self.assertTrue(lines[0].startswith('# '))
        body = [l.split(' ', 1)[1] for l in lines[1:]]
        self.assertEqual(['skin=46.0 ap=44.5 cpu=83.0(cpu-1-3) lvl=1 s=1 c=1 cfl=2246400'] * 2, body)
        for l in lines[1:]:
            self.assertRegex(l, r'^\d\d:\d\d:\d\d ')
        # heat_cap.log keeps its own lines.
        self.assertFalse([l for l in log if 'skin=' in l])

    def test_missing_readings(self):
        _, lines = self.series([(None, {}, None)] * 3, cfl='-1')
        self.assertEqual(['skin=- ap=- cpu=40.0(cpu-0-0) lvl=0 s=0 c=0 cfl=-1'], [l.split(' ', 1)[1] for l in lines[1:]])

    def test_source_form(self):
        block = heat.heat_cap_block()
        self.assertEqual(1, block.count('>> \\$HS'))
        self.assertNotIn('dumpsys', block.split('hst=\\$((hst + 1))', 1)[1].split('sleep 10', 1)[0])
        self.assertIn('rm -f "$STAGE/mem_300.log" "$STAGE/heat_series.log"', SOURCE)
        self.assertIn('for f in "$S"/*.log; do', COLLECT)


for _n in [n for n in dir(heat.HeatCapCpuTest) if n.startswith('test_')]:
    if _n not in HeatSeries.__dict__:
        setattr(HeatSeries, _n, None)


if __name__ == '__main__':
    unittest.main()
