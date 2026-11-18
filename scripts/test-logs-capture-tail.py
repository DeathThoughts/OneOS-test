#!/usr/bin/env python3
"""collect.list, files logs.capture: the report keeps the end of the guest session.

S25 Ultra, session 22343aacdd4e1d6c: the owner sent the report a few minutes after the return, boot.log and
boot.log.01 held One UI's logs only, head/ stopped at 17:13 and the guest's 17:13-17:33 was in no file. The block
now also lists up to two of the newest rotations whose mtime is at or before the mtime of return_inplace.done.
The block's shell is cut out of collect.list and run under sh against a temp tree (paths rewritten into it).
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECT = (ROOT / 'app-android/app/src/full/assets/logs/collect.list').read_text(encoding='utf-8')
BLOCK = COLLECT.split("files logs.capture 300 capture '\n", 1)[1].split("\ntrue'\n", 1)[0] + "\ntrue\n"
SH = '/bin/sh' if os.path.exists('/bin/sh') else shutil.which('sh')
RETURN = 1_700_000_000


def touch(path, mtime, text='x\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')
    os.utime(path, (mtime, mtime))


class Capture(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.root = Path(self._d.name)
        self.logs = self.root / 'data/local/tmp/oneos_logs'
        self.stage = self.root / 'data/local/tmp/oneos'
        self.logs.mkdir(parents=True)
        self.stage.mkdir(parents=True)

    def rotations(self, crash=()):
        # boot.log.NN: the higher the number the older; NN ended 10 minutes apart, .03 ends before the return.
        touch(self.logs / 'boot.log', RETURN + 300)
        touch(self.logs / 'boot.log.01', RETURN + 200)
        touch(self.logs / 'boot.log.02', RETURN + 60)
        for n in range(3, 8):
            touch(self.logs / f'boot.log.{n:02d}', RETURN - 600 * (n - 3),
                  'FATAL EXCEPTION\n' if n in crash else 'x\n')

    def run_block(self):
        text = BLOCK.replace('/data/local/tmp/', f'{self.root.as_posix()}/data/local/tmp/')
        r = subprocess.run([SH, '-c', text], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [Path(l).name if '/head/' not in l else 'head/' + Path(l).name for l in r.stdout.splitlines()]

    def test_parses(self):
        subprocess.run([SH, '-n', '-c', BLOCK], check=True)

    def test_takes_the_two_newest_rotations_at_or_before_the_return(self):
        self.rotations()
        touch(self.stage / 'return_inplace.done', RETURN)
        self.assertEqual(self.run_block(), ['boot.log', 'boot.log.01', 'boot.log.03', 'boot.log.04'])

    def test_a_rotation_ending_exactly_at_the_return_counts(self):
        self.rotations()
        touch(self.logs / 'boot.log.02', RETURN)
        touch(self.stage / 'return_inplace.done', RETURN)
        self.assertEqual(self.run_block(), ['boot.log', 'boot.log.01', 'boot.log.02', 'boot.log.03'])

    def test_no_return_marker_takes_nothing_extra(self):
        self.rotations()
        self.assertEqual(self.run_block(), ['boot.log', 'boot.log.01'])

    def test_skips_files_already_listed_from_head(self):
        self.rotations()
        touch(self.stage / 'return_inplace.done', RETURN)
        (self.logs / 'head').mkdir()
        os.link(self.logs / 'boot.log.03', self.logs / 'head/boot.log.03')
        got = self.run_block()
        self.assertEqual(got, ['head/boot.log.03', 'boot.log', 'boot.log.01', 'boot.log.04', 'boot.log.05'])
        self.assertEqual(len(got), len(set(got)))

    def test_crash_search_does_not_list_the_tail_files_again(self):
        self.rotations(crash=(3, 4, 6, 7))
        touch(self.stage / 'return_inplace.done', RETURN)
        got = self.run_block()
        self.assertEqual(got, ['boot.log', 'boot.log.01', 'boot.log.03', 'boot.log.04', 'boot.log.06', 'boot.log.07'])
        self.assertEqual(len(got), len(set(got)))

    def test_extra_files_are_bounded_at_two(self):
        self.rotations()
        for n in range(8, 25):
            touch(self.logs / f'boot.log.{n:02d}', RETURN - 6000 - n)
        touch(self.stage / 'return_inplace.done', RETURN)
        self.assertEqual(len(self.run_block()), 4)

    def test_a_marker_older_than_every_rotation_takes_nothing(self):
        # A new session moves the old capture away, so an earlier session's marker is older than all files.
        self.rotations()
        touch(self.stage / 'return_inplace.done', RETURN - 100000)
        self.assertEqual(self.run_block(), ['boot.log', 'boot.log.01'])


if __name__ == '__main__':
    unittest.main()
