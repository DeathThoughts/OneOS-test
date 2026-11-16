#!/usr/bin/env python3
"""oneos_sysui_compile.sh (guest_prepare.sh): SystemUI, then launcher, Settings and PermissionController.

S23 SM-S911B, ColorOS 17 (2026-11-15): SystemUI went from verify to speed in
43 s, while Home, Settings and the permission dialogs stayed verify-only. The
script now compiles those three after SystemUI with speed, one at a time, one
marker line per APK, and stops when the guest's thermal status is 3 or more.
speed, not speed-profile: a session has no useful profile, so speed-profile
compiled next to nothing (S25 Ultra session 25b4785c4206828f, Madox 4fad5d45:
OplusLauncher had no odex at all); an app at speed-profile is compiled again.
This runs the real heredoc with stubs for pm, cmd, getprop, dumpsys, sha256sum,
sleep and timeout, under a temporary root for /data and /system.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'switcher/assets/guest_prepare.sh').read_text()
SCRIPT = SOURCE.split("<<'SYSUI'\n", 1)[1].split('\nSYSUI\n', 1)[0] + '\n'

APPS = {
    'com.android.systemui': '/system/system_ext/priv-app/SystemUI/SystemUI.apk',
    'com.android.launcher': '/system/system_ext/priv-app/Launcher/Launcher.apk',
    'com.android.settings': '/system/system_ext/priv-app/Settings/Settings.apk',
    'com.google.android.permissioncontroller': '/data/app/~~x/com.google.android.permissioncontroller-y/base.apk',
}

STUBS = {
    # timeout N cmd...: run cmd
    'timeout': 'shift; exec "$@"\n',
    'sleep': 'exit 0\n',
    'getprop': 'case "$1" in sys.boot_completed) echo 1 ;; dalvik.vm.systemuicompilerfilter) cat "$ST/sysui_filter" 2>/dev/null ;; esac\n',
    'sha256sum': 'echo "$(cat "$1")  $1"\n',
    'dumpsys': '[ "$1" = thermalservice ] && [ -f "$ST/thermal" ] && { echo "IsStatusOverride: false"; echo "Thermal Status: $(cat "$ST/thermal")"; }\nexit 0\n',
    'pm': (
        'case "$1 $2" in\n'
        '"art dump") s=$(cat "$ST/status.$3" 2>/dev/null) || exit 1\n'
        '    echo "[$3]"; echo "  path: x"; echo "    arm64: [status=$s] [reason=install]" ;;\n'
        '"path "*) [ -f "$ST/path.$2" ] && echo "package:$(cat "$ST/path.$2")" ;;\n'
        'esac\n'
    ),
    'cmd': (
        '[ "$1 $2" = "package compile" ] || exit 1\n'
        'p=$6; echo "$p $4" >> "$ST/compiles"\n'
        'if [ -f "$ST/fail.$p" ]; then echo "Failure: dex2oat"; exit 1; fi\n'
        'echo "$4" > "$ST/status.$p"; echo Success\n'
    ),
}


class Guest:
    def __init__(self, d):
        self.root = Path(d)
        self.st = self.root / 'st'
        self.bin = self.root / 'bin'
        for p in (self.st, self.bin, self.root / 'data/dalvik-cache/arm64'):
            p.mkdir(parents=True)
        for n, body in STUBS.items():
            f = self.bin / n
            f.write_text('#!/bin/sh\n' + body)
            f.chmod(0o755)
        self.script = self.root / 'compile.sh'
        self.script.write_text(SCRIPT.replace('/data/', f'{d}/data/').replace('/system/', f'{d}/system/'))

    def app(self, pkg, status, apk=None):
        path = self.root / (apk or APPS[pkg]).lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'sha-{pkg}')
        (self.st / f'path.{pkg}').write_text(str(path))
        (self.st / f'status.{pkg}').write_text(status)
        if pkg == 'com.android.systemui':
            (self.root / 'data/dalvik-cache/arm64/system@system_ext@priv-app@SystemUI@SystemUI.apk@classes.dex').write_text('x')

    def run(self):
        env = dict(os.environ, ST=str(self.st), PATH=f'{self.bin}:{os.environ["PATH"]}')
        subprocess.run(['/bin/sh', str(self.script)], env=env, check=True, timeout=60)
        return (self.root / 'data/oneos_sysui_compile.log').read_text()

    def compiles(self):
        f = self.st / 'compiles'
        return f.read_text().split('\n')[:-1] if f.exists() else []

    def amark(self):
        f = self.root / 'data/oneos_app_compiled'
        return f.read_text() if f.exists() else ''


def full(g, status='verify'):
    for p in APPS:
        g.app(p, status)


class AppCompile(unittest.TestCase):
    def test_script_parses(self):
        subprocess.run(['/bin/sh', '-n', '-c', SCRIPT], check=True)

    def test_verify_compiles_all_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d); full(g)
            log = g.run()
            self.assertEqual(g.compiles(), [
                'com.android.systemui speed',
                'com.android.launcher speed',
                'com.android.settings speed',
                'com.google.android.permissioncontroller speed',
            ])
            self.assertIn('SystemUI arm64 now speed', log)
            self.assertIn('com.android.launcher arm64 now speed\n', log)
            for p in list(APPS)[1:]:
                self.assertIn(f'{p} apk=sha-{p} boot=image filter=speed\n', g.amark())

    def test_marker_match_skips(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d); full(g)
            g.run()
            full(g)  # back to verify, same APKs: the markers still match
            (g.st / 'compiles').unlink()
            log = g.run()
            self.assertEqual(g.compiles(), [])
            self.assertIn('SystemUI compiled on an earlier session', log)
            self.assertIn('com.android.settings compiled on an earlier session', log)
            # a new launcher APK compiles that one only
            (g.root / APPS['com.android.launcher'].lstrip('/')).write_text('new')
            g.run()
            self.assertEqual(g.compiles(), ['com.android.launcher speed'])
            self.assertEqual(g.amark().count('com.android.launcher '), 1)
            self.assertIn('com.android.launcher apk=new boot=image filter=speed', g.amark())

    def test_speed_skips(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d); full(g, 'speed')
            log = g.run()
            self.assertEqual(g.compiles(), [])
            self.assertIn('com.android.launcher arm64 is speed already', log)
            self.assertIn('com.android.launcher apk=sha-com.android.launcher boot=image filter=speed', g.amark())

    def test_speed_profile_compiles_again(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d); full(g, 'speed-profile')
            g.app('com.android.systemui', 'speed')
            log = g.run()
            self.assertEqual(g.compiles(), [
                'com.android.launcher speed',
                'com.android.settings speed',
                'com.google.android.permissioncontroller speed',
            ])
            self.assertIn('com.android.launcher arm64 is speed-profile (apk=sha-com.android.launcher '
                          'boot=image filter=speed): cmd package compile -m speed -f com.android.launcher', log)

    def test_old_speed_profile_marker_compiles_once_more(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d); full(g, 'speed-profile')
            g.app('com.android.systemui', 'speed')
            # a line from the earlier speed-profile step: no filter in its key
            (g.root / 'data/oneos_app_compiled').write_text(
                ''.join(f'{p} apk=sha-{p} boot=image\n' for p in list(APPS)[1:]))
            g.run()
            self.assertEqual(len(g.compiles()), 3)
            for p in list(APPS)[1:]:
                self.assertEqual([l for l in g.amark().split('\n') if l.startswith(p + ' ')],
                                 [f'{p} apk=sha-{p} boot=image filter=speed'])
                self.assertIn(f'{p} apk=sha-{p} boot=image filter=speed\n', g.amark())

    def test_thermal_postpones(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d); full(g)
            (g.st / 'thermal').write_text('3')
            log = g.run()
            # SystemUI's part is unchanged; the apps wait
            self.assertEqual(g.compiles(), ['com.android.systemui speed'])
            self.assertIn('postponed', log)
            self.assertEqual(g.amark(), '')
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d); full(g)
            (g.st / 'thermal').write_text('2')
            g.run()
            self.assertEqual(len(g.compiles()), 4)

    def test_missing_package_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d)
            g.app('com.android.systemui', 'speed')
            g.app('com.android.settings', 'verify')
            g.app('com.android.permissioncontroller', 'verify', '/system/priv-app/PermissionController/PermissionController.apk')
            log = g.run()
            self.assertIn('com.android.launcher: not installed: skipped', log)
            self.assertEqual(g.compiles(), ['com.android.settings speed',
                                            'com.android.permissioncontroller speed'])

    def test_failure_leaves_no_marker(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d); full(g)
            (g.st / 'fail.com.android.settings').write_text('')
            log = g.run()
            self.assertIn('com.android.settings: no marker', log)
            self.assertNotIn('com.android.settings ', g.amark())
            self.assertIn('com.android.launcher ', g.amark())
            self.assertEqual(len(g.compiles()), 4)

    def test_systemui_path_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            g = Guest(d); full(g, 'run-from-apk')
            (g.st / 'sysui_filter').write_text('everything')
            log = g.run()
            self.assertEqual(g.compiles()[0], 'com.android.systemui everything')
            self.assertIn('SystemUI arm64 is run-from-apk (apk=sha-com.android.systemui boot=image): '
                          'cmd package compile -m everything -f com.android.systemui', log)
            self.assertIn('marker saved: later sessions skip this', log)
            self.assertEqual((g.root / 'data/oneos_sysui_compiled').read_text(),
                             'apk=sha-com.android.systemui boot=image\n')
            self.assertNotIn('systemui', g.amark())


if __name__ == '__main__':
    unittest.main()
