#!/usr/bin/env python3
"""oneos_oplus_telemetry.sh (guest_prepare.sh): com.oplus.onetrace and com.oplus.olc off in ColorOS guests.

Owner, 2026-11-17: S25 Ultra, coloros17-findx9u-in-1105, sessions
db19aee410bd79ff and 75e538ac0ed725df showed incident_helper plus a second top
at 40-100% CPU for 30-60 s around com.oplus.onetrace activity. MODE=off turns
the two packages off and records what OneOS turned off; MODE=keep turns back on
only the recorded ones. This runs the real heredoc with a stub pm (state in a
temp dir), and the real guest_prepare.sh block that writes or removes the file.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREPARE = (ROOT / 'switcher/assets/guest_prepare.sh').read_text()
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()
BLOCK = 'rm -f "$STAGE/data/oneos_oplus_telemetry.sh"\n' + \
    PREPARE.split('rm -f "$STAGE/data/oneos_oplus_telemetry.sh"\n', 1)[1].split("\n# The guest's /oem", 1)[0] + '\n'
BODY = PREPARE.split("<<'TELE'\n", 1)[1].split('\nTELE\n', 1)[0] + '\n'

ONT, OLC = 'com.oplus.onetrace', 'com.oplus.olc'

STUBS = {
    'timeout': 'shift; exec "$@"\n',
    # $ST/installed.<pkg>: installed; $ST/disabled.<pkg>: disabled; $ST/fail.<pkg>: pm fails
    'pm': (
        'echo "$*" >> "$ST/pm.calls"\n'
        '[ -f "$ST/pm_reads_stdin" ] && cat >/dev/null\n'
        'case "$1 $2" in\n'
        '"path "*) [ -f "$ST/installed.$2" ] && echo "package:/x/$2.apk" ;;\n'
        '"list packages") for f in "$ST"/disabled.*; do [ -f "$f" ] && echo "package:${f##*/disabled.}"; done; true ;;\n'
        '"disable-user --user") p=$4; [ -f "$ST/fail.$p" ] && { echo "Error: nope"; exit 1; }\n'
        '    touch "$ST/disabled.$p"; echo "Package $p new state: disabled-user" ;;\n'
        '"enable "*) p=$2; [ -f "$ST/fail.$p" ] && { echo "Error: nope"; exit 1; }\n'
        '    rm -f "$ST/disabled.$p"; echo "Package $p new state: enabled" ;;\n'
        'esac\n'
    ),
}


class Guest:
    def __init__(self, d, mode):
        self.root = Path(d)
        self.st = self.root / 'st'
        self.bin = self.root / 'bin'
        (self.root / 'data').mkdir()
        self.st.mkdir()
        self.bin.mkdir()
        for n, body in STUBS.items():
            f = self.bin / n
            f.write_text('#!/bin/sh\n' + body)
            f.chmod(0o755)
        self.rec = self.root / 'data/oneos_oplus_telemetry_off'
        self.script = self.root / 'tele.sh'
        text = f'MODE={mode}\n' + BODY
        self.script.write_text(text.replace('/data/', f'{d}/data/'))

    def pkg(self, p, installed=True, disabled=False):
        if installed:
            (self.st / f'installed.{p}').write_text('')
        if disabled:
            (self.st / f'disabled.{p}').write_text('')

    def disabled(self, p):
        return (self.st / f'disabled.{p}').exists()

    def run(self):
        env = dict(os.environ, ST=str(self.st), PATH=f'{self.bin}:{os.environ["PATH"]}')
        r = subprocess.run(['/bin/sh', str(self.script)], env=env, timeout=60,
                           capture_output=True, text=True)
        self.assert_exit0 = r.returncode
        return r.stdout + r.stderr

    def record(self):
        return self.rec.read_text().split() if self.rec.exists() else None


class Telemetry(unittest.TestCase):
    def test_scripts_parse(self):
        subprocess.run(['/bin/sh', '-n', '-c', 'MODE=off\n' + BODY], check=True)

    def test_off_disables_and_records(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'off'); g.pkg(ONT); g.pkg(OLC)
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertTrue(g.disabled(ONT) and g.disabled(OLC))
            self.assertEqual(g.record(), [ONT, OLC])
            self.assertIn('2 turned off, 0 failed', log)
            g.run()  # a second session: already off, record not doubled
            self.assertEqual(g.record(), [ONT, OLC])

    def test_off_skips_not_installed(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'off'); g.pkg(OLC)
            log = g.run()
            self.assertIn(f'{ONT}: not installed', log)
            self.assertNotIn(f'disable-user --user 0 {ONT}', (g.st / 'pm.calls').read_text())
            self.assertEqual(g.record(), [OLC])

    def test_off_leaves_user_disabled_out_of_record(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'off'); g.pkg(ONT, disabled=True); g.pkg(OLC)
            log = g.run()
            self.assertIn(f'{ONT}: already off', log)
            self.assertEqual(g.record(), [OLC])
            self.assertNotIn(f'disable-user --user 0 {ONT}', (g.st / 'pm.calls').read_text())

    def test_off_failure_is_logged_not_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'off'); g.pkg(ONT); g.pkg(OLC)
            (g.st / f'fail.{ONT}').write_text('')
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertIn(f'{ONT}: pm disable-user failed: Error: nope', log)
            self.assertEqual(g.record(), [OLC])

    def test_keep_enables_only_recorded_and_removes_record(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep')
            g.pkg(ONT, disabled=True); g.pkg(OLC, disabled=True)  # OLC: the user's own
            g.rec.write_text(ONT + '\ncom.other.app\n')
            g.pkg('com.other.app', disabled=True)
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertFalse(g.disabled(ONT))
            self.assertTrue(g.disabled(OLC))
            self.assertTrue(g.disabled('com.other.app'))
            self.assertIsNone(g.record())
            self.assertIn('1 turned back on', log)

    def test_keep_survives_a_pm_that_reads_stdin(self):
        # pm is cmd, which forwards stdin: it must not eat the rest of the record
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep'); g.pkg(ONT, disabled=True); g.pkg(OLC, disabled=True)
            (g.st / 'pm_reads_stdin').write_text('')
            g.rec.write_text(ONT + '\n' + OLC + '\n')
            g.run()
            self.assertFalse(g.disabled(ONT))
            self.assertFalse(g.disabled(OLC))
            self.assertIsNone(g.record())

    def test_keep_failure_keeps_record(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep'); g.pkg(ONT, disabled=True)
            g.rec.write_text(ONT + '\n')
            (g.st / f'fail.{ONT}').write_text('')
            g.run()
            self.assertEqual(g.record(), [ONT])

    def test_keep_without_record_touches_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep'); g.pkg(ONT, disabled=True)
            g.run()
            self.assertTrue(g.disabled(ONT))
            self.assertFalse((g.st / 'pm.calls').exists())

    def test_symlinked_record_is_refused(self):
        for mode in ('off', 'keep'):
            with tempfile.TemporaryDirectory() as d:
                g = Guest(d, mode); g.pkg(ONT, disabled=(mode == 'keep'))
                victim = Path(d) / 'victim'
                victim.write_text('precious\n')
                g.rec.symlink_to(victim)
                log = g.run()
                self.assertEqual(g.assert_exit0, 0)
                self.assertIn('was a symlink', log)
                self.assertEqual(victim.read_text(), 'precious\n')
                self.assertFalse(g.rec.is_symlink())
                if mode == 'keep':
                    self.assertTrue(g.disabled(ONT))
                else:
                    self.assertEqual(g.record(), [ONT])


class Prepare(unittest.TestCase):
    def prepare(self, d, coloros, env=None):
        root = Path(d)
        (root / 'stage/data').mkdir(parents=True)
        (root / 'stage/rootfs/system/framework').mkdir(parents=True)
        if coloros:
            (root / 'stage/rootfs/system/framework/oplus-services.jar').write_text('x')
        out = root / 'stage/data/oneos_oplus_telemetry.sh'
        out.write_text('stale\n')
        e = dict(os.environ, STAGE=str(root / 'stage'), ROOTFS=str(root / 'stage/rootfs'), BB='true')
        e.pop('ONEOS_GUEST_OPLUS_TELEMETRY', None)
        e.update(env or {})
        r = subprocess.run(['/bin/sh', '-c', BLOCK], env=e, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, r.stdout

    def test_written_for_coloros_only(self):
        with tempfile.TemporaryDirectory() as d:
            out, msg = self.prepare(d, True)
            self.assertTrue(out.read_text().startswith('MODE=off\n'))
            self.assertEqual(oct(out.stat().st_mode & 0o777), '0o644')
            self.assertIn('off', msg)
            subprocess.run(['/bin/sh', '-n', str(out)], check=True)
        with tempfile.TemporaryDirectory() as d:
            out, _ = self.prepare(d, False)
            self.assertFalse(out.exists())

    def test_keep_mode(self):
        with tempfile.TemporaryDirectory() as d:
            out, _ = self.prepare(d, True, {'ONEOS_GUEST_OPLUS_TELEMETRY': '1'})
            self.assertTrue(out.read_text().startswith('MODE=keep\n'))

    def test_symlink_guard_lists_the_script(self):
        self.assertIn('oneos_sysui_compile.sh oneos_oplus_telemetry.sh; do', PREPARE)

    def test_ui_activator_runs_it(self):
        self.assertIn('[ -f \\"$STAGE/data/oneos_oplus_telemetry.sh\\" ]', SWITCH)
        self.assertIn('/system/bin/sh /data/oneos_oplus_telemetry.sh >\\"$STAGE/oplus_telemetry.log\\" 2>&1 </dev/null &', SWITCH)


if __name__ == '__main__':
    unittest.main()
