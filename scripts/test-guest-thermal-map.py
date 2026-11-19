#!/usr/bin/env python3
"""The guest thermal status from skin (oneos_switch.sh) is on for Pixel and ColorOS.

S25 Ultra, ColorOS 17 (session db19aee410bd79ff): Samsung's HAL gave the guest
status 3 (SEVERE) from 43.2 C skin, so ColorOS dropped window blur and
oneos_sysui_compile.sh postponed every app compile. The owner chose the skin
mapping for ColorOS too (2026-11-17). These tests run the real condition lines
against a fake stage; ONEOS_GUEST_THERMAL_MAP=0 turns it off for both.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()

START = 'GUEST_THERMAL_MAP=0\n'
END = 'if [ $HEAT_CAP = 1 ] || [ $GUEST_THERMAL_MAP = 1 ]; then'


def condition():
    a = SWITCH.index(START)
    return SWITCH[a:SWITCH.index(END, a)]


class GuestThermalMapTest(unittest.TestCase):
    def mapped(self, image, oplus, env=None):
        with tempfile.TemporaryDirectory(prefix='oneos-thermalmap-') as t:
            stage = Path(t)
            (stage / '.oneos_stage').write_text('{\n  "image": "%s"\n}\n' % image)
            e = {k: v for k, v in os.environ.items() if k != 'ONEOS_GUEST_THERMAL_MAP'}
            e.update(env or {})
            e.update({'STAGE': str(stage), 'GUEST_OPLUS': '1' if oplus else '0'})
            out = subprocess.run(['sh', '-c', condition() + 'echo $GUEST_THERMAL_MAP'],
                                 env=e, capture_output=True, text=True)
            self.assertEqual(0, out.returncode, out.stderr)
            return out.stdout.strip()

    def test_the_condition_is_real_shell(self):
        self.assertEqual(0, subprocess.run(['sh', '-n', '-c', condition()]).returncode)

    def test_coloros_stage_is_on(self):
        self.assertEqual('1', self.mapped('coloros17-findx9u-in-1105', True))

    def test_pixel_image_is_on(self):
        self.assertEqual('1', self.mapped('pixel9-bp4a', False))

    def test_other_guests_are_off(self):
        self.assertEqual('0', self.mapped('hyperos3-xiaomi', False))

    def test_the_kill_switch_turns_both_off(self):
        env = {'ONEOS_GUEST_THERMAL_MAP': '0'}
        self.assertEqual('0', self.mapped('coloros17-findx9u-in-1105', True, env))
        self.assertEqual('0', self.mapped('pixel9-bp4a', False, env))


if __name__ == '__main__':
    unittest.main()
