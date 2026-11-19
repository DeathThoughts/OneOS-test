#!/usr/bin/env python3
"""The watcher's one-off guest memory capture, 300 s after the home screen (oneos_switch.sh).

S25 Ultra, coloros17-findx9u-in-1105, sessions db475ad5017133c4 and a12bf19e1931e81a
(2026-11-18): a RAM base before a ColorOS debloat. Once the watcher sees the home screen it
starts, once per session, a background subshell that sleeps 300 s and then writes
$STAGE/mem_300.log: a header with the time and the seconds since the guest start (guest_mntns's
time), dumpsys meminfo (timeout 30) and pm list packages -d (timeout 20), each at nice 10 with
stdin from /dev/null, in the guest's namespace by the guest surfaceflinger's pid, 512 KiB at most.
It writes nothing when that guest is gone by then (no surfaceflinger, the return's
oneos_rec_stop, or guest_mntns gone or another session's: a later session's switch clears
oneos_rec_stop). The subshell is cut out of the watcher, unquoted the way
test-diag-round-light.py does it (its helpers), its sleep dropped and run with stubs.
"""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('diag_round_light', ROOT / 'scripts/test-diag-round-light.py')
light = importlib.util.module_from_spec(spec)
spec.loader.exec_module(light)
SOURCE = light.SWITCH.read_text()
COLLECT = (ROOT / 'app-android/app/src/full/assets/logs/collect.list').read_text()
GENV = ' -m /system/bin/sh /data/oneos_genv.sh /system/bin/'

ARM = '            if [ \\$home = 1 ] && [ -z \\"\\$memarm\\" ]; then\n                memarm=1\n'
START = '                ( mns=\\$(cat $STAGE/guest_mntns 2>/dev/null); sleep 300\n'
END = '                ) </dev/null >/dev/null 2>&1 &\n'


def probe():
    assert SOURCE.count(START) == 1
    a = SOURCE.index(START)
    b = SOURCE.index(END, a)
    return SOURCE[a:b] + '                )\n'


class MemProbe(unittest.TestCase):
    def run_probe(self, sf='4321', mntns=True, stop=False, big=False, hook='true'):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        stage, stubs, calls = d / 'stage', d / 'bin', d / 'calls'
        (stage / 'data').mkdir(parents=True)
        stubs.mkdir()
        if mntns:
            (stage / 'guest_mntns').write_text('mnt:[4026532999]\n')
            os.utime(stage / 'guest_mntns', (os.path.getmtime(stage / 'guest_mntns') - 400,) * 2)
        if stop:
            (stage / 'data/oneos_rec_stop').write_text('')
        out = 'head -c 700000 /dev/zero | tr "\\\\0" m' if big else 'echo "Total RAM: 11,000K"'
        (stubs / 'bb').write_text('#!/bin/sh\necho "bb $* nice=$(nice) stdin=$(readlink /proc/$$/fd/0)" >> "$CALLS"\n'
                                  'case "$*" in *meminfo*) %s ;; *"list packages"*) echo package:com.x ;; esac\n' % out)
        (stubs / 'pidof').write_text('#!/bin/sh\n[ "$1" = surfaceflinger ] && [ -n "$SF" ] && echo "$SF"\nexit 0\n')
        for f in ('bb', 'pidof'):
            (stubs / f).chmod(0o755)
        text = light.unquote(probe(), str(stage), str(stubs / 'bb'), '1').replace('; sleep 300\n', '; eval "$HOOK"\n', 1)
        env = dict(os.environ, PATH='%s:%s' % (stubs, os.environ['PATH']), CALLS=str(calls), SF=sf,
                   HOOK=hook.replace('STAGE', str(stage)))
        r = subprocess.run(['sh', '-c', text], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(0, r.returncode, r.stderr)
        log = [l for l in calls.read_text().split('\n') if l] if calls.exists() else []
        return stage / 'mem_300.log', log

    def test_capture(self):
        f, log = self.run_probe()
        self.assertEqual(['bb nsenter -t 4321%sdumpsys meminfo nice=10 stdin=/dev/null' % GENV,
                          'bb nsenter -t 4321%spm list packages -d nice=10 stdin=/dev/null' % GENV], log)
        lines = f.read_text().split('\n')
        self.assertRegex(lines[0], r'^# .+ guest memory, 4\d\d s after the guest start$')
        self.assertEqual(['Total RAM: 11,000K', '== pm list packages -d', 'package:com.x', ''], lines[1:])

    def test_capped_at_512_kib(self):
        f, _ = self.run_probe(big=True)
        self.assertEqual(524288, f.stat().st_size)

    def test_skipped_when_the_guest_is_gone(self):
        for kw in ({'sf': ''}, {'mntns': False}, {'stop': True}, {'hook': 'rm STAGE/guest_mntns'},
                   {'hook': 'echo mnt:[4026533111] > STAGE/guest_mntns'}):
            with self.subTest(**kw):
                f, log = self.run_probe(**kw)
                self.assertEqual([], log)
                self.assertFalse(f.exists())

    def test_source_form(self):
        self.assertEqual(1, SOURCE.count(ARM))
        self.assertEqual(1, SOURCE.count('mem_300.log'.join(['> $STAGE/', '\n'])))
        a = SOURCE.index(ARM)
        self.assertEqual(SOURCE.index(START), a + len(ARM))
        p = probe()
        self.assertIn('nice -n 10 timeout 30 $BB nsenter -t \\$SFP' + GENV + 'dumpsys meminfo </dev/null 2>&1\n', p)
        self.assertIn('nice -n 10 timeout 20 $BB nsenter -t \\$SFP' + GENV + 'pm list packages -d </dev/null 2>&1\n', p)
        self.assertIn('} | head -c 524288 > $STAGE/mem_300.log\n', p)
        # In the background, inside the watcher's loop, after the home check it is gated on.
        self.assertLess(SOURCE.index('home=0; [ \\"\\$(grep -c \'Home activated\''), a)
        self.assertIn(END, SOURCE[a:a + 2000])
        self.assertIn('rm -f "$STAGE/mem_300.log"', SOURCE)
        self.assertIn('for f in "$S"/*.log; do', COLLECT)


if __name__ == '__main__':
    unittest.main()
