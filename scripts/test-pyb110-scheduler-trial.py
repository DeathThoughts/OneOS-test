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
        self.guest_bootui = self.guest_root / 'data/oneos_bootui'
        self.guest_bootui.mkdir(parents=True)
        self.guest_marker = self.guest_bootui / 'anim_level'
        self.build_prop = self.guest_root / 'system_ext/etc/build.prop'
        self.build_prop.parent.mkdir(parents=True)
        self.build_prop.touch()
        self.host_root = self.root / 'host'
        self.host_root.mkdir()
        self.marker = self.stage / '.oneos_stage'
        self.marker.write_text('{\n  "image": "coloros17-pyb110",\n  "set": "pyb110-s23"\n}\n')
        (self.stage / 'guest_mntns').write_text('mnt:[20]\n')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.stub('id', 'echo 0')
        self.stub('stat', '''if [ "$1:$2" = -c:%u ]; then
    echo 0
elif [ "$3" = / ]; then
    exec /usr/bin/stat "$1" "$2" "$GUEST_ROOT"
else
    exec /usr/bin/stat "$@"
fi''')
        self.stub('sha256sum', '''[ "$1" = "$BUILD_PROP" ] || exit 42
printf '%s  %s\n' "$SYSTEM_EXT_SHA" "$1"
''')
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
            .replace('/data/oneos_bootui', str(self.guest_bootui))
            .replace('/system_ext/etc/build.prop', str(self.build_prop))
            .replace('/proc/', f'{self.proc}/')
        )
        self.env = {
            **os.environ, 'LOG': str(self.log), 'TASK': str(self.task),
            'LEVEL': str(self.level), 'HOST_ROOT': str(self.host_root), 'GUEST_ROOT': str(self.guest_root),
            'BUILD_PROP': str(self.build_prop),
            'SYSTEM_EXT_SHA': '615b41e1ada996ed8f042c54d8b3bfb4ab0119fb5b44ce5015f5ad9c62e6f16e',
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

    def test_refuses_non_init_namespace_without_guest_marker(self):
        self.ns(self.proc / 'self', 20)
        self.assert_refused('missing root-owned OneOS guest animation marker', '--run', '1')

    def configure_guest_root(self):
        self.ns(self.proc / 'self', 20)
        self.guest_marker.write_text('1\n')
        self.marker.unlink()
        (self.stage / 'guest_mntns').unlink()

    def test_guest_root_does_not_need_the_host_stage(self):
        self.configure_guest_root()
        result = self.run_trial('--run', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['-6 201', '6 201'])

    def test_guest_root_works_with_unreadable_init_namespace(self):
        self.configure_guest_root()
        (self.proc / '1/ns/mnt').unlink()
        result = self.run_trial('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())

    def test_guest_root_rejects_a_mismatched_image(self):
        self.configure_guest_root()
        self.env['SYSTEM_EXT_SHA'] = '0' * 64
        self.assert_refused('unexpected PYB110 image fingerprint', '--run', '1')

    def test_guest_root_rejects_a_linked_marker(self):
        self.configure_guest_root()
        self.guest_marker.unlink()
        self.guest_marker.symlink_to(self.level)
        self.assert_refused('missing root-owned OneOS guest animation marker', '--run', '1')

    def test_guest_root_rejects_host_namespace_and_root(self):
        self.configure_guest_root()
        self.ns(self.guest, 10)
        self.assert_refused('no com.android.systemui', '--run', '1')
        self.ns(self.guest, 30)
        (self.guest / 'root').unlink()
        (self.guest / 'root').symlink_to(self.host_root)
        self.assert_refused('no com.android.systemui', '--run', '1')

    def test_guest_root_requires_live_full_effects_to_write(self):
        self.configure_guest_root()
        self.level.write_text('3\n')
        self.assert_refused('activate ColorOS animation level 1', '--run', '1')

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
        self.env['pin'] = '201:420'
        result = self.run_trial('--check', 'render')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('more than one SystemUI RenderThread', result.stderr)
        self.assertIn('TID=201 start=420 nice=-4 policy=0', result.stderr)
        self.assertIn('TID=202 start=421 nice=-4 policy=0', result.stderr)
        self.assertFalse(self.log.exists())

    def test_render_ignores_duplicates_with_wrong_policy_nice_or_namespace(self):
        self.create_thread(202, -4, 421, policy=2)
        self.create_thread(203, -3, 422)
        foreign = self.create_thread(206, -4, 423)
        self.ns(foreign, 10)
        result = self.run_trial('--check', 'render')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('TID=201', result.stdout)
        self.assertFalse(self.log.exists())
        result = self.run_trial('--run', '1', 'render')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['-6 201', '6 201'])

    def test_render_pin_checks_then_changes_only_the_selected_worker(self):
        second = self.create_thread(202, -4, 421)
        self.env['TASK'] = str(second)
        check = self.run_trial('--check', 'render', '202:421')
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn('TID=202', check.stdout)
        self.assertFalse(self.log.exists())
        result = self.run_trial('--run', '1', 'render', '202:421')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['-6 202', '6 202'])
        self.assertEqual((self.task / 'stat').read_text().split()[18], '-4')

    def test_render_pin_refuses_stale_identity_and_other_process(self):
        self.create_thread(202, -4, 421)
        host_thread = self.host / 'task' / '101'
        self.ns(host_thread, 10)
        (host_thread / 'comm').write_text('RenderThread\n')
        self.statfile(host_thread, 101, -4, 419)
        for pin in ('202:424', '101:419'):
            with self.subTest(pin=pin):
                self.assert_refused('render pin does not identify', '--run', '1', 'render', pin)

    def test_render_requires_a_pin_with_tid_and_start_ticks(self):
        for pin in ('', '201', '201:', '201:420:1'):
            with self.subTest(pin=pin):
                self.assert_refused('render', '--check', 'render', pin)

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
        self.assert_refused('pin is only valid after render', '--run', '1', 'main', 'render')

    def configure_surfaceflinger(self):
        for process, root in ((self.host, self.host_root), (self.guest, self.guest_root)):
            (process / 'cmdline').write_bytes(b'/system/bin/surfaceflinger\0')
            executable = root / 'system/bin/surfaceflinger'
            executable.parent.mkdir(parents=True)
            executable.touch()
            (process / 'exe').symlink_to(executable)
        (self.task / 'comm').write_text('UniRenderEngine\n')
        self.statfile(self.task, 201, -10, 420, comm='UniRenderEngine')

    def test_unirender_trial_changes_only_the_engine(self):
        self.configure_surfaceflinger()
        worker = self.create_thread(202, 0, 421, policy=1)
        (worker / 'comm').write_text('UniRenderThread\n')
        result = self.run_trial('--run', '1', 'unirender-engine')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('SurfaceFlinger UniRenderEngine PID=200 TID=201', result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), ['-2 201', '2 201'])
        self.assertEqual((worker / 'stat').read_text().split()[18], '0')

    def test_refuses_surfaceflinger_running_the_wrong_executable(self):
        self.configure_surfaceflinger()
        (self.guest / 'exe').unlink()
        (self.guest / 'exe').symlink_to(self.host_root / 'system/bin/surfaceflinger')
        self.assert_refused('no surfaceflinger', '--run', '1', 'unirender-engine')

    def test_refuses_surfaceflinger_with_unexpected_argument(self):
        self.configure_surfaceflinger()
        (self.guest / 'cmdline').write_bytes(b'/system/bin/surfaceflinger\0--other\0')
        self.assert_refused('no surfaceflinger', '--run', '1', 'unirender-engine')

    def test_unirender_cleanup_checks_the_current_executable(self):
        self.configure_surfaceflinger()
        self.env['GUEST_EXE_LINK'] = str(self.guest / 'exe')
        self.stub('sleep', 'rm "$GUEST_EXE_LINK" && ln -s "$HOST_ROOT/system/bin/surfaceflinger" "$GUEST_EXE_LINK"')
        result = self.run_trial('--run', '1', 'unirender-engine')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('thread changed or disappeared', result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['-2 201'])

    def configure_launcher(self):
        (self.guest / 'cmdline').write_bytes(b'com.android.launcher\0')
        workers = []
        for index in (0, 1):
            tid = 202 + index
            worker = self.create_thread(tid, -2, 421 + index)
            (worker / 'comm').write_text(f'hwuiTask{index}\n')
            workers.append(worker)
        return workers

    def test_launcher_trials_select_one_named_worker(self):
        workers = self.configure_launcher()
        for index, worker in enumerate(workers):
            with self.subTest(index=index):
                self.env['TASK'] = str(worker)
                result = self.run_trial('--run', '1', f'launcher-worker{index}')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'launcher hwuiTask{index} PID=200 TID={worker.name}', result.stdout)
                self.assertEqual(self.log.read_text().splitlines(), [f'-2 {worker.name}', f'2 {worker.name}'])
                self.assertEqual((workers[1 - index] / 'stat').read_text().split()[18], '-2')
                self.assertEqual((self.task / 'stat').read_text().split()[18], '-4')
                self.log.unlink()

    def test_launcher_trial_requires_the_exact_package(self):
        self.configure_launcher()
        (self.guest / 'cmdline').write_bytes(b'com.android.launcher:tools\0')
        self.assert_refused('no com.android.launcher', '--run', '1', 'launcher-worker0')

    def test_launcher_trial_refuses_duplicate_matching_workers(self):
        self.configure_launcher()
        duplicate = self.create_thread(205, -2, 424)
        (duplicate / 'comm').write_text('hwuiTask0\n')
        self.assert_refused('more than one launcher hwuiTask0', '--run', '1', 'launcher-worker0')

    def test_launcher_main_trial_changes_only_the_process_leader(self):
        workers = self.configure_launcher()
        leader = self.create_thread(200, -10, 204)
        (leader / 'comm').write_text('ndroid.launcher\n')
        self.env['TASK'] = str(leader)
        result = self.run_trial('--run', '1', 'launcher-main')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('launcher main thread PID=200 TID=200', result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), ['-2 200', '2 200'])
        self.assertEqual((leader / 'stat').read_text().split()[18], '-10')
        self.assertEqual([(worker / 'stat').read_text().split()[18] for worker in workers], ['-2', '-2'])

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
