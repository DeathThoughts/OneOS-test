#!/usr/bin/env python3
"""bring_apk_path (oneos_root.sh): One UI's APK of a user app, also during a session.

During a session pm answers for the guest's framework, which does not have the
app yet; bring-add then reads One UI's /data/app itself. Runs the real function
with a fake pm and a fake /data.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'app-android/app/src/full/assets/root/oneos_root.sh').read_text()


def function():
    start = SCRIPT.index('bring_apk_path() {')
    return SCRIPT[start:SCRIPT.index('\n}\n', start) + 3]


class BringApkPathTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oneos-bring-')
        self.r = Path(self.temp.name)
        self.data = self.r / 'data'
        (self.data / 'system').mkdir(parents=True)
        (self.data / 'system/packages.list').write_text(
            'com.anthropic.claude 10234 0 /data/user/0/com.anthropic.claude default:targetSdkVersion=35 none 0 1 1 @null\n'
            'com.termux 10111 0 /data/user/0/com.termux default none 0 1 1 @null\n')
        self.bin = self.r / 'bin'
        self.bin.mkdir()
        # The guest's pm: it knows only its own apps.
        (self.bin / 'pm').write_text('#!/bin/sh\n[ "$2" = com.guest.only ] && echo package:/data/app/~~g==/com.guest.only-x==/base.apk\nexit 0\n')
        os.chmod(self.bin / 'pm', 0o755)

    def tearDown(self):
        self.temp.cleanup()

    def app(self, rel):
        d = self.data / 'app' / rel
        d.mkdir(parents=True)
        (d / 'base.apk').write_text('apk')
        return str(d / 'base.apk')

    def path(self, pkg, live='1'):
        done = subprocess.run(['sh', '-c', function() + '\nbring_apk_path "$1"', 'test', pkg],
                              env={**os.environ, 'DATA': str(self.data), 'BRING_LIVE': live,
                                   'PATH': f'{self.bin}:' + os.environ['PATH']},
                              capture_output=True, text=True)
        self.assertEqual(0, done.returncode, done.stderr)
        return done.stdout.strip()

    def test_live_session_finds_one_uis_app_the_guest_does_not_have(self):
        want = self.app('~~a1==/com.anthropic.claude-b2==')
        self.assertEqual(want, self.path('com.anthropic.claude'))

    def test_live_session_older_layout(self):
        want = self.app('com.termux-1')
        self.assertEqual(want, self.path('com.termux'))

    def test_live_session_needs_one_uis_package_list(self):
        self.app('~~a1==/com.not.listed-b2==')
        self.assertEqual('', self.path('com.not.listed'))

    def test_live_session_two_folders_is_no_answer(self):
        self.app('~~a1==/com.anthropic.claude-b2==')
        self.app('~~c3==/com.anthropic.claude-d4==')
        self.assertEqual('', self.path('com.anthropic.claude'))

    def test_live_session_a_prefix_is_not_another_app(self):
        self.app('~~a1==/com.termux.api-b2==')
        self.assertEqual('', self.path('com.termux'))

    def test_live_session_never_asks_the_guests_pm(self):
        self.assertEqual('', self.path('com.guest.only'))

    def test_live_session_symlinked_apk_is_refused(self):
        d = self.data / 'app/~~a1==/com.anthropic.claude-b2=='
        d.mkdir(parents=True)
        (d / 'base.apk').symlink_to('/etc/hostname')
        self.assertEqual('', self.path('com.anthropic.claude'))

    def test_before_a_session_pm_answers_as_before(self):
        self.assertEqual('/data/app/~~g==/com.guest.only-x==/base.apk', self.path('com.guest.only', live='0'))

    def test_bring_one_uses_it(self):
        self.assertIn('first=$(bring_apk_path "$pkg")', SCRIPT)


if __name__ == '__main__':
    unittest.main()
