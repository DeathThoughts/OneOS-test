#!/usr/bin/env python3
"""No `sh -c "..."` string of oneos_switch.sh comes near Linux's 128 KB argv limit.

S23 SM-S911B, ColorOS 17 (session 9120b5a653a89fdb, 2026-11-15): the UI
activator was one double-quoted `sh -c` string of over 128 KB once the switch
expanded it; execve took it for E2BIG ("Argument list too long",
MAX_ARG_STRLEN 131072 bytes), the activator never ran, and the watcher
returned to One UI after 150 s. The activator is now written to a file and
run with sh; every string still passed as an argument must stay under 96 KB.
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH_PATH = ROOT / 'switcher/assets/oneos_switch.sh'
SWITCH = SWITCH_PATH.read_text()
RETURN = (ROOT / 'switcher/assets/return_to_oneui.sh').read_text()

STAGE = '/data/local/tmp/oneos'
BB = '/data/adb/ksu/bin/busybox'
LIMIT = 96 * 1024
# Any variable but STAGE and BB the switch expands into a string: a path or a
# number, never near this.
OTHER_VAR = 256

ACT_START = '\nUI_ACTIVATOR="\n'
ACT_WRITE = 'rm -f "$STAGE/tmp/ui_activator.sh"\n'
ACT_RUN = '    $BB sh "$STAGE/tmp/ui_activator.sh" </dev/null >/data/local/tmp/ui_activator.log 2>&1 &\n'


def dq_end(text, i):
    """Index of the double quote that closes the string opened just before i."""
    while True:
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if c == '"':
            return i
        i += 1


def sh_c_strings():
    for m in re.finditer(r'\bsh -c "', SWITCH):
        a = m.end()
        yield SWITCH.count('\n', 0, m.start()) + 1, SWITCH[a:dq_end(SWITCH, a)]


def expanded_size(body):
    names = re.findall(r'(?<!\\)\$\{?([A-Za-z_][A-Za-z0-9_]*)', body)
    size = len(body.encode())
    for n in names:
        size += {'STAGE': len(STAGE), 'BB': len(BB)}.get(n, OTHER_VAR) - len(n) - 1
    return size


def activator_body():
    a = SWITCH.index(ACT_START) + len(ACT_START)
    return SWITCH[a:dq_end(SWITCH, a)]


class ShCSize(unittest.TestCase):
    def test_every_sh_c_string_is_under_96_kb(self):
        found = list(sh_c_strings())
        self.assertGreater(len(found), 5)
        for line, body in found:
            self.assertLess(expanded_size(body), LIMIT, 'sh -c string at line %d' % line)

    def test_the_activator_runs_from_a_file(self):
        self.assertNotIn('\n" </dev/null >/data/local/tmp/ui_activator.log', SWITCH)
        self.assertEqual(1, SWITCH.count(ACT_START))
        self.assertEqual(1, SWITCH.count(ACT_RUN))
        self.assertLess(SWITCH.index(ACT_START), SWITCH.index(ACT_WRITE))
        self.assertLess(SWITCH.index(ACT_WRITE), SWITCH.index(ACT_RUN))
        # Too long for an argument: the reason it is a file.
        self.assertGreater(expanded_size(activator_body()), 128 * 1024)
        # The return stops it by the file's name on its command line.
        self.assertIn('pkill -9 -f "ui_activator" 2>/dev/null', RETURN)
        self.assertIn('ui_activator', ACT_RUN.split('<')[0])


class ActivatorFile(unittest.TestCase):
    """Writes the file with the switch's own lines (dash), as on a session start."""

    def render(self, d, link=None):
        stage = Path(d, 'stage')
        (stage / 'tmp').mkdir(parents=True)
        bb = Path(d, 'busybox')
        bb.write_text('#!/bin/sh\nexec "$@"\n')
        bb.chmod(0o755)
        if link:
            os.symlink(link, stage / 'tmp/ui_activator.sh')
        a = SWITCH.index(ACT_START) + 1
        b = SWITCH.index('\nthen\n' + ACT_RUN, a)
        names = sorted(set(re.findall(r'(?<!\\)\$\{?([A-Za-z_][A-Za-z0-9_]*)', activator_body())))
        env = {n: '1' if n.startswith('GUEST_') or n.endswith('_PID') else '/x/' + n.lower() for n in names}
        env.update(STAGE=str(stage), BB=str(bb), PATH=os.environ['PATH'])
        Path(d, 'write.sh').write_text(SWITCH[a:b] + '\nthen echo written; else echo refused; fi\n')
        r = subprocess.run(['/bin/sh', str(Path(d, 'write.sh'))], env=env, capture_output=True, text=True, timeout=60)
        # The same string as the old `sh -c` argument got.
        Path(d, 'gen.sh').write_text("printf '%s' \"" + activator_body() + '"\n')
        want = subprocess.run(['/bin/sh', str(Path(d, 'gen.sh'))], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual((0, ''), (want.returncode, want.stderr))
        return r, stage / 'tmp/ui_activator.sh', want.stdout

    def test_it_is_the_old_string_and_parses(self):
        with tempfile.TemporaryDirectory() as d:
            r, f, want = self.render(d)
            self.assertEqual((0, 'written\n', ''), (r.returncode, r.stdout, r.stderr))
            self.assertEqual(0o600, f.stat().st_mode & 0o777)
            text = f.read_text()
            first, rest = text.split('\n', 1)
            self.assertTrue(first.startswith('# Written by oneos_switch.sh'))
            self.assertEqual('\n' + want + '\n', rest)
            self.assertIn("echo '[UI Activator] Home activated!'", text)
            n = subprocess.run(['/bin/sh', '-n', str(f)], capture_output=True, text=True, timeout=60)
            self.assertEqual((0, ''), (n.returncode, n.stderr))

    def test_a_link_in_its_place_is_replaced_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            victim = Path(d, 'victim')
            victim.write_text('keep\n')
            r, f, _ = self.render(d, link=str(victim))
            self.assertEqual('written\n', r.stdout)
            self.assertFalse(f.is_symlink())
            self.assertEqual('keep\n', victim.read_text())


if __name__ == '__main__':
    unittest.main()
