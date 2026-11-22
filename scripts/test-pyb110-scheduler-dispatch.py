#!/usr/bin/env python3
"""Exercise the watcher text with a fake OneOS stage and a stub trial worker."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

SWITCH = Path(__file__).resolve().parents[1].joinpath('switcher/assets/oneos_switch.sh').read_text()
TOKEN = 'cdf82045-6a09-4dcc-a8be-b6d838e849d1'


def section(start, stop):
    return SWITCH.split(start, 1)[1].split(stop, 1)[0]


class SchedulerDispatcherTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='pyb110-dispatch-')
        self.addCleanup(temp.cleanup)
        self.stage = Path(temp.name)
        (self.stage / 'tmp').mkdir()
        self.bootui = self.stage / 'data/oneos_bootui'
        self.bootui.mkdir(parents=True)
        self.status = self.bootui / 'scheduler_trial'
        self.request = self.stage / 'data/user/0/com.fahadali.oneos.guest/files/oneos_scheduler_trial'
        self.request.parent.mkdir(parents=True)
        self.marker = self.stage / '.oneos_stage'
        self.marker.write_text('{\n  "image": "coloros17-pyb110",\n}\n')
        self.calls = self.stage / 'calls'
        self.pin = self.stage / 'files.sha256'
        self.pin.write_text(f'{"0" * 64}  pyb110-scheduler-trial.sh\n')
        self.worker = self.stage / 'pyb110-scheduler-trial.sh'
        self.worker.write_text('printf "%s\\n" "$*" >> "$CALLS"\necho "Trial active for 60 seconds"\nexit "$WORKER_EXIT"\n')
        self.busybox = self.stage / 'busybox'
        self.busybox.write_text('#!/bin/sh\nexec "$@"\n')
        self.busybox.chmod(0o755)
        stat = self.stage / 'stat'
        stat.write_text('''#!/bin/sh
if [ "$1:$2" = '-c:%u %A' ]; then
    printf '0 %s\\n' "$(/usr/bin/stat -c %A "$3")"
else
    exec /usr/bin/stat "$@"
fi
''')
        stat.chmod(0o755)
        self.env = {
            **os.environ, 'STAGE': str(self.stage), 'APP': '1',
            'SCHED_DIR': str(self.bootui), 'SCHED_SCRIPT': str(self.worker),
            'GUEST_SCHED_REQ': str(self.request), 'SUMS': str(self.pin),
            'BB': str(self.busybox), 'CALLS': str(self.calls), 'WORKER_EXIT': '0',
            'MODEL': 'SM-S911B', 'ONEOS_PYB110_SCHEDULER_TRIAL': '1',
            'PATH': f'{self.stage}:{os.environ["PATH"]}',
        }
        helpers = 'root_only() {' + section('root_only() {', '[ "$(id -u 2>/dev/null)"') + '''
getprop() { printf '%s\\n' "$MODEL"; }
'''
        init = 'if (' + section(
            '# by an earlier session cannot start a trial. Clear stale status on every boot.\nif (',
            '# OneOS: keep the network usable while the guest boots.',
        )
        init = init.replace('cat /proc/sys/kernel/random/uuid', 'printf %s ' + shlex.quote(TOKEN))
        self.init = helpers + "\nSCHED_TOKEN=''\n" + init

    def invoke(self, action=''):
        nested = "        sched_pid=''" + section("        trial_gen=''\n        sched_pid=''", '        restart_guest() {')
        nested = nested.replace('/system/bin/sh', '/bin/sh')
        # oneos_switch passes the watcher as a double-quoted sh -c string.
        # Reproduce that expansion with the fixture's original paths.
        outer = self.init + '''
if [ -n "$SCHED_TOKEN" ]; then
    printf '%s\\n' "''' + nested + '''" > "$STAGE/nested.sh"
    cat >> "$STAGE/nested.sh" <<'EOF_ACTION'
''' + action + '''
:
EOF_ACTION
    /bin/sh -n "$STAGE/nested.sh" &&
        /bin/sh "$STAGE/nested.sh"
fi
'''
        return subprocess.run(['/bin/sh', '-c', outer], env=self.env, capture_output=True, text=True, timeout=5)

    def test_supported_session_publishes_idle(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status.read_text(), f'{TOKEN} idle\n')

    def test_release_clears_stale_status(self):
        self.status.write_text('old running render\n')
        self.env['ONEOS_PYB110_SCHEDULER_TRIAL'] = '0'
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.status.exists())
        self.assertFalse(self.calls.exists())

    def test_wrong_phone_or_unpinned_stage_does_not_publish_controls(self):
        self.env['MODEL'] = 'SM-S918B'
        self.assertEqual(self.invoke().returncode, 0)
        self.assertFalse(self.status.exists())
        self.env['MODEL'] = 'SM-S911B'
        self.pin.write_text('')
        self.assertEqual(self.invoke().returncode, 0)
        self.assertFalse(self.status.exists())

    def test_dispatch_uses_fixed_args_and_records_worker_exit(self):
        for code, finish in (('0', 'restored'), ('1', 'failed')):
            with self.subTest(code=code):
                self.env['WORKER_EXIT'] = code
                self.request.write_text(f'{TOKEN} launcher-worker1')
                result = self.invoke('''
scheduler_tick
if [ -n "$sched_pid" ]; then
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$sched_pid" 2>/dev/null || break
        sleep .03
    done
    scheduler_tick
fi
''')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls.read_text(), '--run 60 launcher-worker1\n')
                self.assertEqual(self.status.read_text(), f'{TOKEN} {finish} launcher-worker1\n')
                self.calls.unlink()

    def test_invalid_stale_and_oversized_requests_are_inert(self):
        for request in (f'{TOKEN} missing', f'{TOKEN} render extra', f'{TOKEN} render\n', 'stale render', 'x' * 81):
            with self.subTest(request=request):
                self.request.write_text(request)
                result = self.invoke('scheduler_tick')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.calls.exists())
                self.assertEqual(self.status.read_text(), f'{TOKEN} idle\n')

    def test_request_and_status_symlinks_are_not_followed(self):
        fake = self.stage / 'fake'
        fake.write_text(f'{TOKEN} render')
        self.request.symlink_to(fake)
        result = self.invoke('scheduler_tick')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls.exists())
        self.request.unlink()
        self.request.write_text(f'{TOKEN} render')
        result = self.invoke(f'rm -f {self.status}; ln -s {fake} {self.status}; scheduler_tick')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertEqual(fake.read_text(), f'{TOKEN} render')


if __name__ == '__main__':
    unittest.main()
