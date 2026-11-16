#!/usr/bin/env python3
"""guest_start.sh's step time lines ("[Pivoted Root] time HH:MM:SS <step>").

ColorOS native audit 2026-11-16, F15: none of guest_start.sh's ~115
"[Pivoted Root]" lines carried a time, so the 21 s from its start to the
zygote (S25 Ultra session bceaef29fb128e94: 05:16:48 -> 05:17:09) could not be
split into steps. The time lines are additions only: each has the one fixed
form, they come in script order before the zygote is launched, the launch line
stays the last one before the exec, no reader of takeover.log (analyze-session.py,
oneos_switch.sh's greps) matches them, and against git HEAD no other line of
the script changed.
"""
import difflib
import importlib.util
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START_PATH = ROOT / 'switcher/assets/guest_start.sh'
START = START_PATH.read_text()
LINES = START.split('\n')
ANALYZER = ROOT / 'scripts/analyze-session.py'

# The source form of a time line, and what it prints.
SRC_RE = re.compile(r'^echo "\[Pivoted Root\] time \$\(\$BB date \+%T\) ([a-z0-9-]+)"$')
OUT_RE = re.compile(r'^\[Pivoted Root\] time ([01]\d|2[0-3]):[0-5]\d:[0-5]\d [a-z0-9-]+$')
LAUNCH = 'echo "[Pivoted Root] Launching guest Zygote & SystemServer via zygote_exec..."'


def time_lines():
    return [(i, line) for i, line in enumerate(LINES)
            if '[Pivoted Root] time ' in line and not line.lstrip().startswith('#')]


def load_analyzer():
    spec = importlib.util.spec_from_file_location('analyze_session', ANALYZER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StepTimes(unittest.TestCase):
    def test_every_time_line_has_the_one_form(self):
        found = time_lines()
        for i, line in found:
            self.assertRegex(line, SRC_RE, f'guest_start.sh:{i + 1}')
        names = [SRC_RE.match(line).group(1) for _, line in found]
        self.assertEqual(len(names), len(set(names)), f'step names repeat: {names}')

    def test_at_least_ten(self):
        self.assertGreaterEqual(len(time_lines()), 10)

    def test_top_level_in_order_before_the_zygote(self):
        launch = LINES.index(LAUNCH)
        found = time_lines()
        self.assertEqual('start', SRC_RE.match(found[0][1]).group(1))
        self.assertEqual('zygote', SRC_RE.match(found[-1][1]).group(1))
        for i, _ in found:
            self.assertLess(i, launch)
        # Right before the launch line, which stays the last line before the exec.
        self.assertEqual(found[-1][0], launch - 1)
        tail = [l for l in LINES[launch + 1:] if l.strip() and not l.lstrip().startswith('#')]
        self.assertEqual('if [ -f /data/oneos_root/enabled ]; then', tail[0])
        self.assertIn('exec /data/zygote_exec', tail)
        first_exec = next(i for i, l in enumerate(LINES) if l.strip() in (
            'exec /data/zygote_exec', 'exec /data/adb/oneos/bin/su --launch'))
        self.assertGreater(first_exec, launch)

    def test_lines_print_the_form(self):
        found = time_lines()
        script = '\n'.join(line for _, line in found) + '\n'
        bbs = [''] + ([shutil.which('busybox') + ' '] if shutil.which('busybox') else [])
        for bb in bbs:
            out = subprocess.run(['sh', '-c', script], env={'BB': bb.strip(), 'PATH': '/usr/bin:/bin'},
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(0, out.returncode, out.stderr)
            got = out.stdout.splitlines()
            self.assertEqual(len(found), len(got))
            for line in got:
                self.assertRegex(line, OUT_RE)

    def test_no_reader_takes_them_for_its_lines(self):
        a = load_analyzer()
        sample = [f'[Pivoted Root] time 05:16:48 {SRC_RE.match(l).group(1)}' for _, l in time_lines()]
        regexes = [a.FAIL_RE, a.THREADTIME_RE, a.TS_PREFIX_RE, a.HMS_RE, a.KICK_FAILURE_RE,
                   a.KICK_EXHAUSTION_RE, a.KICK_NEGATIVE_RE, a.KICK_SUCCESS_RE]
        for line in sample:
            for r in regexes:
                self.assertIsNone(r.search(line), f'{r.pattern} matches {line!r}')
            low = line.lower()
            # analyze_kick's own filter of takeover*.log lines.
            for word in ('sf render rate', 'no physical displays', 'left to the panel keeper',
                         'displaymanagerpolicy', 'panel at '):
                self.assertNotIn(word, low)
            # oneos_switch.sh's greps of takeover.log and the tests' launch anchor.
            for word in ('SurfaceFlinger PID:', 'HW overlays', 'GPU composition', 'Launching guest Zygote'):
                self.assertNotIn(word, line)

    def test_only_additions_against_head(self):
        try:
            head = subprocess.run(['git', '-C', str(ROOT), 'show', 'HEAD:switcher/assets/guest_start.sh'],
                                  capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as e:
            self.skipTest(f'no git HEAD: {e}')
        old = head.split('\n')
        # Lines are only added: time lines, and the comment that introduces them.
        ops = difflib.SequenceMatcher(None, old, LINES, autojunk=False).get_opcodes()
        for tag, i1, i2, j1, j2 in ops:
            if tag == 'equal':
                continue
            self.assertEqual('insert', tag, f'HEAD {old[i1:i2]} became {LINES[j1:j2]}')
            for line in LINES[j1:j2]:
                self.assertTrue(SRC_RE.match(line) or line.startswith('# '), line)
        def code(text):
            return [l for l in text if '[Pivoted Root]' in l and not l.lstrip().startswith('#')]
        old_pr = code(old)
        new_pr = [l for l in code(LINES) if not SRC_RE.match(l)]
        self.assertEqual(old_pr, new_pr)


if __name__ == '__main__':
    unittest.main()
