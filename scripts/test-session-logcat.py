#!/usr/bin/env python3
"""Run the generated session capture and elapsed-report block with stub inputs."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()
RETURN = (ROOT / 'switcher/assets/return_to_oneui.sh').read_text()
CAPTURE = SWITCH[SWITCH.index('LOGS=/data/local/tmp/oneos_logs\n'):SWITCH.index('# Sound and vibration watch')]
HEADER = next(line for line in SWITCH.splitlines() if line.startswith('SESSION_START_UPTIME='))
REPORT = SWITCH.split('# Report elapsed time from the session header, including suspend.\n')[1].split('# Who asked:')[0]


class SessionLogcat(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'tmp').mkdir()

    def capture(self, outcome, filter_on='1'):
        fake = self.root / 'android-logcat'
        fake.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
[ "$2" = all ] || exit 0
case "$OUTCOME" in
    option-error) printf '%0600d\\n' 0 >&2; echo 'invalid buffer selection' >&2; exit 2 ;;
    stopped) kill -TERM "$$" ;;
esac
''')
        fake.chmod(0o700)
        wrong = self.root / 'logcat'
        wrong.write_text('#!/bin/sh\necho wrong-logcat >> "$CALLS"\n')
        wrong.chmod(0o700)
        logs = self.root / 'logs'
        calls = self.root / 'calls'
        env = dict(os.environ, PATH=f'{self.root}:{os.environ["PATH"]}', STAGE=str(self.root),
                   OUTCOME=outcome, CALLS=str(calls), ONEOS_LOGCAT='1', ONEOS_FULL_LOGS='0',
                   ONEOS_LOG_FILTER=filter_on)
        generator = (CAPTURE.replace('/data/local/tmp/oneos_logs', str(logs))
                     .replace('/system/bin/logcat -b', f'{fake} -b'))
        subprocess.run(['sh', '-c', generator], env=env, check=True, capture_output=True)
        self.assertFalse(calls.exists())  # Full session logs is initially off.
        logs.mkdir()
        subprocess.run(['sh', str(self.root / 'logcat_capture.sh')], env=env,
                       check=True, capture_output=True, timeout=5)
        return calls.read_text().splitlines(), (self.root / 'auto_return.log').read_text()

    def test_option_failure_retries_with_filters_and_bounded_error(self):
        calls, log = self.capture('option-error')
        self.assertEqual([line.split()[1] for line in calls], ['all', 'main,system,crash,events,radio'])
        for line in calls:
            self.assertIn('-r 32768 -n 24 QuickStartRUSHelper:S ', line)
            self.assertTrue(line.endswith(' *:V'))
        self.assertIn('exit 2', log)
        self.assertIn('invalid buffer selection', log)
        self.assertNotIn('0' * 513, log)

    def test_stop_does_not_retry_and_filter_can_be_disabled(self):
        calls, log = self.capture('stopped', filter_on='0')
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith('-r 32768 -n 24'))
        self.assertIn('exit 143', log)

    def test_discovery_and_stop_match_both_logcat_invocations(self):
        pattern = next(line.split('"')[1] for line in SWITCH.splitlines() if line.startswith('LOGCAT_PROCESS='))
        pattern = pattern.replace('$LOGS', '/data/local/tmp/oneos_logs')
        self.assertIn(f"pkill -f '{pattern}'", RETURN)
        for cmd in ('logcat', '/system/bin/logcat'):
            self.assertRegex(f'{cmd} -b all -f /data/local/tmp/oneos_logs/boot.log -r 32768', pattern)
        self.assertIsNone(re.search(pattern, 'logcat -f /data/local/tmp/oneos_logs/boot.log.01'))

    def test_elapsed_uses_boot_clock_or_falls_back_to_watcher_count(self):
        start, now = self.root / 'start', self.root / 'now'
        for start_value, now_value, expected in (
            ('1200.01 0.00', '5830.99 0.00', 4630),
            ('1200.01 0.00', '1200.99 0.00', 0),
            ('1200.01 0.00', '1199.99 0.00', 590),
            ('1200.01 0.00', '5830.invalid', 590),
            (None, '5830.99 0.00', 590),
            ('1200.01 0.00', None, 590),
        ):
            with self.subTest(start=start_value, now=now_value):
                for path, value in ((start, start_value), (now, now_value)):
                    if value is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_text(value + '\n')
                script = (HEADER.replace('/proc/uptime', str(start))
                          + '\nsh -c "total=590; req=shutdown;\n'
                          + REPORT.replace('/proc/uptime', str(now)) + '"')
                result = subprocess.run(['sh', '-c', script], check=True, capture_output=True, text=True)
                self.assertIn(f"guest requested 'shutdown' after {expected}s, returning to One UI", result.stdout)


if __name__ == '__main__':
    unittest.main()
