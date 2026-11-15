#!/usr/bin/env python3
"""oneos_switch.sh: HyperOS's dynamic island centred on this phone's camera.

HyperOS's island plugin centres the island on SystemUI's dimen/cut_out_center_y,
which the image sets for its Xiaomi: on a Galaxy S23 (11-15) the island sat 20 px
below the camera, and changing the display cutout moved nothing. The UI
activator registers OneOSIslandY with the middle of the lens One UI's cutout
gives (hole= in host_statusbar), in the guest's pixels. Runs the real block,
taken out of the switch's guest script, with stand-ins for its overlay helpers.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()


def block():
    start = SWITCH.index('                    if [ $GUEST_MIUI = 1 ] && [ \\"${ONEOS_GUEST_ISLAND_Y:-1}\\" = 1 ]; then')
    end = SWITCH.index('\n                    fi\n', start) + len('\n                    fi\n')
    # The block is inside the guest script's double quotes: undo that escaping.
    return SWITCH[start:end].replace('\\$', '$').replace('\\"', '"')


class IslandYTest(unittest.TestCase):
    def run_block(self, hole='510,19:570,79', miui='1', sbk='', env=None):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            (r / 'host_statusbar').write_text('spec=x\nheight=98\n' + (f'hole={hole}\n' if hole else ''))
            script = ('gdim() { printf "%s\\n" "$*" >> "$STAGE/gdim.log"; echo "registered com.android.shell:$1"; }\n'
                      'gxo() { printf "%s\\n" "$*" >> "$STAGE/gxo.log"; }\n'
                      f'OVS=""; DIMS=""; SBK="{sbk}"\n' + block() +
                      'echo "OVS=$OVS"; echo "DIMS=$DIMS"\n')
            done = subprocess.run(['sh', '-c', script], capture_output=True, text=True,
                                  env={**os.environ, 'STAGE': t, 'GUEST_MIUI': miui, **(env or {})})
            self.assertEqual(0, done.returncode, done.stderr)
            logs = {n: (r / f'{n}.log').read_text() if (r / f'{n}.log').exists() else '' for n in ('gdim', 'gxo')}
            return done.stdout, logs

    def test_s23_lens_middle(self):
        out, logs = self.run_block()
        self.assertEqual('OneOSIslandY com.android.systemui com.android.systemui:dimen/cut_out_center_y=49\n', logs['gdim'])
        self.assertIn('OVS= OneOSIslandY', out)

    def test_another_phone_its_own_lens(self):
        out, logs = self.run_block(hole='685,40:755,110')
        self.assertIn('cut_out_center_y=75', logs['gdim'])

    def test_guest_at_another_width_scales(self):
        out, logs = self.run_block(hole='686,34:756,104', sbk='1080 1440')
        self.assertIn('cut_out_center_y=52', logs['gdim'])

    def test_no_lens_read_leaves_the_image_value(self):
        out, logs = self.run_block(hole='')
        self.assertEqual('', logs['gdim'])
        self.assertIn('OVS=\n', out)

    def test_bad_hole_is_ignored(self):
        out, logs = self.run_block(hole='510,79:570,19')
        self.assertEqual('', logs['gdim'])

    def test_not_hyperos_does_nothing(self):
        out, logs = self.run_block(miui='0')
        self.assertEqual('', logs['gdim'] + logs['gxo'])

    def test_turned_off_disables_an_earlier_one(self):
        out, logs = self.run_block(env={'ONEOS_GUEST_ISLAND_Y': '0'})
        self.assertEqual('', logs['gdim'])
        self.assertEqual('disable --user 0 com.android.shell:OneOSIslandY\n', logs['gxo'])

    def test_no_host_cutout_turns_it_off(self):
        self.assertIn('OneOSNtStatusBar|OneOSIslandY)', SWITCH)


if __name__ == '__main__':
    unittest.main()
