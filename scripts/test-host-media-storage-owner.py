#!/usr/bin/env python3
"""/data/media owner on the host side (guest_prepare.sh): keeps per-app owners under Android/{data,media,obb}.

S25 Ultra, coloros17-findx9u-in-1105 (sessions db475ad5017133c4 and
a12bf19e1931e81a): after PR #36 made guest_start.sh's /data/media chown
non-recursive, guest/app_storage.log still said "realigned 1485" and
"realigned 1486 to new uids": guest_prepare.sh ran
`$BB chown -h -R 1023:1023 "$STAGE/data/media"` on every switch, resetting
ensure_app_storage.sh's <uid>:1078 owners under
/data/media/0/Android/{data,media,obb}/<pkg> before the guest booted.

This slices the real block out of switcher/assets/guest_prepare.sh and runs it
with a stub $BB that only records its arguments, under a temporary $STAGE.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'switcher/assets/guest_prepare.sh').read_text()
START_MARK = "# The host's half of PR #36:"
END_MARK = '$BB chmod 0770 "$STAGE/data/media" "$STAGE/data/media/0" 2>/dev/null || true\n'
BLOCK = START_MARK + SOURCE.split(START_MARK, 1)[1].split(END_MARK, 1)[0]


class Env:
    def __init__(self, d):
        self.root = Path(d)
        self.stage = self.root / 'stage'
        self.bin = self.root / 'bin'
        self.logs = self.root / 'logs'
        (self.stage / 'data').mkdir(parents=True)
        self.bin.mkdir()
        self.logs.mkdir()
        self.bb = self.bin / 'busybox'
        self.bb.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$TEST_LOGS/bb.log"\n'
            '[ -e "$TEST_LOGS/fail" ] && exit 1\n'
            'exit 0\n'
        )
        self.bb.chmod(0o755)
        self.script = self.root / 'host_media.sh'
        self.script.write_text(BLOCK + 'echo done\n')

    def mk(self, rel, text=None):
        p = self.stage / 'data/media' / rel
        if text is None:
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        return p

    def run(self):
        env = dict(os.environ, TEST_LOGS=str(self.logs), STAGE=str(self.stage), BB=str(self.bb))
        return subprocess.run(['/bin/sh', str(self.script)], env=env,
                              capture_output=True, text=True, timeout=30)

    def calls(self):
        f = self.logs / 'bb.log'
        if not f.exists():
            return []
        prefix = str(self.stage) + '/data'
        return [line.replace(prefix, '') for line in f.read_text().splitlines()]


class HostMediaStorageOwner(unittest.TestCase):
    def test_no_recursive_chown_of_all_of_media(self):
        self.assertNotIn('chown -h -R 1023:1023 "$STAGE/data/media" ', SOURCE)
        self.assertNotIn('chown -R 1023:1023 "$STAGE/data/media" ', SOURCE)
        self.assertNotRegex(SOURCE, r'chown[^\n]*-R[^\n]*"\$STAGE/data/media/0"')

    def test_chmod_and_chcon_lines_are_unchanged(self):
        self.assertIn(END_MARK, SOURCE)
        self.assertIn('$BB chcon -h -R u:object_r:media_rw_data_file:s0 "$STAGE/data/media" 2>/dev/null || true\n', SOURCE)
        # The block sits right before them, in section 5b.
        i = SOURCE.index(START_MARK)
        self.assertLess(SOURCE.index('=== 5b.'), i)
        self.assertLess(i, SOURCE.index(END_MARK))

    def test_block_parses(self):
        r = subprocess.run(['/bin/sh', '-n', '-c', BLOCK], capture_output=True, text=True)
        self.assertEqual((0, ''), (r.returncode, r.stderr))

    def test_new_dirs_are_in_the_no_symlink_list(self):
        loop = re.search(r'for s in (.*?); do\n    no_symlink "\$s"\ndone', SOURCE, re.S)
        self.assertIsNotNone(loop)
        entries = loop.group(1).replace('\\\n', ' ').split()
        for e in ('media/0', 'media/0/Android', 'media/0/Android/data',
                  'media/0/Android/media', 'media/0/Android/obb'):
            self.assertIn(e, entries)
        # The no_symlink loop runs before the first write into $STAGE/data.
        self.assertLess(loop.start(), SOURCE.index(START_MARK))

    def test_android_dirs_one_level_and_others_recursive(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            for sub in ('data', 'media', 'obb'):
                env.mk(f'0/Android/{sub}/com.example.app/files/db', 'keep')
            env.mk('0/OneUI/Camera/IMG.jpg', 'photo')
            env.mk('0/DCIM/Camera')
            env.mk('0/Download/a.pdf', 'pdf')
            env.mk('0/.thumbnails/t.jpg', 't')
            env.mk('obb/shared.obb', 'obb')
            r = env.run()
            self.assertEqual(0, r.returncode, r.stderr)
            calls = env.calls()
            self.assertEqual(calls[:6], [
                'chown -h 1023:1023 /media',
                'chown -h 1023:1023 /media/0',
                'chown -h 1023:1023 /media/0/Android',
                'chown -h 1023:1023 /media/0/Android/data',
                'chown -h 1023:1023 /media/0/Android/media',
                'chown -h 1023:1023 /media/0/Android/obb',
            ])
            self.assertEqual(set(calls[6:]), {
                'chown -h -R 1023:1023 /media/obb',
                'chown -h -R 1023:1023 /media/0/DCIM',
                'chown -h -R 1023:1023 /media/0/Download',
                'chown -h -R 1023:1023 /media/0/.thumbnails',
            })
            for c in calls:
                self.assertNotIn('com.example.app', c)
                self.assertNotIn('OneUI', c)
                self.assertFalse('-R' in c and '/Android' in c, c)
                self.assertNotIn(c, ('chown -h -R 1023:1023 /media', 'chown -h -R 1023:1023 /media/0'))

    def test_symlinks_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            outside = env.root / 'outside'
            outside.mkdir()
            env.mk('0/Android/data')
            os.symlink(str(outside), str(env.stage / 'data/media/link_top'))
            os.symlink(str(outside), str(env.stage / 'data/media/0/MusicLink'))
            os.symlink(str(outside), str(env.stage / 'data/media/0/.dotlink'))
            os.symlink('/nonexistent/missing', str(env.stage / 'data/media/0/broken_link'))
            os.symlink(str(outside), str(env.stage / 'data/media/0/Android/obb'))
            r = env.run()
            self.assertEqual(0, r.returncode, r.stderr)
            calls = env.calls()
            for bad in ('link_top', 'MusicLink', '.dotlink', 'broken_link', '/Android/obb', 'outside'):
                self.assertFalse(any(bad in c for c in calls), (bad, calls))
            self.assertIn('chown -h 1023:1023 /media/0/Android/data', calls)

        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            outside0 = env.root / 'outside0'
            outside0.mkdir()
            (env.stage / 'data/media').mkdir()
            os.symlink(str(outside0), str(env.stage / 'data/media/0'))
            r = env.run()
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual(env.calls(), ['chown -h 1023:1023 /media'])

    def test_missing_media_does_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            r = env.run()
            self.assertEqual((0, 'done\n'), (r.returncode, r.stdout), r.stderr)
            self.assertEqual(env.calls(), [])

    def test_failures_do_not_stop_the_switch(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            env.mk('0/Android/data/com.example.app')
            env.mk('0/DCIM')
            (env.logs / 'fail').write_text('1')
            r = env.run()
            self.assertEqual((0, 'done\n'), (r.returncode, r.stdout), r.stderr)
            self.assertGreater(len(env.calls()), 4)


if __name__ == '__main__':
    unittest.main()
