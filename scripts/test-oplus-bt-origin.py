#!/usr/bin/env python3
"""The ColorOS Bluetooth override is opt-in, including on the Galaxy A55."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'switcher/assets/guest_props.sh').read_text()
START = '    BTO=persist.sys.oplus.bt.use_origin\n'
CODE = START + SOURCE.split(START, 1)[1].split('    # Power key stall (run 32:', 1)[0]
KEY = 'persist.sys.oplus.bt.use_origin'
OPT_IN = {'ONEOS_GUEST_BT_ORIGIN': '1'}


class OplusBtOrigin(unittest.TestCase):
    def run_case(self, soc='MT6789', oplus=1, env=None, new='ro.build.id=X\n', host='ro.soc.model\n', props=None, rp_fails=False):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); store = root / 'props'; store.mkdir()
            for k, v in {'ro.soc.model': soc, **(props or {})}.items(): (store / k).write_text(v)
            (root / 'new').write_text(new); (root / 'host').write_text(host)
            rp = root / 'rp'
            rp.write_text('#!/bin/sh\n[ "$1" = -n ] || exit 2\n' + ('exit 1\n' if rp_fails else 'printf "%s" "$3" > "$PROPS/$2"\n'))
            rp.chmod(0o755)
            prefix = 'getprop() { [ -f "$PROPS/$1" ] && cat "$PROPS/$1"; }\n'
            e = {k: v for k, v in os.environ.items() if k != 'ONEOS_GUEST_BT_ORIGIN'}
            e.update({'PROPS': str(store), 'RP': str(rp), 'OVR': str(root / 'ovr'), 'NEW': str(root / 'new'),
                      'HOST': str(root / 'host'), 'GUEST_OPLUS': str(oplus), **(env or {})})
            out = subprocess.run(['sh', '-c', prefix + CODE], env=e, check=True, capture_output=True, text=True)
            self.assertEqual('', out.stderr)
            ovr = (root / 'ovr').read_text() if (root / 'ovr').exists() else ''
            now = {p.name: p.read_text() for p in store.iterdir() if p.name != 'ro.soc.model'}
            return out.stdout, (root / 'new').read_text(), ovr, now

    def test_a_helio_g99_phone_also_requires_opt_in(self):
        for soc in ('MT6789', 'MT6789V/CD'):
            with self.subTest(soc):
                self.assertEqual(('', 'ro.build.id=X\n', '', {}), self.run_case(soc=soc))

    def test_the_images_own_value_is_replaced_not_doubled(self):
        _, new, _, _ = self.run_case(env=OPT_IN, new=f'a=1\n{KEY}=false\npersist.sys.oplus.bt.use_originX=1\nb=2\n')
        self.assertEqual(f'a=1\npersist.sys.oplus.bt.use_originX=1\nb=2\n{KEY}=true\n', new)

    def test_other_phones_and_other_systems_are_left_alone(self):
        # A34 (MT6877): ColorOS's Bluetooth is reported working as it is. Exynos and Qualcomm: no such crash.
        for kw in ({'soc': 'MT6877'}, {'soc': 'SM8850'}, {'soc': 's5e8845'}, {'soc': ''}, {'oplus': 0},
                   {'env': {'ONEOS_GUEST_BT_ORIGIN': '0'}}, {'env': {'ONEOS_GUEST_BT_ORIGIN': 'yes'}}):
            with self.subTest(kw):
                self.assertEqual(('', 'ro.build.id=X\n', '', {}), self.run_case(**kw))

    def test_the_override_sets_it_on_any_phone_with_a_coloros_guest(self):
        for soc in ('SM8850', 's5e8845', 'MT6789'):
            with self.subTest(soc):
                out, new, ovr, now = self.run_case(soc=soc, env=OPT_IN)
                self.assertEqual((f'guest_props: {KEY}=true for the session (ONEOS_GUEST_BT_ORIGIN=1)\n',
                                  f'ro.build.id=X\n{KEY}=true\n', '', {}), (out, new, ovr, now))
        self.assertEqual(('', 'ro.build.id=X\n', '', {}), self.run_case(soc='s5e8845', oplus=0, env=OPT_IN))

    def test_a_name_one_ui_holds_is_overridden_and_restorable(self):
        # Left behind by a session that never returned: then it is One UI's to get back.
        host = f'ro.soc.model\n{KEY}\n'
        out, new, ovr, now = self.run_case(host=host, env=OPT_IN, props={KEY: 'false'})
        self.assertEqual((f'guest_props: {KEY}=false overridden to true for the session (ONEOS_GUEST_BT_ORIGIN=1)\n', 'ro.build.id=X\n',
                          f'{KEY}=false\n', {KEY: 'true'}), (out, new, ovr, now))
        # Already true, or a write that fails: nothing recorded, nothing said.
        self.assertEqual(('', 'ro.build.id=X\n', '', {KEY: 'true'}), self.run_case(host=host, env=OPT_IN, props={KEY: 'true'}))
        self.assertEqual(('', 'ro.build.id=X\n', '', {KEY: 'false'}), self.run_case(host=host, env=OPT_IN, props={KEY: 'false'}, rp_fails=True))


if __name__ == '__main__':
    unittest.main()
