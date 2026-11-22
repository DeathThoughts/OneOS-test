#!/usr/bin/env python3
"""The session's logcat capture silences noise tags through one shared LOGCAT_FILTER.

S25 Ultra, ColorOS 17 guest, sessions 22343aacdd4e1d6c, 161a843fbaddcd7a, f8c0108ef1bea94a: 210-420k lines/min
filled a 32 MB file in 30-60 s. Both capture commands (the session's and the guest-app mid-session one) must use
the same variable, nothing used for diagnosis may be silenced, return_to_oneui.sh's pkill pattern must still match
and ONEOS_LOG_FILTER=0 must drop the specs. The assignment is cut out of oneos_switch.sh and run under sh.
"""
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text(encoding='utf-8')
RETURN = (ROOT / 'switcher/assets/return_to_oneui.sh').read_text(encoding='utf-8')
SH = '/bin/sh' if os.path.exists('/bin/sh') else shutil.which('sh')
LOGS = '/data/local/tmp/oneos_logs'

ASSIGN = re.search(r'^LOGCAT_FILTER="(?:[^"\\]|\\.|\\\n)*"\nif \[ "\$\{ONEOS_LOG_FILTER:-1\}" = 0 \]; then LOGCAT_FILTER=""; fi\n',
                   SWITCH, re.M).group(0)
CMD1 = re.search(r'^\s*"(logcat -b all [^"\n]*boot\.log[^"\n]*)" \\$', SWITCH, re.M).group(1)
CMD2 = re.search(r'\$BB setsid (logcat -b all [^\n]*boot\.log[^\n]*?) </dev/null', SWITCH).group(1)
# 0.5.28 (merged from upstream): the capture script's commands send their error to a file for the retry on the
# standard buffers (and a third, the retry itself, uses the same variable); the words are compared without it.
CMD1 = CMD1.replace(' 2>$STAGE/logcat_capture.err', '')
RETRY = re.findall(r'"(logcat -b main,system,crash,events,radio [^"\n]*boot\.log[^"\n]*)"', SWITCH)

PROTECTED = ['MediaProvider', 'StorageManager', 'ModernMediaScanner', 'OneOSMediaHold', 'AndroidRuntime',
             'ActivityManager', 'ActivityTaskManager', 'WindowManager', 'DEBUG', 'libc', 'SystemUi--Keyguard',
             'SystemUi--Aod', 'KeyguardUpdateMonitor', 'CameraService', 'vold', 'init', 'zygote']
SILENCED = ['QuickStartRUSHelper', 'SettingsState', 'OplusGcSupressionHelper', 'minksocket', 'SurfaceControl',
            'DigitalClock', 'UniRenderCanvasContext', 'BackgroundBlurDrawable', 'Blur_WindowBlurSessionImpl',
            'Blur_ScreenCapture', 'BlurApp', 'SQLiteLog', 'Finsky', 'SG', 'OplusRequestedLayerState',
            'OplusStartingWindowManager', 'OverlayManager', 'OplusAppDataMigrateManager', 'ULELite', 'Region',
            'HYPER-HAL']


def render(command, knob=None):
    """The command as the capture's shell sees it after the outer expansion, run for its words."""
    env = {'PATH': os.environ.get('PATH', '')}
    if knob is not None:
        env['ONEOS_LOG_FILTER'] = knob
    script = ASSIGN + 'LOGS=%s\nprintf "%%s\\n" "%s"\n' % (LOGS, command)
    out = subprocess.run([SH, '-c', script], env=env, capture_output=True, text=True, check=True).stdout.strip()
    return out


def words(rendered):
    """Words the way sh splits the generated script's line (quotes honoured, no globbing)."""
    return subprocess.run([SH, '-c', 'set -f; for w in %s; do echo "$w"; done' % rendered],
                          capture_output=True, text=True, check=True).stdout.split('\n')[:-1]


class Filter(unittest.TestCase):
    def test_both_commands_use_the_shared_variable(self):
        self.assertIn('$LOGCAT_FILTER', CMD1)
        self.assertIn('$LOGCAT_FILTER', CMD2)
        self.assertEqual(len(re.findall(r'^LOGCAT_FILTER="[A-Za-z]', SWITCH, re.M)), 1)
        self.assertEqual(SWITCH.count('-r 32768 -n 24'), 3)
        self.assertEqual(len(RETRY), 1)
        self.assertIn('-n 24 $LOGCAT_FILTER 2>', RETRY[0])
        for cmd in (CMD1, CMD2):
            self.assertTrue(cmd.endswith('-n 24 $LOGCAT_FILTER'), cmd)

    def test_silenced_and_protected_tags(self):
        specs = words(render(CMD1))
        self.assertEqual(specs[-1], '*:V')
        silenced = {w[:-2] for w in specs if w.endswith(':S')}
        self.assertEqual(silenced, set(SILENCED))
        for tag in PROTECTED:
            self.assertNotIn(tag, silenced)
        for w in silenced:
            self.assertFalse(re.match(r'(OneOS|sec_|Biometric|Fingerprint|Face|Camera3-|audio)', w, re.I), w)

    def test_rendered_command(self):
        self.assertEqual(
            render(CMD1).split(' -n 24 ')[0],
            'logcat -b all -v threadtime -f %s/boot.log -r 32768' % LOGS)
        self.assertEqual(render(CMD1), render(CMD2))
        self.assertNotIn('\n', render(CMD1))

    def test_return_pkill_pattern_still_matches(self):
        m = re.search(r"pkill -f '(\^logcat \.\*/data/local/tmp/oneos_logs/boot\.log)'", RETURN)
        self.assertIsNotNone(m)
        for cmd in (CMD1, CMD2):
            self.assertTrue(re.search(m.group(1), render(cmd)), render(cmd))

    def test_knob_off_drops_the_specs(self):
        for cmd in (CMD1, CMD2):
            self.assertNotIn(':S', render(cmd, '0'))
            self.assertNotIn('*:V', render(cmd, '0'))
            self.assertIn(':S', render(cmd, '1'))

    def test_knob_is_validated(self):
        self.assertIn('case "${ONEOS_LOG_FILTER:-1}" in 0|1)', SWITCH)


if __name__ == '__main__':
    unittest.main()
