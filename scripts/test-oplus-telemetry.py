#!/usr/bin/env python3
"""oneos_oplus_telemetry.sh (guest_prepare.sh): OPPO telemetry, Phase 1 bloat, and Game Assistant restriction in ColorOS guests.

- Telemetry (owner, 2026-11-17): S25 Ultra, coloros17-findx9u-in-1105, sessions
  db19aee410bd79ff and 75e538ac0ed725df: com.oplus.onetrace and com.oplus.olc off
  for user 0 -> /data/oneos_oplus_telemetry_off; ONEOS_GUEST_OPLUS_TELEMETRY=1
  turns back on only those recorded packages.
- Phase 1 debloat + restrict (2026-11-18): S25 Ultra, coloros17-findx9u-in-1105,
  sessions db475ad5017133c4 and a12bf19e1931e81a: 15 bloat packages off for user 0
  -> /data/oneos_oplus_bloat_off; com.oplus.games stays enabled and gets
  RUN_ANY_IN_BACKGROUND ignore + standby bucket restricted ->
  /data/oneos_oplus_restricted; ONEOS_GUEST_OPLUS_BLOAT=1 turns back on and
  unrestricts only what OneOS recorded.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREPARE = (ROOT / 'switcher/assets/guest_prepare.sh').read_text(encoding='utf-8')
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text(encoding='utf-8')
BLOCK = 'rm -f "$STAGE/data/oneos_oplus_telemetry.sh"\n' + \
    PREPARE.split('rm -f "$STAGE/data/oneos_oplus_telemetry.sh"\n', 1)[1].split("\n# The guest's /oem", 1)[0] + '\n'
BODY = PREPARE.split("<<'TELE'\n", 1)[1].split('\nTELE\n', 1)[0] + '\n'


def find_sh():
    if os.path.exists('/bin/sh'):
        return '/bin/sh'
    for candidate in (shutil.which('sh'), r'C:\Program Files\Git\usr\bin\sh.exe', r'C:\Program Files\Git\bin\sh.exe'):
        if candidate and os.path.exists(candidate):
            return candidate
    raise RuntimeError('No POSIX sh binary found')


SH = find_sh()


def sh_env(extra=None, bin_dir=None):
    parts = []
    if bin_dir is not None:
        parts.append(str(bin_dir))
    if os.name == 'nt':
        sh_dir = Path(SH).parent
        parts.append(str(sh_dir))
        usr_bin = sh_dir.parent / 'usr' / 'bin'
        if usr_bin.is_dir() and usr_bin != sh_dir:
            parts.append(str(usr_bin))
    parts.append(os.environ.get('PATH', ''))
    env = dict(os.environ, PATH=os.pathsep.join(p for p in parts if p))
    if extra:
        env.update(extra)
    return env


def make_symlink(link_path, target_path):
    link_path = Path(link_path)
    target_path = Path(target_path)
    try:
        link_path.symlink_to(target_path)
        return link_path
    except OSError:
        if os.name != 'nt':
            raise
        subprocess.run(
            [SH, '-c', 'ln -s "$1" "$2"', '_', target_path.as_posix(), link_path.as_posix()],
            env=sh_env({'MSYS': 'winsymlinks:lnk'}),
            capture_output=True, text=True, check=True,
        )
        return Path(str(link_path) + '.lnk')


def link_exists(path):
    p = Path(path)
    if p.is_symlink():
        return True
    return os.name == 'nt' and Path(str(p) + '.lnk').exists()


ONT, OLC = 'com.oplus.onetrace', 'com.oplus.olc'
GAMES = 'com.oplus.games'
BLOAT_EXPECTED = [
    'com.android.hotwordenrollment.okgoogle',
    'com.android.hotwordenrollment.xgoogle',
    'com.facebook.appmanager',
    'com.facebook.services',
    'com.facebook.system',
    'com.facebook.katana',
    'com.oplus.ebadge',
    'com.microsoft.appmanager',
    'com.microsoftsdk.crossdeviceservicebroker',
    'com.microsoft.deviceintegrationservice',
    'com.oppo.quicksearchbox',
    'com.heytap.pictorial',
    'com.coloros.assistantscreen',
    'com.oplus.member',
    'com.heytap.browser',
]
DEFERRED_OR_DROPPED = [
    'com.oplus.aiunit',
    'com.oplus.dmp',
    'com.oplus.metis',
    'com.oplus.pantanal.ums',
    'com.heytap.htms',
    'com.heytap.accessory',
    'com.oplus.stdsp',
    'com.daemon.shelper',
    'com.oplus.cosa',
    'com.oplus.statistics.rom',
    'com.oplus.midas',
]

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
    # $ST/appops.<pkg>: raw appops get output (default "No operations."); $ST/fail.appops.<pkg>
    'cmd': (
        'echo "$*" >> "$ST/cmd.calls"\n'
        '{ [ -f "$ST/appops_reads_stdin" ] || [ -f "$ST/pm_reads_stdin" ]; } && cat >/dev/null\n'
        'case "$1 $2 $3 $4" in\n'
        '"appops get --user 0")\n'
        '    p=$5\n'
        '    { [ -f "$ST/fail.appops.$p" ] || [ -f "$ST/fail.appops_get.$p" ]; } && { echo "Error: appops get failed"; exit 1; }\n'
        '    if [ -f "$ST/appops.$p" ]; then cat "$ST/appops.$p"; else echo "No operations."; fi\n'
        '    ;;\n'
        '"appops set --user 0")\n'
        '    p=$5; mode=$7\n'
        '    { [ -f "$ST/fail.appops.$p" ] || [ -f "$ST/fail.appops_set.$p" ]; } && { echo "Error: appops set failed"; exit 1; }\n'
        '    if [ "$mode" = default ]; then rm -f "$ST/appops.$p"; else echo "Uid mode: RUN_ANY_IN_BACKGROUND: $mode" > "$ST/appops.$p"; fi\n'
        '    ;;\n'
        '*) echo "unexpected cmd: $*" >&2; exit 2 ;;\n'
        'esac\n'
    ),
    # $ST/bucket.<pkg>: standby bucket (default "active"); $ST/fail.bucket.<pkg>
    'am': (
        'echo "$*" >> "$ST/am.calls"\n'
        '[ -f "$ST/am_reads_stdin" ] && cat >/dev/null\n'
        'case "$1 $2 $3" in\n'
        '"get-standby-bucket --user 0")\n'
        '    p=$4\n'
        '    [ -f "$ST/fail.bucket.$p" ] && { echo "Error: bucket get failed"; exit 1; }\n'
        '    if [ -f "$ST/bucket.$p" ]; then cat "$ST/bucket.$p"; else echo "active"; fi\n'
        '    ;;\n'
        '"set-standby-bucket --user 0")\n'
        '    p=$4; bucket=$5\n'
        '    [ -f "$ST/fail.bucket.$p" ] && { echo "Error: bucket set failed"; exit 1; }\n'
        '    echo "$bucket" > "$ST/bucket.$p"\n'
        '    ;;\n'
        '*) echo "unexpected am: $*" >&2; exit 2 ;;\n'
        'esac\n'
    ),
}


class Guest:
    def __init__(self, d, mode='off', bloat_mode='off', legacy_mode=False):
        self.root = Path(d)
        self.st = self.root / 'st'
        self.bin = self.root / 'bin'
        (self.root / 'data').mkdir()
        self.st.mkdir()
        self.bin.mkdir()
        for n, body in STUBS.items():
            f = self.bin / n
            f.write_text('#!/bin/sh\n' + body, encoding='utf-8', newline='\n')
            f.chmod(0o755)
        self.rec = self.root / 'data/oneos_oplus_telemetry_off'
        self.bloat_rec = self.root / 'data/oneos_oplus_bloat_off'
        self.restrict_rec = self.root / 'data/oneos_oplus_restricted'
        self.script = self.root / 'tele.sh'
        header = f'MODE={mode}\n' if legacy_mode else f'TELE_MODE={mode}\nBLOAT_MODE={bloat_mode}\n'
        text = header + BODY
        self.script.write_text(
            text.replace('/data/', f'{self.root.as_posix()}/data/'),
            encoding='utf-8', newline='\n',
        )

    def pkg(self, p, installed=True, disabled=False, appop=None, bucket=None):
        if installed:
            (self.st / f'installed.{p}').write_text('', encoding='utf-8', newline='\n')
        if disabled:
            (self.st / f'disabled.{p}').write_text('', encoding='utf-8', newline='\n')
        if appop is not None:
            raw = appop if ('\n' in appop or ':' in appop or appop == 'No operations.') else f'Uid mode: RUN_ANY_IN_BACKGROUND: {appop}\n'
            (self.st / f'appops.{p}').write_text(raw, encoding='utf-8', newline='\n')
        if bucket is not None:
            (self.st / f'bucket.{p}').write_text(bucket + '\n', encoding='utf-8', newline='\n')

    def disabled(self, p):
        return (self.st / f'disabled.{p}').exists()

    def appop(self, p):
        f = self.st / f'appops.{p}'
        if not f.exists():
            return 'default'
        text = f.read_text(encoding='utf-8')
        m = re.findall(r'RUN_ANY_IN_BACKGROUND:\s*([A-Za-z_]+)', text)
        return m[-1] if m else text.strip()

    def bucket(self, p):
        f = self.st / f'bucket.{p}'
        return f.read_text(encoding='utf-8').strip() if f.exists() else 'active'

    def run(self):
        env = sh_env({'ST': self.st.as_posix()}, bin_dir=self.bin)
        r = subprocess.run([SH, self.script.as_posix()], env=env, timeout=60,
                           capture_output=True, text=True)
        self.assert_exit0 = r.returncode
        return r.stdout + r.stderr

    def record(self):
        return self.rec.read_text(encoding='utf-8').splitlines() if self.rec.exists() else None

    def bloat_record(self):
        return self.bloat_rec.read_text(encoding='utf-8').splitlines() if self.bloat_rec.exists() else None

    def restrict_record(self):
        return self.restrict_rec.read_text(encoding='utf-8').splitlines() if self.restrict_rec.exists() else None

    def calls(self, tool):
        f = self.st / f'{tool}.calls'
        return f.read_text(encoding='utf-8') if f.exists() else ''


def extract_pkg_list(name):
    m = re.search(rf'^{name}="([^"]*)"', BODY, re.M)
    assert m, f'{name} not found in BODY'
    return m.group(1).split()


def assert_failed_log(tc, log, prefix, msg):
    # On Windows Git Bash without /tmp, stub #!/bin/sh scripts print a /tmp
    # warning to stderr that `2>&1` captures inside the failed output.
    if os.name == 'nt':
        warn = r'(?:bash\.exe: warning: could not find /tmp, please create!\n)*'
        tc.assertRegex(log, re.escape(prefix) + warn + re.escape(msg))
    else:
        tc.assertIn(prefix + msg, log)


class ListsAndStatic(unittest.TestCase):
    def test_scripts_parse(self):
        env = sh_env()
        subprocess.run([SH, '-n'], input='TELE_MODE=off\nBLOAT_MODE=off\n' + BODY,
                       text=True, env=env, check=True)
        subprocess.run([SH, '-n', (ROOT / 'switcher/assets/guest_prepare.sh').as_posix()],
                       env=env, check=True)

    def test_exact_package_lists(self):
        self.assertEqual(extract_pkg_list('TELE_PKGS'), [ONT, OLC])
        self.assertEqual(extract_pkg_list('BLOAT_PKGS'), BLOAT_EXPECTED)
        self.assertEqual(len(BLOAT_EXPECTED), 15)
        self.assertEqual(extract_pkg_list('RESTRICT_PKGS'), [GAMES])
        all_pkgs = set(extract_pkg_list('TELE_PKGS')) | set(BLOAT_EXPECTED) | {GAMES}
        for p in DEFERRED_OR_DROPPED:
            self.assertNotIn(p, all_pkgs)
            self.assertNotIn(p, BODY)

    def test_every_pm_cmd_am_uses_timeout_and_dev_null(self):
        checked = 0
        for lineno, raw in enumerate(BODY.splitlines(), 1):
            line = re.sub(r'(?:^|\s)#.*$', '', raw)
            # Strip log message string literals so error text like "pm enable failed:" is ignored.
            line = re.sub(r'\blog\s+"[^"]*"', '', line)
            if re.search(r'(?:^|[\s;|&({`])(?:timeout\s+\d+\s+)?(?:pm|cmd|am)\s+', line):
                checked += 1
                self.assertRegex(
                    line, r'\btimeout\s+\d+\s+(?:pm|cmd|am)\b',
                    f'line {lineno} invokes pm/cmd/am without timeout: {raw!r}',
                )
                self.assertIn(
                    '</dev/null', line,
                    f'line {lineno} invokes pm/cmd/am without </dev/null: {raw!r}',
                )
        self.assertGreaterEqual(checked, 10)


class Telemetry(unittest.TestCase):
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
            self.assertNotIn(f'disable-user --user 0 {ONT}', g.calls('pm'))
            self.assertEqual(g.record(), [OLC])

    def test_off_leaves_user_disabled_out_of_record(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'off'); g.pkg(ONT, disabled=True); g.pkg(OLC)
            log = g.run()
            self.assertIn(f'{ONT}: already off', log)
            self.assertEqual(g.record(), [OLC])
            self.assertNotIn(f'disable-user --user 0 {ONT}', g.calls('pm'))

    def test_off_failure_is_logged_not_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'off'); g.pkg(ONT); g.pkg(OLC)
            (g.st / f'fail.{ONT}').write_text('', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            assert_failed_log(self, log, f'{ONT}: pm disable-user failed: ', 'Error: nope')
            self.assertEqual(g.record(), [OLC])

    def test_keep_enables_only_recorded_and_removes_record(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep')
            g.pkg(ONT, disabled=True); g.pkg(OLC, disabled=True)  # OLC: the user's own
            g.rec.write_text(ONT + '\ncom.other.app\n', encoding='utf-8', newline='\n')
            g.pkg('com.other.app', disabled=True)
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertFalse(g.disabled(ONT))
            self.assertTrue(g.disabled(OLC))
            self.assertTrue(g.disabled('com.other.app'))
            self.assertIsNone(g.record())
            self.assertIn('1 turned back on', log)

    def test_legacy_mode_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep', legacy_mode=True)
            g.pkg(ONT, disabled=True)
            g.pkg(BLOAT_EXPECTED[0])
            g.rec.write_text(ONT + '\n', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertFalse(g.disabled(ONT))
            self.assertTrue(g.disabled(BLOAT_EXPECTED[0]))
            self.assertIn('OPPO telemetry apps: kept, 1 turned back on, 0 failed', log)

    def test_keep_survives_a_pm_that_reads_stdin(self):
        # pm is cmd, which forwards stdin: it must not eat the rest of the record
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep'); g.pkg(ONT, disabled=True); g.pkg(OLC, disabled=True)
            (g.st / 'pm_reads_stdin').write_text('', encoding='utf-8', newline='\n')
            g.rec.write_text(ONT + '\n' + OLC + '\n', encoding='utf-8', newline='\n')
            g.run()
            self.assertFalse(g.disabled(ONT))
            self.assertFalse(g.disabled(OLC))
            self.assertIsNone(g.record())

    def test_keep_failure_keeps_record(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep'); g.pkg(ONT, disabled=True)
            g.rec.write_text(ONT + '\n', encoding='utf-8', newline='\n')
            (g.st / f'fail.{ONT}').write_text('', encoding='utf-8', newline='\n')
            g.run()
            self.assertEqual(g.record(), [ONT])

    def test_keep_without_record_touches_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep', bloat_mode='keep'); g.pkg(ONT, disabled=True)
            g.run()
            self.assertTrue(g.disabled(ONT))
            self.assertEqual(g.calls('pm'), '')


class Bloat(unittest.TestCase):
    def test_off_disables_all_15_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            for p in BLOAT_EXPECTED:
                g.pkg(p)
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            for p in BLOAT_EXPECTED:
                self.assertTrue(g.disabled(p), p)
            self.assertEqual(g.bloat_record(), BLOAT_EXPECTED)
            self.assertIn('OPPO bloat apps: 15 turned off, 0 failed', log)
            log2 = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertEqual(g.bloat_record(), BLOAT_EXPECTED)
            self.assertIn('OPPO bloat apps: 0 turned off, 0 failed', log2)
            self.assertIn(f'{BLOAT_EXPECTED[0]}: already off', log2)

    def test_off_skips_not_installed_and_owner_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            # First missing, second owner-disabled, rest installed & enabled.
            g.pkg(BLOAT_EXPECTED[1], disabled=True)
            for p in BLOAT_EXPECTED[2:]:
                g.pkg(p)
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertIn(f'{BLOAT_EXPECTED[0]}: not installed', log)
            self.assertIn(f'{BLOAT_EXPECTED[1]}: already off', log)
            self.assertNotIn(f'disable-user --user 0 {BLOAT_EXPECTED[0]}', g.calls('pm'))
            self.assertNotIn(f'disable-user --user 0 {BLOAT_EXPECTED[1]}', g.calls('pm'))
            self.assertEqual(g.bloat_record(), BLOAT_EXPECTED[2:])
            self.assertIn('OPPO bloat apps: 13 turned off, 0 failed', log)

    def test_off_failure_logged_not_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            for p in BLOAT_EXPECTED[:3]:
                g.pkg(p)
            (g.st / f'fail.{BLOAT_EXPECTED[0]}').write_text('', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            assert_failed_log(self, log, f'{BLOAT_EXPECTED[0]}: pm disable-user failed: ', 'Error: nope')
            self.assertFalse(g.disabled(BLOAT_EXPECTED[0]))
            self.assertEqual(g.bloat_record(), BLOAT_EXPECTED[1:3])
            self.assertIn('OPPO bloat apps: 2 turned off, 1 failed', log)

    def test_keep_restores_all_valid_including_dropped_and_ignores_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='keep')
            dropped = 'com.oplus.statistics.rom'
            g.pkg(BLOAT_EXPECTED[0], disabled=True)
            g.pkg(BLOAT_EXPECTED[1], disabled=True)  # owner-disabled, not in record
            g.pkg(dropped, disabled=True)
            g.bloat_rec.write_text(
                f'{BLOAT_EXPECTED[0]}\n\n../bad/entry\nbad pkg\n{dropped}\n',
                encoding='utf-8', newline='\n',
            )
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertFalse(g.disabled(BLOAT_EXPECTED[0]))
            self.assertFalse(g.disabled(dropped))
            self.assertTrue(g.disabled(BLOAT_EXPECTED[1]))
            self.assertIsNone(g.bloat_record())
            self.assertIn('OPPO bloat apps: kept, 2 turned back on, 0 failed', log)

    def test_keep_failure_preserves_only_failed_and_survives_stdin_drain(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='keep')
            (g.st / 'pm_reads_stdin').write_text('', encoding='utf-8', newline='\n')
            p0, p1, p2 = BLOAT_EXPECTED[0], BLOAT_EXPECTED[6], BLOAT_EXPECTED[14]
            for p in (p0, p1, p2):
                g.pkg(p, disabled=True)
            (g.st / f'fail.{p1}').write_text('', encoding='utf-8', newline='\n')
            g.bloat_rec.write_text(f'{p0}\n{p1}\n{p2}\n', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertFalse(g.disabled(p0))
            self.assertTrue(g.disabled(p1))
            self.assertFalse(g.disabled(p2))
            self.assertEqual(g.bloat_record(), [p1])
            self.assertFalse((g.root / 'data/oneos_oplus_bloat_off.tmp').exists())
            self.assertIn('OPPO bloat apps: kept, 2 turned back on, 1 failed', log)

    def test_keep_without_record_touches_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, mode='keep', bloat_mode='keep')
            g.pkg(BLOAT_EXPECTED[0], disabled=True)
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertTrue(g.disabled(BLOAT_EXPECTED[0]))
            self.assertEqual(g.calls('pm'), '')
            self.assertIn('OPPO bloat apps: kept, 0 turned back on, 0 failed', log)

    def test_off_shrinking_list_restores_dropped_via_tmp(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            (g.st / 'pm_reads_stdin').write_text('', encoding='utf-8', newline='\n')
            d1, d2 = 'com.oplus.statistics.rom', 'com.oplus.cosa'
            active = BLOAT_EXPECTED[6]
            g.pkg(d1, disabled=True)
            g.pkg(d2, disabled=True)
            g.pkg(active, disabled=True)
            g.pkg(BLOAT_EXPECTED[0])
            g.bloat_rec.write_text(f'{d1}\n{active}\n{d2}\n', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertFalse(g.disabled(d1))
            self.assertFalse(g.disabled(d2))
            self.assertTrue(g.disabled(active))
            self.assertTrue(g.disabled(BLOAT_EXPECTED[0]))
            self.assertEqual(g.bloat_record(), [active, BLOAT_EXPECTED[0]])
            self.assertFalse((g.root / 'data/oneos_oplus_bloat_off.tmp').exists())
            self.assertIn(f'{d1}: turned back on', log)
            self.assertIn(f'{d2}: turned back on', log)

    def test_off_shrinking_list_retains_failed_dropped_entry(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            dropped = 'com.oplus.statistics.rom'
            g.pkg(dropped, disabled=True)
            (g.st / f'fail.{dropped}').write_text('', encoding='utf-8', newline='\n')
            g.bloat_rec.write_text(dropped + '\n', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertTrue(g.disabled(dropped))
            self.assertEqual(g.bloat_record(), [dropped])
            assert_failed_log(self, log, f'{dropped}: pm enable failed: ', 'Error: nope')


class Restrict(unittest.TestCase):
    def test_off_restricts_reads_back_and_reapplies_drifted_bucket(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            g.pkg(GAMES)
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertEqual(g.appop(GAMES), 'ignore')
            self.assertEqual(g.bucket(GAMES), 'restricted')
            self.assertEqual(g.restrict_record(), [GAMES])
            self.assertIn(f'OPPO restricted apps: {GAMES} appop=ignore bucket=restricted', log)

            # Simulate standby bucket drifting back to active between sessions.
            (g.st / f'bucket.{GAMES}').write_text('active\n', encoding='utf-8', newline='\n')
            log2 = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertEqual(g.appop(GAMES), 'ignore')
            self.assertEqual(g.bucket(GAMES), 'restricted')
            self.assertEqual(g.restrict_record(), [GAMES])
            self.assertIn(f'OPPO restricted apps: {GAMES} appop=ignore bucket=restricted', log2)

    def test_off_parses_multiline_appops_outputs(self):
        # Case 1: Uid mode ignore + package allow -> still treated as ignore (owner-restricted).
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            g.pkg(GAMES, appop='Uid mode: RUN_ANY_IN_BACKGROUND: ignore\nRUN_ANY_IN_BACKGROUND: allow\n',
                  bucket='active')
            log = g.run()
            self.assertIn(f'{GAMES}: already restricted', log)
            self.assertEqual(g.calls('cmd'), f'appops get --user 0 {GAMES} RUN_ANY_IN_BACKGROUND\n')
            self.assertEqual(g.calls('am'), f'get-standby-bucket --user 0 {GAMES}\n')
            self.assertIsNone(g.restrict_record())

        # Case 2: Uid mode default + package allow -> not ignore, OneOS restricts it.
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            g.pkg(GAMES, appop='Uid mode: RUN_ANY_IN_BACKGROUND: default\nRUN_ANY_IN_BACKGROUND: allow\n')
            log = g.run()
            self.assertEqual(g.appop(GAMES), 'ignore')
            self.assertEqual(g.bucket(GAMES), 'restricted')
            self.assertEqual(g.restrict_record(), [GAMES])
            self.assertIn(f'OPPO restricted apps: {GAMES} appop=ignore bucket=restricted', log)

    def test_off_leaves_owner_restricted_untouched_and_keep_respects_it(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            g.pkg(GAMES, appop='ignore', bucket='rare')
            log = g.run()
            self.assertIn(f'{GAMES}: already restricted', log)
            self.assertIn(f'OPPO restricted apps: {GAMES} appop=ignore bucket=rare', log)
            self.assertIsNone(g.restrict_record())

            g.script.write_text(
                ('TELE_MODE=off\nBLOAT_MODE=keep\n' + BODY).replace('/data/', f'{g.root.as_posix()}/data/'),
                encoding='utf-8', newline='\n',
            )
            (g.st / 'cmd.calls').unlink()
            (g.st / 'am.calls').unlink()
            log2 = g.run()
            self.assertEqual(g.appop(GAMES), 'ignore')
            self.assertEqual(g.bucket(GAMES), 'rare')
            self.assertEqual(g.calls('cmd'), '')
            self.assertEqual(g.calls('am'), '')
            self.assertIn('OPPO restricted apps: kept, 0 restored, 0 failed', log2)

    def test_off_appops_get_failure_does_not_restrict(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            g.pkg(GAMES)
            (g.st / f'fail.appops.{GAMES}').write_text('', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertIn(f'{GAMES}: cmd appops get failed; not restricting', log)
            self.assertIn(f'OPPO restricted apps: {GAMES} appop=unknown bucket=active', log)
            self.assertNotIn('appops set', g.calls('cmd'))
            self.assertNotIn('set-standby-bucket', g.calls('am'))
            self.assertIsNone(g.restrict_record())

    def test_off_skips_not_installed(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            log = g.run()
            self.assertIn(f'{GAMES}: not installed', log)
            self.assertEqual(g.calls('cmd'), '')
            self.assertEqual(g.calls('am'), '')
            self.assertIsNone(g.restrict_record())

    def test_keep_restores_and_removes_record(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='keep')
            g.pkg(GAMES, appop='ignore', bucket='restricted')
            g.restrict_rec.write_text(GAMES + '\n', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertEqual(g.appop(GAMES), 'default')
            self.assertEqual(g.bucket(GAMES), 'active')
            self.assertIsNone(g.restrict_record())
            self.assertIn(f'{GAMES}: unrestricted (appop=default bucket=active)', log)
            self.assertIn(f'OPPO restricted apps: {GAMES} appop=default bucket=active', log)
            self.assertIn('OPPO restricted apps: kept, 1 restored, 0 failed', log)

    def test_keep_failure_retains_record(self):
        for fail_file in (f'fail.appops.{GAMES}', f'fail.bucket.{GAMES}'):
            with self.subTest(fail_file=fail_file), tempfile.TemporaryDirectory() as d:
                g = Guest(d, bloat_mode='keep')
                g.pkg(GAMES, appop='ignore', bucket='restricted')
                g.restrict_rec.write_text(GAMES + '\n', encoding='utf-8', newline='\n')
                (g.st / fail_file).write_text('', encoding='utf-8', newline='\n')
                log = g.run()
                self.assertEqual(g.assert_exit0, 0)
                self.assertEqual(g.restrict_record(), [GAMES])
                self.assertIn('OPPO restricted apps: kept, 0 restored, 1 failed', log)

    def test_shrinking_list_in_off_and_keep_with_stdin_drain(self):
        # off mode: dropped packages unrestricted and dropped from record while GAMES stays
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            (g.st / 'appops_reads_stdin').write_text('', encoding='utf-8', newline='\n')
            (g.st / 'am_reads_stdin').write_text('', encoding='utf-8', newline='\n')
            d1, d2 = 'com.oplus.dropped1', 'com.oplus.dropped2'
            g.pkg(d1, appop='ignore', bucket='restricted')
            g.pkg(d2, appop='ignore', bucket='restricted')
            g.pkg(GAMES, appop='ignore', bucket='restricted')
            g.restrict_rec.write_text(f'{d1}\n{GAMES}\n{d2}\n', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertEqual(g.appop(d1), 'default')
            self.assertEqual(g.bucket(d1), 'active')
            self.assertEqual(g.appop(d2), 'default')
            self.assertEqual(g.bucket(d2), 'active')
            self.assertEqual(g.appop(GAMES), 'ignore')
            self.assertEqual(g.bucket(GAMES), 'restricted')
            self.assertEqual(g.restrict_record(), [GAMES])
            self.assertIn(f'{d1}: unrestricted (appop=default bucket=active)', log)
            self.assertIn(f'{d2}: unrestricted (appop=default bucket=active)', log)

        # off mode: failed shrinking-list unrestrict retains the dropped entry
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='off')
            dropped = 'com.oplus.dropped1'
            g.pkg(dropped, appop='ignore', bucket='restricted')
            (g.st / f'fail.bucket.{dropped}').write_text('', encoding='utf-8', newline='\n')
            g.restrict_rec.write_text(dropped + '\n', encoding='utf-8', newline='\n')
            g.run()
            self.assertEqual(g.restrict_record(), [dropped])

        # keep mode: restores multiple recorded packages (including dropped) even when cmd/am drain stdin
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, bloat_mode='keep')
            (g.st / 'appops_reads_stdin').write_text('', encoding='utf-8', newline='\n')
            (g.st / 'am_reads_stdin').write_text('', encoding='utf-8', newline='\n')
            dropped = 'com.oplus.dropped1'
            g.pkg(dropped, appop='ignore', bucket='restricted')
            g.pkg(GAMES, appop='ignore', bucket='restricted')
            g.restrict_rec.write_text(f'{dropped}\n{GAMES}\n', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.assert_exit0, 0)
            self.assertEqual(g.appop(dropped), 'default')
            self.assertEqual(g.bucket(dropped), 'active')
            self.assertEqual(g.appop(GAMES), 'default')
            self.assertEqual(g.bucket(GAMES), 'active')
            self.assertIsNone(g.restrict_record())
            self.assertIn('OPPO restricted apps: kept, 2 restored, 0 failed', log)


class SymlinksAndModes(unittest.TestCase):
    def test_symlinked_records_and_tmp_refused_in_both_modes(self):
        records = [
            ('telemetry', 'rec', ONT, 'disable'),
            ('bloat', 'bloat_rec', BLOAT_EXPECTED[0], 'disable'),
            ('restrict', 'restrict_rec', GAMES, 'restrict'),
        ]
        for label, attr, pkg_name, kind in records:
            for target_kind in ('rec', 'tmp'):
                for mode in ('off', 'keep'):
                    with self.subTest(label=label, target=target_kind, mode=mode), \
                            tempfile.TemporaryDirectory() as d:
                        g = Guest(d, mode=mode, bloat_mode=mode)
                        if kind == 'disable':
                            g.pkg(pkg_name, disabled=(mode == 'keep'))
                        else:
                            g.pkg(pkg_name,
                                  appop='ignore' if mode == 'keep' else 'default',
                                  bucket='restricted' if mode == 'keep' else 'active')
                        victim = Path(d) / 'victim'
                        victim_text = f'{pkg_name}\n'
                        victim.write_text(victim_text, encoding='utf-8', newline='\n')
                        rec_path = getattr(g, attr)
                        if target_kind == 'tmp' and mode == 'keep':
                            rec_path.write_text('', encoding='utf-8', newline='\n')
                        link_target = rec_path if target_kind == 'rec' else Path(str(rec_path) + '.tmp')
                        make_symlink(link_target, victim)
                        log = g.run()
                        self.assertEqual(g.assert_exit0, 0)
                        self.assertIn('was a symlink', log)
                        self.assertEqual(victim.read_text(encoding='utf-8'), victim_text)
                        self.assertFalse(link_exists(link_target))
                        if target_kind == 'rec' and mode == 'keep':
                            if kind == 'disable':
                                self.assertTrue(g.disabled(pkg_name))
                            else:
                                self.assertEqual(g.appop(pkg_name), 'ignore')
                                self.assertEqual(g.bucket(pkg_name), 'restricted')

    def test_tele_and_bloat_modes_are_independent(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, mode='keep', bloat_mode='off')
            g.pkg(ONT, disabled=True)
            g.pkg(BLOAT_EXPECTED[0])
            g.pkg(GAMES)
            g.rec.write_text(ONT + '\n', encoding='utf-8', newline='\n')
            g.run()
            self.assertFalse(g.disabled(ONT))
            self.assertTrue(g.disabled(BLOAT_EXPECTED[0]))
            self.assertEqual(g.appop(GAMES), 'ignore')
            self.assertEqual(g.bucket(GAMES), 'restricted')

        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, mode='off', bloat_mode='keep')
            g.pkg(ONT)
            g.pkg(BLOAT_EXPECTED[0], disabled=True)
            g.pkg(GAMES, appop='ignore', bucket='restricted')
            g.bloat_rec.write_text(BLOAT_EXPECTED[0] + '\n', encoding='utf-8', newline='\n')
            g.restrict_rec.write_text(GAMES + '\n', encoding='utf-8', newline='\n')
            g.run()
            self.assertTrue(g.disabled(ONT))
            self.assertFalse(g.disabled(BLOAT_EXPECTED[0]))
            self.assertEqual(g.appop(GAMES), 'default')
            self.assertEqual(g.bucket(GAMES), 'active')


class Prepare(unittest.TestCase):
    def prepare(self, d, coloros, env=None):
        root = Path(d)
        (root / 'stage/data').mkdir(parents=True)
        (root / 'stage/rootfs/system/framework').mkdir(parents=True)
        if coloros:
            (root / 'stage/rootfs/system/framework/oplus-services.jar').write_text('x', encoding='utf-8', newline='\n')
        out = root / 'stage/data/oneos_oplus_telemetry.sh'
        out.write_text('stale\n', encoding='utf-8', newline='\n')
        e = sh_env({
            'STAGE': (root / 'stage').as_posix(),
            'ROOTFS': (root / 'stage/rootfs').as_posix(),
            'BB': 'true',
        })
        e.pop('ONEOS_GUEST_OPLUS_TELEMETRY', None)
        e.pop('ONEOS_GUEST_OPLUS_BLOAT', None)
        e.update(env or {})
        prep_sh = root / 'prepare.sh'
        prep_sh.write_text(BLOCK, encoding='utf-8', newline='\n')
        r = subprocess.run([SH, prep_sh.as_posix()], env=e, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, r.stdout

    def test_written_for_coloros_only(self):
        with tempfile.TemporaryDirectory() as d:
            out, msg = self.prepare(d, True)
            text = out.read_text(encoding='utf-8')
            self.assertTrue(text.startswith('TELE_MODE=off\nBLOAT_MODE=off\n'))
            if os.name == 'nt':
                self.assertIn('chmod 644 "$STAGE/data/oneos_oplus_telemetry.sh"', BLOCK)
            else:
                self.assertEqual(oct(out.stat().st_mode & 0o777), '0o644')
            self.assertIn('telemetry=off (ONEOS_GUEST_OPLUS_TELEMETRY=0), bloat/restrict=off (ONEOS_GUEST_OPLUS_BLOAT=0; oplus_telemetry.log)', msg)
            subprocess.run([SH, '-n', out.as_posix()], env=sh_env(), check=True)
        with tempfile.TemporaryDirectory() as d:
            out, _ = self.prepare(d, False)
            self.assertFalse(out.exists())

    def test_independent_mode_combinations(self):
        for tele_val, tele_mode in (('0', 'off'), ('1', 'keep')):
            for bloat_val, bloat_mode in (('0', 'off'), ('1', 'keep')):
                with self.subTest(tele=tele_val, bloat=bloat_val), tempfile.TemporaryDirectory() as d:
                    out, msg = self.prepare(d, True, {
                        'ONEOS_GUEST_OPLUS_TELEMETRY': tele_val,
                        'ONEOS_GUEST_OPLUS_BLOAT': bloat_val,
                    })
                    text = out.read_text(encoding='utf-8')
                    self.assertTrue(text.startswith(f'TELE_MODE={tele_mode}\nBLOAT_MODE={bloat_mode}\n'))
                    self.assertIn(
                        f'ColorOS OPPO apps: telemetry={tele_mode} (ONEOS_GUEST_OPLUS_TELEMETRY={tele_val}), '
                        f'bloat/restrict={bloat_mode} (ONEOS_GUEST_OPLUS_BLOAT={bloat_val}; oplus_telemetry.log)',
                        msg,
                    )

    def test_symlink_guard_lists_the_script(self):
        self.assertIn('oneos_sysui_compile.sh oneos_oplus_telemetry.sh; do', PREPARE)

    def test_ui_activator_runs_it(self):
        self.assertIn('[ -f \\"$STAGE/data/oneos_oplus_telemetry.sh\\" ]', SWITCH)
        self.assertIn('/system/bin/sh /data/oneos_oplus_telemetry.sh >\\"$STAGE/oplus_telemetry.log\\" 2>&1 </dev/null &', SWITCH)


if __name__ == '__main__':
    unittest.main()
