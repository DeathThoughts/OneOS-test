#!/usr/bin/env python3
"""The audio adapter's vendor extension is remembered as absent per One UI build (oneos_switch.sh).

S25 Ultra sessions db19aee410bd79ff and 75e538ac0ed725df: android.media.audio.IHalAdapterVendorExtension
never appears with One UI's audioserver stopped, and every switch paid about 5.5 s of checks in step
[6/6] before setting ro.audio.ihaladaptervendorextension_enabled=false for the session.

The AVX block is cut out of the script (from "AVX=ro.audio..." to the else of the guest-audio
branch) and run with getprop, service, resetprop and sleep stubbed (each logs its call). Checked:
- no memory: polls 10 times with sleeps, overrides, writes $STAGE/oneos_avx_absent (the fingerprint of
  inplace_base/props, mode 600, no .tmp left);
- memory with the same fingerprint: one check, no sleep, override, " (remembered for this firmware)";
- memory and the extension found: no override, the memory file removed;
- another fingerprint (One UI updated): polls again and rewrites the memory;
- no baseline file, no fingerprint, or a symlink in the memory's place: today's behaviour and nothing
  written (nothing followed);
- the extension found while polling: no override, no memory.
"""
from pathlib import Path
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SWITCH = ROOT / 'switcher/assets/oneos_switch.sh'
START = '    AVX=ro.audio.ihaladaptervendorextension_enabled\n'
END = 'else\n    rm -f "$STAGE/data/guest_audio.flag" "$STAGE/host_audio_hal_svcs"\n'
FP = 'samsung/e3qxeea/e3q:16/BP2A.250605.031.A3/S938BXXU1AYA1:user/release-keys'
OVERRIDE = "Audio: ro.audio.ihaladaptervendorextension_enabled=false for the session (no IHalAdapterVendorExtension with One UI's audioserver stopped)"
REMEMBERED_LINE = 'Audio: no IHalAdapterVendorExtension after the wait; remembered for this firmware'


def block(src):
    assert src.count(START) == 1 and src.count(END) == 1
    a = src.index(START)
    b = src.index(END)
    assert a < b
    return src[a:b]


def shells():
    out = []
    for p in [shutil.which('busybox')] + sorted(glob.glob('/nix/store/*busybox*/bin/busybox')):
        if p and os.access(p, os.X_OK):
            out.append(('busybox sh', [p, 'sh']))
            break
    else:
        print('busybox not found: busybox sh is not tested', file=sys.stderr)
    for name in ('dash', 'mksh', 'bash'):
        p = shutil.which(name)
        if p:
            out.append((name, [p]))
    return out


