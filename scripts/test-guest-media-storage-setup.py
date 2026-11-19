#!/usr/bin/env python3
"""/data/media setup on boot (guest_start.sh): keeps per-app owners under Android/{data,media,obb}.

S25 Ultra, coloros17-findx9u-in-1105 (sessions db19aee410bd79ff and
75e538ac0ed725df): recursive `chown -R 1023:1023 /data/media` and
`chmod -R 777 /data/media` on every boot overwrote ensure_app_storage.sh's
<uid>:1078 0770 owners under /data/media/0/Android/{data,media,obb}/<pkg>
(guest/app_storage.log: "realigned 1485 to new uids"), leaving per-app folders
0777 for ~80 s and spawning ~4,000 chown processes to repair them.

This slices the real /data/media setup block out of switcher/assets/guest_start.sh
and runs it with stubbed chown and chmod under a temporary root.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'switcher/assets/guest_start.sh').read_text()
START_MARK = 'echo "[Pivoted Root] Setting up /data/media and emulated storage..."\n'
END_MARK = '\n$BB mount -t tmpfs tmpfs /storage 2>/dev/null || true\n'
BLOCK = START_MARK + SOURCE.split(START_MARK, 1)[1].split(END_MARK, 1)[0] + '\n'


class Env:
    def __init__(self, d):
        self.root = Path(d)
        self.bin = self.root / 'bin'
        self.logs = self.root / 'logs'
        self.bin.mkdir()
        self.logs.mkdir()
        for tool in ('chown', 'chmod'):
            stub = self.bin / tool
            stub.write_text(
                '#!/bin/sh\n'
                f'printf "%s\\n" "$*" >> "$TEST_LOGS/{tool}.log"\n'
                '[ -e "$TEST_LOGS/fail" ] && exit 1\n'
                'exit 0\n'
            )
            stub.chmod(0o755)
        self.script = self.root / 'setup_media.sh'
        self.script.write_text(BLOCK.replace('/data/media', f'{d}/data/media'))

    def run(self):
        env = dict(
            os.environ,
            TEST_LOGS=str(self.logs),
            PATH=f'{self.bin}:{os.environ["PATH"]}',
        )
        return subprocess.run(
            ['/bin/sh', str(self.script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def calls(self, tool):
        f = self.logs / f'{tool}.log'
        if not f.exists():
            return []
        prefix = str(self.root)
        return [line.replace(prefix, '') for line in f.read_text().splitlines()]


class GuestMediaStorageSetup(unittest.TestCase):
    def test_script_parses(self):
        r = subprocess.run(['/bin/sh', '-n', '-c', BLOCK], capture_output=True, text=True)
        self.assertEqual((0, ''), (r.returncode, r.stderr))

    def test_fresh_boot_without_android_subdirs(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            r = env.run()
            self.assertEqual(0, r.returncode, r.stderr)
            for rel in (
                'data/media/0/Android',
                'data/media/0/Pictures/Screenshots',
                'data/media/0/Download',
                'data/media/0/DCIM',
                'data/media/0/Documents',
            ):
                self.assertTrue((env.root / rel).is_dir(), rel)

            chown = env.calls('chown')
            chmod = env.calls('chmod')
            self.assertEqual(
                chown[:3],
                [
                    '1023:1023 /data/media',
                    '1023:1023 /data/media/0',
                    '1023:1023 /data/media/0/Android',
                ],
            )
            self.assertEqual(
                chmod[:3],
                [
                    '777 /data/media',
                    '777 /data/media/0',
                    '777 /data/media/0/Android',
                ],
            )
            for absent in ('/data/media/0/Android/data', '/data/media/0/Android/media', '/data/media/0/Android/obb'):
                self.assertFalse(any(absent in c for c in chown + chmod), absent)
            self.assertEqual(
                set(chown[3:]),
                {
                    '-R 1023:1023 /data/media/0/DCIM',
                    '-R 1023:1023 /data/media/0/Documents',
                    '-R 1023:1023 /data/media/0/Download',
                    '-R 1023:1023 /data/media/0/Pictures',
                },
            )
            self.assertEqual(
                set(chmod[3:]),
                {
                    '-R 777 /data/media/0/DCIM',
                    '-R 777 /data/media/0/Documents',
                    '-R 777 /data/media/0/Download',
                    '-R 777 /data/media/0/Pictures',
                },
            )

    def test_second_boot_preserves_per_app_dirs_and_recurses_other_entries(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            for sub in ('data', 'media', 'obb'):
                pkg_file = env.root / f'data/media/0/Android/{sub}/com.example.app/files/db'
                pkg_file.parent.mkdir(parents=True, exist_ok=True)
                pkg_file.write_text('keep')
            (env.root / 'data/media/obb/shared.obb').parent.mkdir(parents=True, exist_ok=True)
            (env.root / 'data/media/obb/shared.obb').write_text('obb')
            (env.root / 'data/media/0/.thumbnails').mkdir(parents=True, exist_ok=True)
            (env.root / 'data/media/0/.thumbnails/thumb.jpg').write_text('t')

            r = env.run()
            self.assertEqual(0, r.returncode, r.stderr)
            chown = env.calls('chown')
            chmod = env.calls('chmod')

            want_non_recursive_chown = [
                '1023:1023 /data/media',
                '1023:1023 /data/media/0',
                '1023:1023 /data/media/0/Android',
                '1023:1023 /data/media/0/Android/data',
                '1023:1023 /data/media/0/Android/media',
                '1023:1023 /data/media/0/Android/obb',
            ]
            want_non_recursive_chmod = [
                '777 /data/media',
                '777 /data/media/0',
                '777 /data/media/0/Android',
                '777 /data/media/0/Android/data',
                '777 /data/media/0/Android/media',
                '777 /data/media/0/Android/obb',
            ]
            self.assertEqual(chown[:6], want_non_recursive_chown)
            self.assertEqual(chmod[:6], want_non_recursive_chmod)

            # Zero touches (recursive or non-recursive) to <pkg> dirs or recursive touches on Android/*.
            for c in chown + chmod:
                self.assertNotIn('com.example.app', c)
                self.assertFalse(c.startswith('-R ') and '/Android' in c, c)
                self.assertNotEqual(c, '-R 1023:1023 /data/media')
                self.assertNotEqual(c, '-R 777 /data/media')
                self.assertNotEqual(c, '-R 1023:1023 /data/media/0')
                self.assertNotEqual(c, '-R 777 /data/media/0')

            self.assertEqual(
                set(chown[6:]),
                {
                    '-R 1023:1023 /data/media/obb',
                    '-R 1023:1023 /data/media/0/DCIM',
                    '-R 1023:1023 /data/media/0/Documents',
                    '-R 1023:1023 /data/media/0/Download',
                    '-R 1023:1023 /data/media/0/Pictures',
                    '-R 1023:1023 /data/media/0/.thumbnails',
                },
            )
            self.assertEqual(
                set(chmod[6:]),
                {
                    '-R 777 /data/media/obb',
                    '-R 777 /data/media/0/DCIM',
                    '-R 777 /data/media/0/Documents',
                    '-R 777 /data/media/0/Download',
                    '-R 777 /data/media/0/Pictures',
                    '-R 777 /data/media/0/.thumbnails',
                },
            )

    def test_oneui_album_tree_is_completely_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            img = env.root / 'data/media/0/OneUI/Camera/IMG.jpg'
            img.parent.mkdir(parents=True, exist_ok=True)
            img.write_text('photo')
            r = env.run()
            self.assertEqual(0, r.returncode, r.stderr)
            for c in env.calls('chown') + env.calls('chmod'):
                self.assertNotIn('OneUI', c)

    def test_symlinks_are_skipped(self):
        # Top-level dir link, dotfile link, broken link, and Android/obb link.
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            outside = env.root / 'outside'
            outside.mkdir()
            (outside / 'secret').write_text('s')
            (env.root / 'data/media/0/Android/data').mkdir(parents=True)
            os.symlink(str(outside), str(env.root / 'data/media/link_top'))
            os.symlink(str(outside), str(env.root / 'data/media/0/MusicLink'))
            os.symlink(str(outside), str(env.root / 'data/media/0/.dotlink'))
            os.symlink('/nonexistent/missing_target', str(env.root / 'data/media/0/broken_link'))
            os.symlink(str(outside), str(env.root / 'data/media/0/Android/obb'))

            r = env.run()
            self.assertEqual(0, r.returncode, r.stderr)
            all_calls = env.calls('chown') + env.calls('chmod')
            for forbidden in ('link_top', 'MusicLink', '.dotlink', 'broken_link', '/Android/obb', 'outside'):
                self.assertFalse(any(forbidden in c for c in all_calls), (forbidden, all_calls))
            self.assertIn('1023:1023 /data/media/0/Android/data', env.calls('chown'))

        # /data/media/0 itself is a symlink (pre-created before mkdir -p runs on real dirs:
        # test the guard when /data/media/0 is a symlink to an external dir).
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            outside0 = env.root / 'outside0'
            outside0.mkdir()
            (env.root / 'data/media').mkdir(parents=True)
            os.symlink(str(outside0), str(env.root / 'data/media/0'))

            r = env.run()
            self.assertEqual(0, r.returncode, r.stderr)
            all_calls = env.calls('chown') + env.calls('chmod')
            self.assertEqual(env.calls('chown'), ['1023:1023 /data/media'])
            self.assertEqual(env.calls('chmod'), ['777 /data/media'])
            self.assertFalse(any('/data/media/0' in c or 'outside0' in c for c in all_calls), all_calls)

    def test_chown_and_chmod_failures_are_non_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            (env.root / 'data/media/0/Android/data/com.example.app').mkdir(parents=True)
            (env.logs / 'fail').write_text('1')
            r = env.run()
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertGreater(len(env.calls('chown')), 4)
            self.assertGreater(len(env.calls('chmod')), 4)


if __name__ == '__main__':
    unittest.main()
