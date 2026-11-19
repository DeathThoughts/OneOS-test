#!/usr/bin/env python3
"""guards.txt comment lines never count as a pinned file.

x9u-in-1105-sultra's guards.txt from r6 on has comment lines such as
"# services_patched.jar r6: ...", whose second field is a file name. The
preflight's `awk '$2 == n {print $1}'` then printed "#" lines next to the real
hash, so oneos_switch.sh refused every switch with "services_patched.jar is
missing or not the guarded build in guards.txt" (S25 Ultra, 0.5.20 debug build,
session 2cd832be456d4fc1 and the attempts before it, 2026-11-18). Every awk that
reads guards.txt by its second field skips comment lines.
"""
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ['switcher/assets/oneos_switch.sh', 'switcher/assets/guest_prepare.sh', 'scripts/deploy-phone.sh']
GUARDS = ROOT / 'app-android/packages/x9u-in-1105-sultra.guards.txt'
AWK = re.compile(r"awk (?:-v \S+ )?'([^']*)'\s+\"\$(?:STAGE|OVERLAYS)/guards\.txt\"")


def awk_programs(text):
    return [m.group(1) for m in AWK.finditer(text)]


class GuardsCommentLines(unittest.TestCase):
    def test_every_field_two_awk_skips_comments(self):
        n = 0
        for f in FILES:
            for prog in awk_programs((ROOT / f).read_text()):
                if '$2 ==' not in prog:
                    continue
                n += 1
                self.assertTrue('/^#/ { next }' in prog or '$1 !~ /^#/' in prog, f'{f}: {prog[:80]}')
        self.assertGreaterEqual(n, 10)

    def test_the_preflight_sum_is_the_hash_alone(self):
        text = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()
        prog = next(p for p in awk_programs(text) if p.endswith('{print $1}'))
        pins = {l.split()[1]: l.split()[0] for l in GUARDS.read_text().splitlines() if l and not l.startswith('#')}
        for name in ('services_patched.jar', 'oplus-services_patched.jar', 'vold_patched'):
            out = subprocess.run(['awk', '-v', f'n={name}', prog, str(GUARDS)],
                                 capture_output=True, text=True, check=True).stdout
            self.assertEqual(out.strip(), pins[name], name)

    def test_the_real_guards_has_a_comment_naming_the_jar(self):
        # The shape that broke the switch stays covered by the test above.
        self.assertTrue(any(l.split()[1:2] == ['services_patched.jar']
                            for l in GUARDS.read_text().splitlines() if l.startswith('#')))


if __name__ == '__main__':
    unittest.main()
