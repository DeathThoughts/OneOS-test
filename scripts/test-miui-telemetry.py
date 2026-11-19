#!/usr/bin/env python3
"""oneos_miui_telemetry.sh (guest_prepare.sh): Xiaomi's analytics app in HyperOS guests.

Owner's decision of 2026-11-19: com.miui.analytics off for user 0 -> /data/oneos_miui_telemetry_off,
like the ColorOS telemetry apps (scripts/test-oplus-telemetry.py). There is no session log for it
and it has not been tried on a phone; these tests run the generated script against a stub pm only.
ONEOS_GUEST_MIUI_TELEMETRY=1 turns back on only the recorded packages. Joyose, MSA and MiuiDaemon
are not touched.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREPARE = (ROOT / 'switcher/assets/guest_prepare.sh').read_text(encoding='utf-8')
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text(encoding='utf-8')
# The Xiaomi block sits right before the ColorOS block; both are sliced by text markers.
BLOCK = 'rm -f "$STAGE/data/oneos_miui_telemetry.sh"\n' + \
    PREPARE.split('rm -f "$STAGE/data/oneos_miui_telemetry.sh"\n', 1)[1].split(
        "\n# OPPO's trace/log uploaders and Phase 1 bloat/background-CPU apps in ColorOS guests.", 1)[0] + '\n'
BODY = PREPARE.split("<<'MIUITELE'\n", 1)[1].split('\nMIUITELE\n', 1)[0] + '\n'
OPPO_BLOCK = 'rm -f "$STAGE/data/oneos_oplus_telemetry.sh"\n' + \
    PREPARE.split('rm -f "$STAGE/data/oneos_oplus_telemetry.sh"\n', 1)[1].split("\n# The guest's /oem", 1)[0] + '\n'


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


MIA = 'com.miui.analytics'

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
    def __init__(self, d, mode='off'):
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
        self.rec = self.root / 'data/oneos_miui_telemetry_off'
        self.script = self.root / 'tele.sh'
        text = f'TELE_MODE={mode}\n' + BODY
        self.script.write_text(
            text.replace('/data/', f'{self.root.as_posix()}/data/'),
            encoding='utf-8', newline='\n',
        )

    def pkg(self, p, installed=True, disabled=False):
        if installed:
            (self.st / f'installed.{p}').write_text('', encoding='utf-8', newline='\n')
        if disabled:
            (self.st / f'disabled.{p}').write_text('', encoding='utf-8', newline='\n')

    def disabled(self, p):
        return (self.st / f'disabled.{p}').exists()

    def run(self):
        env = sh_env({'ST': self.st.as_posix()}, bin_dir=self.bin)
        r = subprocess.run([SH, self.script.as_posix()], env=env, timeout=60,
                           capture_output=True, text=True)
        self.exit_code = r.returncode
        return r.stdout + r.stderr

    def record(self):
        return self.rec.read_text(encoding='utf-8').splitlines() if self.rec.exists() else None

    def calls(self, tool):
        f = self.st / f'{tool}.calls'
        return f.read_text(encoding='utf-8') if f.exists() else ''


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
        subprocess.run([SH, '-n'], input='TELE_MODE=off\n' + BODY, text=True, env=env, check=True)
        subprocess.run([SH, '-n', (ROOT / 'switcher/assets/guest_prepare.sh').as_posix()], env=env, check=True)

    def test_exact_package_list(self):
        import re
        m = re.search(r'^TELE_PKGS="([^"]*)"', BODY, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).split(), [MIA])
        for p in ('com.xiaomi.joyose', 'com.miui.msa.global', 'com.miui.daemon', 'com.miui.bugreport'):
            self.assertNotIn(p, BODY)

    def test_heredoc_delimiter_is_separate_from_coloros(self):
        self.assertNotIn("<<'TELE'", BLOCK)
        self.assertIn("<<'MIUITELE'", BLOCK)

    def test_every_pm_uses_timeout_and_dev_null(self):
        import re
        checked = 0
        for lineno, raw in enumerate(BODY.splitlines(), 1):
            line = re.sub(r'(?:^|\s)#.*$', '', raw)
            line = re.sub(r'\blog\s+"[^"]*"', '', line)
            if re.search(r'(?:^|[\s;|&({`])(?:timeout\s+\d+\s+)?pm\s+', line):
                checked += 1
                self.assertRegex(line, r'\btimeout\s+\d+\s+pm\b', f'line {lineno}: {raw!r}')
                self.assertIn('</dev/null', line, f'line {lineno}: {raw!r}')
        self.assertGreaterEqual(checked, 3)


class Telemetry(unittest.TestCase):
    def test_off_disables_and_records(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'off'); g.pkg(MIA)
            log = g.run()
            self.assertEqual(g.exit_code, 0)
            self.assertTrue(g.disabled(MIA))
            self.assertIn(f'disable-user --user 0 {MIA}', g.calls('pm'))
            self.assertEqual(g.record(), [MIA])
            self.assertIn('Xiaomi telemetry apps: 1 turned off, 0 failed', log)
            g.run()  # a second session: already off, record not doubled
            self.assertEqual(g.record(), [MIA])

    def test_owner_disabled_is_neither_touched_nor_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'off'); g.pkg(MIA, disabled=True)
            log = g.run()
            self.assertIn(f'{MIA}: already off', log)
            self.assertNotIn('disable-user', g.calls('pm'))
            self.assertIsNone(g.record())

    def test_not_installed_is_logged(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'off')
            log = g.run()
            self.assertEqual(g.exit_code, 0)
            self.assertIn(f'{MIA}: not installed', log)
            self.assertNotIn('disable-user', g.calls('pm'))
            self.assertIsNone(g.record())

    def test_off_failure_is_logged_not_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'off'); g.pkg(MIA)
            (g.st / f'fail.{MIA}').write_text('', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.exit_code, 0)
            assert_failed_log(self, log, f'{MIA}: pm disable-user failed: ', 'Error: nope')
            self.assertIn('0 turned off, 1 failed', log)
            self.assertFalse(g.disabled(MIA))
            self.assertIsNone(g.record())

    def test_keep_enables_only_recorded_and_removes_record(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep')
            g.pkg(MIA, disabled=True); g.pkg('com.other.app', disabled=True)
            g.rec.write_text(MIA + '\ncom.other.app\n', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertEqual(g.exit_code, 0)
            self.assertFalse(g.disabled(MIA))
            self.assertTrue(g.disabled('com.other.app'))
            self.assertIsNone(g.record())
            self.assertIn('Xiaomi telemetry apps: kept, 1 turned back on, 0 failed', log)

    def test_keep_failure_keeps_record(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep'); g.pkg(MIA, disabled=True)
            g.rec.write_text(MIA + '\n', encoding='utf-8', newline='\n')
            (g.st / f'fail.{MIA}').write_text('', encoding='utf-8', newline='\n')
            log = g.run()
            self.assertIn('kept, 0 turned back on, 1 failed', log)
            self.assertEqual(g.record(), [MIA])

    def test_keep_without_record_touches_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d, 'keep'); g.pkg(MIA, disabled=True)
            g.run()
            self.assertTrue(g.disabled(MIA))
            self.assertEqual(g.calls('pm'), '')


class Symlinks(unittest.TestCase):
    def test_symlinked_record_and_tmp_refused_in_both_modes(self):
        for target_kind in ('rec', 'tmp'):
            for mode in ('off', 'keep'):
                with self.subTest(target=target_kind, mode=mode), tempfile.TemporaryDirectory() as d:
                    g = Guest(d, mode)
                    g.pkg(MIA, disabled=(mode == 'keep'))
                    victim = Path(d) / 'victim'
                    victim_text = f'{MIA}\n'
                    victim.write_text(victim_text, encoding='utf-8', newline='\n')
                    if target_kind == 'tmp' and mode == 'keep':
                        g.rec.write_text('', encoding='utf-8', newline='\n')
                    link_target = g.rec if target_kind == 'rec' else Path(str(g.rec) + '.tmp')
                    make_symlink(link_target, victim)
                    log = g.run()
                    self.assertEqual(g.exit_code, 0)
                    self.assertIn('was a symlink', log)
                    self.assertEqual(victim.read_text(encoding='utf-8'), victim_text)
                    self.assertFalse(link_exists(link_target))
                    if mode == 'off':
                        self.assertEqual(g.record(), [MIA])
                    elif target_kind == 'rec':
                        self.assertTrue(g.disabled(MIA))

    def test_symlink_guard_lists_the_script(self):
        # Its own line in the no_symlink list, kept off the ColorOS line so the ColorOS test's
        # exact 'oneos_sysui_compile.sh oneos_oplus_telemetry.sh; do' text still matches.
        loop = PREPARE.split('for s in data misc misc/adb', 1)[1].split('; do\n', 1)[0]
        self.assertIn(' oneos_miui_telemetry.sh ', loop)


class Prepare(unittest.TestCase):
    def prepare(self, d, family, block=BLOCK, env=None, stale=None):
        """family: 'miui' (miui-services.jar), 'oplus' (oplus-services.jar) or None."""
        root = Path(d)
        (root / 'stage/data').mkdir(parents=True)
        fw = root / 'stage/rootfs/system_ext/framework'
        sysfw = root / 'stage/rootfs/system/framework'
        fw.mkdir(parents=True); sysfw.mkdir(parents=True)
        if family == 'miui':
            (fw / 'miui-services.jar').write_text('x', encoding='utf-8', newline='\n')
        elif family == 'oplus':
            (sysfw / 'oplus-services.jar').write_text('x', encoding='utf-8', newline='\n')
        for n in ('oneos_miui_telemetry.sh', 'oneos_oplus_telemetry.sh'):
            (root / 'stage/data' / n).write_text('stale\n', encoding='utf-8', newline='\n')
        e = sh_env({
            'STAGE': (root / 'stage').as_posix(),
            'ROOTFS': (root / 'stage/rootfs').as_posix(),
            'GUEST_SYSTEM_EXT': (root / 'stage/rootfs/system_ext').as_posix(),
            'BB': 'true',
        })
        for k in ('ONEOS_GUEST_MIUI_TELEMETRY', 'ONEOS_GUEST_OPLUS_TELEMETRY', 'ONEOS_GUEST_OPLUS_BLOAT'):
            e.pop(k, None)
        e.update(env or {})
        prep_sh = root / 'prepare.sh'
        prep_sh.write_text(block, encoding='utf-8', newline='\n')
        r = subprocess.run([SH, prep_sh.as_posix()], env=e, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return root / 'stage/data/oneos_miui_telemetry.sh', root / 'stage/data/oneos_oplus_telemetry.sh', r.stdout

    def test_written_for_hyperos_only_and_stale_removed(self):
        with tempfile.TemporaryDirectory() as d:
            out, _, msg = self.prepare(d, 'miui')
            text = out.read_text(encoding='utf-8')
            self.assertTrue(text.startswith('TELE_MODE=off\n'))
            if os.name == 'nt':
                self.assertIn('chmod 644 "$STAGE/data/oneos_miui_telemetry.sh"', BLOCK)
            else:
                self.assertEqual(oct(out.stat().st_mode & 0o777), '0o644')
            self.assertIn('telemetry=off (ONEOS_GUEST_MIUI_TELEMETRY=0; miui_telemetry.log)', msg)
            subprocess.run([SH, '-n', out.as_posix()], env=sh_env(), check=True)
        for family in (None, 'oplus'):
            with tempfile.TemporaryDirectory() as d:
                out, _, _ = self.prepare(d, family)
                self.assertFalse(out.exists())

    def test_keep_mode_from_env(self):
        with tempfile.TemporaryDirectory() as d:
            out, _, msg = self.prepare(d, 'miui', env={'ONEOS_GUEST_MIUI_TELEMETRY': '1'})
            self.assertTrue(out.read_text(encoding='utf-8').startswith('TELE_MODE=keep\n'))
            self.assertIn('telemetry=keep (ONEOS_GUEST_MIUI_TELEMETRY=1; miui_telemetry.log)', msg)

    def test_families_get_only_their_own_script(self):
        # Both blocks run in guest_prepare.sh; run them back to back like the real file does.
        both = BLOCK + OPPO_BLOCK
        with tempfile.TemporaryDirectory() as d:
            miui, oplus, _ = self.prepare(d, 'oplus', block=both)
            self.assertFalse(miui.exists())
            self.assertTrue(oplus.read_text(encoding='utf-8').startswith('TELE_MODE=off\n'))
        with tempfile.TemporaryDirectory() as d:
            miui, oplus, _ = self.prepare(d, 'miui', block=both)
            self.assertTrue(miui.read_text(encoding='utf-8').startswith('TELE_MODE=off\n'))
            self.assertFalse(oplus.exists())

    def test_ui_activator_runs_it(self):
        self.assertIn('[ -f \\"$STAGE/data/oneos_miui_telemetry.sh\\" ]', SWITCH)
        self.assertIn('/system/bin/sh /data/oneos_miui_telemetry.sh >\\"$STAGE/miui_telemetry.log\\" 2>&1 </dev/null &', SWITCH)
        self.assertIn('[UI Activator] Xiaomi telemetry apps: started (miui_telemetry.log)', SWITCH)


if __name__ == '__main__':
    unittest.main()
