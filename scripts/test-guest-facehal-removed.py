#!/usr/bin/env python3
"""facehal-rs, the in-guest face enrollment, is removed (review by the mlcraze5 fork, 2026-11-16).

The fork's rule: only One UI enrolls, and nothing is added that lets the guest.
ONEOS_GUEST_FACEHAL=1 staged facehal-rs (guest_prepare.sh) and started it as
IFace/default in Samsung's place (guest_start.sh), which enrolled a face inside
the guest. Now oneos_switch.sh refuses the knob before One UI's framework
stops, guest_prepare.sh never stages the payload and removes one an earlier
prepare staged, and guest_start.sh never starts it and clears an earlier
facehal session's moved-aside face metadata and marker, so Samsung's face HAL
with One UI's enrollment is always the one used. These tests run the real
blocks from the three scripts under a temporary root.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'switcher/assets'
SWITCH = (ASSETS / 'oneos_switch.sh').read_text()
PREPARE = (ASSETS / 'guest_prepare.sh').read_text()
START = (ASSETS / 'guest_start.sh').read_text()


def block(text, start, end):
    a = text.index(start)
    b = text.index(end, a)
    return text[a:b]


def sh(script, env=None):
    return subprocess.run(['sh', '-c', script], env={**os.environ, **(env or {})},
                          capture_output=True, text=True, timeout=30)


class Switch(unittest.TestCase):
    CASE = block(SWITCH, 'case "${ONEOS_GUEST_FACEHAL:-0}" in\n', '\n# The same guard and app-id checks')

    def run_case(self, value):
        env = {k: v for k, v in os.environ.items() if k != 'ONEOS_GUEST_FACEHAL'}
        if value is not None:
            env['ONEOS_GUEST_FACEHAL'] = value
        return subprocess.run(['sh', '-c', 'refuse() { echo "REFUSED: $*"; exit 7; }\n' + self.CASE + '\necho passed\n'],
                              env=env, capture_output=True, text=True)

    def test_unset_and_0_pass(self):
        for v in (None, '0', ''):
            done = self.run_case(v)
            self.assertEqual(0, done.returncode, v)
            self.assertIn('passed', done.stdout)

    def test_1_is_refused(self):
        done = self.run_case('1')
        self.assertEqual(7, done.returncode)
        self.assertIn('ONEOS_GUEST_FACEHAL=1: the in-guest face enrollment (facehal-rs) was removed', done.stdout)

    def test_other_values_refused(self):
        for v in ('2', 'yes'):
            self.assertEqual(7, self.run_case(v).returncode, v)


class Prepare(unittest.TestCase):
    BLOCK = block(PREPARE, '    FH_DST="$STAGE/data/local/tmp/facehal"\n', '    # The SIM on a HIDL radio')
    NO_SYMLINK = block(PREPARE, 'no_symlink() {', '\nfor pair in $BRIDGES')

    def run_block(self, stage, knob='1'):
        return sh(self.NO_SYMLINK + '\n' + self.BLOCK, {'STAGE': str(stage), 'ONEOS_GUEST_FACEHAL': knob})

    def test_never_staged_even_with_the_knob_and_a_payload(self):
        with tempfile.TemporaryDirectory() as d:
            st = Path(d)
            (st / 'data/local/tmp').mkdir(parents=True)
            (st / 'facehal_payload.tar').write_text('pinned set file')
            done = self.run_block(st)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertFalse((st / 'data/local/tmp/facehal').exists())
            self.assertEqual('pinned set file', (st / 'facehal_payload.tar').read_text(), 'set file left as it is')
            self.assertIn("Samsung's face HAL with One UI's enrollment", done.stdout)

    def test_an_earlier_payload_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            st = Path(d)
            hal = st / 'data/local/tmp/facehal/bin/hw/android.hardware.biometrics.face-service.facehal'
            hal.parent.mkdir(parents=True)
            hal.write_text('x')
            done = self.run_block(st, '0')
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertFalse((st / 'data/local/tmp/facehal').exists())
            self.assertIn('the facehal-rs payload an earlier prepare staged is removed', done.stdout)

    def test_a_planted_link_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            st = Path(d)
            (st / 'data/local').mkdir(parents=True)
            target = st / 'host'
            target.mkdir()
            (target / 'facehal').mkdir()
            (target / 'facehal/keep').write_text('keep')
            (st / 'data/local/tmp').symlink_to(target)
            done = self.run_block(st)
            self.assertEqual(3, done.returncode)
            self.assertEqual('keep', (target / 'facehal/keep').read_text())

    def test_no_staging_code_left(self):
        for word in ('facehal_payload.tar" -C', 'tar -xf "$STAGE/facehal_payload.tar"', 'FH_SRC'):
            self.assertNotIn(word, PREPARE)


class Start(unittest.TestCase):
    BLOCK = block(START, 'if [ -L /data/system/users ] || [ -L /data/system/users/0 ]',
                  'echo "[Pivoted Root] Launching guest Zygote')

    def run_block(self, root, knob='1'):
        text = self.BLOCK.replace('/data/', f'{root}/data/')
        stubs = f'stop() {{ echo "$*" >> {root}/stopped; }}\nservice() {{ :; }}\n'
        return sh(stubs + text, {'ONEOS_GUEST_FACEHAL': knob})

    def test_never_started_and_leftovers_cleared(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            u = r / 'data/system/users/0'
            (u / 'oneos-face-host').mkdir(parents=True)
            (u / 'oneos-face-host/settings_face.xml').write_text('old')
            (u / 'settings_face.xml').write_text('carried from One UI')
            (r / 'data/oneos_facehal_moved').write_text('')
            hal = r / 'data/local/tmp/facehal/bin/hw/android.hardware.biometrics.face-service.facehal'
            hal.parent.mkdir(parents=True)
            hal.write_text('#!/bin/sh\ntouch ' + str(r / 'ran') + '\n')
            hal.chmod(0o755)
            done = self.run_block(r)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertFalse((u / 'oneos-face-host').exists())
            self.assertFalse((r / 'data/oneos_facehal_moved').exists())
            self.assertEqual('carried from One UI', (u / 'settings_face.xml').read_text())
            self.assertFalse((r / 'stopped').exists(), "Samsung's face HAL is not stopped")
            self.assertFalse((r / 'ran').exists())
            self.assertIn('moved-aside face metadata removed', done.stdout)

    def test_a_link_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            (r / 'data/system/users/0').mkdir(parents=True)
            target = r / 'host'
            target.mkdir()
            (target / 'keep').write_text('keep')
            (r / 'data/system/users/0/oneos-face-host').symlink_to(target)
            done = self.run_block(r)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual('keep', (target / 'keep').read_text())
            self.assertIn('a symlink, left alone', done.stdout)

    def test_no_start_code_left(self):
        self.assertNotIn('stop sec-biometrics-face-aidl', START)
        self.assertNotIn('face-service.facehal', START)
        self.assertNotIn('FACEHAL_ALGORITHM_LIBRARY', START)


if __name__ == '__main__':
    unittest.main()