class Avx(unittest.TestCase):
    def run_block(self, shell, fp=FP, memory=None, found=False, symlink_memory=False):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        stage, stubs, calls = Path(d, 'stage'), Path(d, 'bin'), Path(d, 'calls')
        (stage / 'inplace_base').mkdir(parents=True)
        stubs.mkdir()
        if fp is not None:
            (stage / 'inplace_base/props').write_text('[ro.build.id]: [X]\n[ro.build.fingerprint]: [%s]\n[ro.other]: [1]\n' % fp)
        mem = stage / 'oneos_avx_absent'
        if memory is not None:
            mem.write_text(memory + '\n')
        if symlink_memory:
            Path(d, 'victim').write_text('keep\n')
            mem.symlink_to(Path(d, 'victim'))
        stub = {
            'getprop': 'echo true',
            'service': 'echo "Service $2: %s"' % ('found' if found else 'not found'),
            'resetprop': '',
        }
        for name, body in stub.items():
            f = stubs / name
            f.write_text('#!/bin/sh\necho "%s $*" >> "%s"\n%s\n' % (name, calls, body))
            f.chmod(0o755)
        # Functions beat applets (busybox runs sleep and timeout in-process, not from PATH).
        pre = 'sleep() { echo "sleep $*" >> %s; }\ntimeout() { shift; "$@"; }\n' % calls
        script = 'STAGE=%s; RP=%s/resetprop\n%s%s' % (stage, stubs, pre, block(SOURCE))
        env = dict(os.environ, PATH='%s:%s' % (stubs, os.environ['PATH']))
        r = subprocess.run(shell + ['-c', script], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(0, r.returncode, r.stderr)
        log = calls.read_text().splitlines() if calls.exists() else []
        over = stage / 'guest_props.overridden'
        return {
            'out': r.stdout.splitlines(),
            'checks': len([l for l in log if l.startswith('service ')]),
            'sleeps': len([l for l in log if l.startswith('sleep ')]),
            'resetprop': [l for l in log if l.startswith('resetprop ')],
            'overridden': over.read_text() if over.exists() else None,
            'memory': mem.read_text() if mem.exists() and not mem.is_symlink() else None,
            'mem_is_link': mem.is_symlink(),
            'victim': Path(d, 'victim').read_text() if symlink_memory else None,
            'tmp_left': (stage / 'oneos_avx_absent.tmp').exists(),
            'mode': oct(mem.stat().st_mode & 0o777) if mem.exists() and not mem.is_symlink() else None,
        }, stage

    def each(self, **kw):
        for name, shell in shells():
            with self.subTest(shell=name):
                yield self.run_block(shell, **kw)[0]

    def test_no_memory_polls_overrides_and_remembers(self):
        for g in self.each():
            self.assertEqual(11, g['checks'])
            self.assertEqual(10, g['sleeps'])
            self.assertEqual(['resetprop -n ro.audio.ihaladaptervendorextension_enabled false'], g['resetprop'])
            self.assertEqual('ro.audio.ihaladaptervendorextension_enabled=true\n', g['overridden'])
            self.assertEqual([OVERRIDE, REMEMBERED_LINE], g['out'])
            self.assertEqual(FP + '\n', g['memory'])
            self.assertEqual('0o600', g['mode'])
            self.assertFalse(g['tmp_left'])

    def test_memory_same_fingerprint_is_one_check(self):
        for g in self.each(memory=FP):
            self.assertEqual(1, g['checks'])
            self.assertEqual(0, g['sleeps'])
            self.assertEqual(1, len(g['resetprop']))
            self.assertEqual('ro.audio.ihaladaptervendorextension_enabled=true\n', g['overridden'])
            self.assertEqual([OVERRIDE + ' (remembered for this firmware)'], g['out'])
            self.assertEqual(FP + '\n', g['memory'])

    def test_memory_and_found_removes_memory_and_does_not_override(self):
        for g in self.each(memory=FP, found=True):
            self.assertEqual(1, g['checks'])
            self.assertEqual(0, g['sleeps'])
            self.assertEqual([], g['resetprop'])
            self.assertIsNone(g['overridden'])
            self.assertEqual([], g['out'])
            self.assertIsNone(g['memory'])

    def test_different_fingerprint_polls_and_rewrites(self):
        for g in self.each(memory=FP + '-old'):
            self.assertEqual(11, g['checks'])
            self.assertEqual(10, g['sleeps'])
            self.assertEqual([OVERRIDE, REMEMBERED_LINE], g['out'])
            self.assertEqual(FP + '\n', g['memory'])

    def test_found_while_polling_leaves_no_memory(self):
        for g in self.each(found=True):
            self.assertEqual(0, g['sleeps'])
            self.assertEqual([], g['resetprop'])
            self.assertIsNone(g['memory'])

    def test_no_baseline_file_is_todays_behaviour(self):
        for g in self.each(fp=None):
            self.assertEqual(11, g['checks'])
            self.assertEqual(10, g['sleeps'])
            self.assertEqual([OVERRIDE], g['out'])
            self.assertIsNone(g['memory'])

    def test_empty_fingerprint_is_todays_behaviour(self):
        for g in self.each(fp=''):
            self.assertEqual(10, g['sleeps'])
            self.assertEqual([OVERRIDE], g['out'])
            self.assertIsNone(g['memory'])

    def test_memory_without_a_fingerprint_does_not_apply(self):
        for g in self.each(fp=None, memory=FP):
            self.assertEqual(10, g['sleeps'])
            self.assertEqual([OVERRIDE], g['out'])

    def test_symlink_in_the_memory_place_is_not_followed(self):
        for g in self.each(symlink_memory=True):
            self.assertEqual(10, g['sleeps'])
            self.assertEqual([OVERRIDE], g['out'])
            self.assertTrue(g['mem_is_link'])
            self.assertEqual('keep\n', g['victim'])


if __name__ == '__main__':
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        SWITCH = Path(sys.argv.pop(1))
    SOURCE = SWITCH.read_text()
    unittest.main()
