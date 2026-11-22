#!/usr/bin/env python3
"""Execute the scheduler trial against a fake /proc and relative Toybox renice."""
import os
from pathlib import Path
import shlex
import signal
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).with_name('pyb110-scheduler-trial.sh').read_text()


class SchedulerTrialTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix='pyb110-trial-')
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.stage = self.root / 'stage'
        self.stage.mkdir()
        self.guest_root = self.stage / 'rootfs'
        self.guest_root.mkdir()
        self.host_root = self.root / 'host'
        self.host_root.mkdir()
        self.marker = self.stage / '.oneos_stage'
        self.marker.write_text('{\n  "image": "coloros17-pyb110",\n  "set": "pyb110-s23"\n}\n')
        (self.stage / 'guest_mntns').write_text('mnt:[20]\n')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.stub('id', 'echo 0')
        self.stub('stat', 'if [ "$1:$2" = -c:%u ]; then echo 0; else exec /usr/bin/stat "$@"; fi')
        self.level = self.root / 'anim_level'
        self.level.write_text('1\n')
        self.stub('getprop', '''case "$1" in
    ro.product.model) echo SM-S911B ;;
    persist.sys.oplus.anim_level) cat "$LEVEL" ;;
    *) exit 42 ;;
esac''')
        self.stub('pidof', 'echo "100 200"')
        self.stub('sleep', ':')
        self.log = self.root / 'renice.log'
        self.stub('toybox', """[ "$1" = renice ] && [ "$2" = -n ] && [ "$4" = -p ] &&
    [ "$5" = "${TASK##*/}" ] || exit 42
printf '%s\\n' "$3 $5" >> "$LOG"
awk -v delta="$3" '{$19 += delta; print}' "$TASK/stat" > "$TASK/stat.next"
mv "$TASK/stat.next" "$TASK/stat"
""")
        self.proc = self.root / 'proc'
        self.ns(self.proc / 'self', 10)
        self.ns(self.proc / '1', 10)
        self.host = self.create_process(100, 10, self.host_root)
        self.guest = self.create_process(200, 30, self.guest_root)
        self.task = self.create_thread(201, -4, 420)
        self.script = self.root / 'run-trial.sh'
        self.script.write_text(
            SOURCE.replace('PATH=/system/bin', 'PATH=' + shlex.quote(f'{self.bin}:/usr/bin:/bin'))
            .replace('/system/bin/toybox ', shlex.quote(str(self.bin / 'toybox')) + ' ')
            .replace('/data/local/tmp/oneos', str(self.stage))
            .replace('/proc/', f'{self.proc}/')
        )
        self.env = {
            **os.environ, 'LOG': str(self.log), 'TASK': str(self.task),
            'LEVEL': str(self.level), 'HOST_ROOT': str(self.host_root), 'GUEST_ROOT': str(self.guest_root),
        }

    def stub(self, name, source):
        stub = self.bin / name
        stub.write_text('#!/bin/sh\n' + source + '\n')
        stub.chmod(0o755)

    @staticmethod
    def ns(procdir, namespace):
        (procdir / 'ns').mkdir(parents=True, exist_ok=True)
        path = procdir / 'ns' / 'mnt'
        path.unlink(missing_ok=True)
        path.symlink_to(f'mnt:[{namespace}]')

    @staticmethod
    def statfile(procdir, tid, nice, start, policy=0, comm='RenderThread'):
        # Linux stat has 52 fields. Fields 1 and 2 are PID and comm.
        fields = ['0'] * 52
        fields[0], fields[1], fields[2] = str(tid), f'({comm})', 'S'
        fields[18], fields[21], fields[40] = str(nice), str(start), str(policy)
        (procdir / 'stat').write_text(' '.join(fields) + '\n')

    def create_process(self, pid, namespace, root):
        procdir = self.proc / str(pid)
        self.ns(procdir, namespace)
        (procdir / 'root').symlink_to(root)
        (procdir / 'cmdline').write_bytes(b'com.android.systemui\0')
        self.statfile(procdir, pid, 0, 204, comm='ndroid.systemui')
        return procdir

    def create_thread(self, tid, nice, start, policy=0):
        task = self.guest / 'task' / str(tid)
        self.ns(task, 30)
        (task / 'comm').write_text('RenderThread\n')
        self.statfile(task, tid, nice, start, policy)
        return task

    def run_trial(self, *args):
        return subprocess.run(['/bin/sh', self.script, *args], env=self.env, capture_output=True, text=True, timeout=5)

    def assert_refused(self, message, *args):
        result = self.run_trial(*args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.log.exists())

    def test_check_reads_only(self):
        result = self.run_trial('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PID=200 TID=201 nice=-4', result.stdout)
        self.assertFalse(self.log.exists())

    def test_run_applies_relative_delta_and_restores(self):
        result = self.run_trial('--run', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['-6 201', '6 201'])
        self.assertEqual((self.task / 'stat').read_text().split()[18], '-4')
        self.assertIn('Restored', result.stdout)

    def test_refuses_non_init_namespace(self):
        self.ns(self.proc / 'self', 20)
        self.assert_refused("init's mount namespace", '--run', '1')

    def test_refuses_mismatched_guest_namespace(self):
        self.ns(self.task, 10)
        self.assert_refused('identity changed', '--run', '1')

    def test_refuses_host_root_in_non_host_namespace(self):
        (self.guest / 'root').unlink()
        (self.guest / 'root').symlink_to(self.host_root)
        self.assert_refused('no com.android.systemui', '--run', '1')

    def test_refuses_host_namespace_with_matching_root(self):
        self.ns(self.guest, 10)
        self.assert_refused('no com.android.systemui', '--run', '1')

    def test_refuses_wrong_image(self):
        self.marker.write_text('{\n  "image": "coloros17-pyb110-extra",\n}\n')
        self.assert_refused('PYB110 is not staged', '--run', '1')

    def test_refuses_non_baseline_nice(self):
        self.create_thread(201, -3, 420)
        self.assert_refused('observed policy:nice 0:-3', '--run', '1')

    def test_refuses_non_cfs_policy(self):
        self.create_thread(201, -4, 420, policy=2)
        self.assert_refused('observed policy:nice 2:-4', '--run', '1')

    def test_refuses_ambiguous_renderthreads(self):
        self.create_thread(202, -4, 421)
        self.assert_refused('more than one SystemUI RenderThread', '--run', '1')

    def test_refuses_unbounded_trial(self):
        self.assert_refused('duration must be 1 through 120', '--run', '121')

    def test_check_shows_level_three_but_run_refuses_it(self):
        self.level.write_text('3\n')
        check = self.run_trial('--check')
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn('animation_level=3', check.stdout)
        self.assert_refused('activate ColorOS animation level 1', '--run', '1')

    def test_does_not_restore_reused_tid(self):
        self.stub('sleep', """awk '{$22++; print}' "$TASK/stat" > "$TASK/stat.next"
mv "$TASK/stat.next" "$TASK/stat"
""")
        result = self.run_trial('--run', '1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('thread changed or disappeared', result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['-6 201'])

    def test_does_not_restore_after_guest_root_substitution(self):
        self.stub('sleep', 'mv "$GUEST_ROOT" "$GUEST_ROOT.old" && mkdir "$GUEST_ROOT"')
        result = self.run_trial('--run', '1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('thread changed or disappeared', result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['-6 201'])

    def test_restores_if_only_animation_level_changes(self):
        self.stub('sleep', 'echo 3 > "$LEVEL"')
        result = self.run_trial('--run', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['-6 201', '6 201'])

    def test_main_trial_changes_only_the_process_leader(self):
        leader = self.create_thread(200, 0, 204)
        (leader / 'comm').write_text('ndroid.systemui\n')
        self.env['TASK'] = str(leader)
        result = self.run_trial('--run', '1', 'main')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('SystemUI main thread PID=200 TID=200', result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), ['-10 200', '10 200'])
        self.assertEqual((self.task / 'stat').read_text().split()[18], '-4')

    def test_refuses_leader_starttime_mismatch(self):
        self.create_thread(200, 0, 205)
        self.assert_refused('identity changed', '--run', '1', 'main')

    def test_refuses_leader_namespace_mismatch(self):
        leader = self.create_thread(200, 0, 204)
        self.ns(leader, 10)
        self.assert_refused('identity changed', '--run', '1', 'main')

    def test_refuses_multiple_modes(self):
        self.assert_refused('usage:', '--run', '1', 'main', 'render')

    def test_does_not_overwrite_a_later_policy_change(self):
        self.stub('sleep', """awk '{$19 = -8; print}' "$TASK/stat" > "$TASK/stat.next"
mv "$TASK/stat.next" "$TASK/stat"
""")
        result = self.run_trial('--run', '1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('another policy changed', result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['-6 201'])

    def test_term_restores_priority(self):
        self.stub('sleep', 'exec /usr/bin/sleep 3')
        trial = subprocess.Popen(
            ['/bin/sh', self.script, '--run', '1'],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertIn('Verified', trial.stdout.readline())
            self.assertIn('Trial active', trial.stdout.readline())
            trial.send_signal(signal.SIGTERM)
            stdout, stderr = trial.communicate(timeout=5)
        finally:
            if trial.poll() is None:
                trial.kill()
                trial.communicate()
        self.assertEqual(trial.returncode, 143, stderr)
        self.assertIn('Restored', stdout)
        self.assertEqual(self.log.read_text().splitlines(), ['-6 201', '6 201'])


if __name__ == '__main__':
    unittest.main()
