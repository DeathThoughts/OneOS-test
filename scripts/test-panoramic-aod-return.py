#!/usr/bin/env python3
"""oneos_panoramic_aod.sh (written by guest_prepare.sh): the SystemUI restart for the wallpaper AOD feature."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / 'switcher/assets/guest_prepare.sh').read_text()
SCRIPT = SOURCE.split("<<'PANO'\n", 1)[1].split('\nPANO\n', 1)[0] + '\n'


class PanoramicAod(unittest.TestCase):
    def run_script(self, present=False, returning=False, awake='Dozing'):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t); bin_ = r / 'bin'; bin_.mkdir()
            stop = r / 'oneos_rec_stop'
            if returning:
                stop.write_text('')
            stubs = {
                'content': 'case "$1" in query) %s ;; esac\nexit 0\n' % ('echo "Row: 0 parameters=boolean:true"' if present else ':'),
                'dumpsys': 'echo "mWakefulness=%s"\n' % awake,
                'pidof': 'echo 4242\n',
                'sleep': 'exit 0\n',
            }
            for n, body in stubs.items():
                (bin_ / n).write_text('#!/bin/sh\n' + body); (bin_ / n).chmod(0o755)
            # kill is a shell builtin, so it is a function here, not a PATH stub.
            script = 'kill() { echo "$@" >> "%s"; }\n' % (r / 'kills') + SCRIPT.replace('/data/oneos_rec_stop', str(stop))
            out = subprocess.run(['/bin/sh', '-c', script], check=True, capture_output=True, text=True, timeout=60,
                                 env=dict(os.environ, PATH=f'{bin_}:{os.environ["PATH"]}')).stdout
            kills = (r / 'kills').read_text() if (r / 'kills').exists() else ''
            return out, kills

    def test_screen_off_restarts_systemui(self):
        out, kills = self.run_script()
        self.assertIn('wallpaper AOD feature added', out)
        self.assertIn('SystemUI restarted for the wallpaper AOD feature', out)
        self.assertEqual('-TERM 4242\n', kills)

    def test_no_restart_in_the_return_to_one_ui(self):
        out, kills = self.run_script(returning=True)
        self.assertIn('wallpaper AOD feature added', out)
        self.assertIn('return to One UI: no SystemUI restart', out)
        self.assertNotIn('SystemUI restarted', out)
        self.assertEqual('', kills)

    def test_feature_present_needs_no_restart(self):
        out, kills = self.run_script(present=True)
        self.assertEqual('wallpaper AOD feature present\n', out)
        self.assertEqual('', kills)

    def test_awake_screen_is_left_alone_until_the_return(self):
        # Awake for good: the loop runs its 1440 rounds (sleep stubbed) and no restart follows.
        out, kills = self.run_script(awake='Awake')
        self.assertNotIn('SystemUI restarted', out)
        self.assertEqual('', kills)


if __name__ == '__main__':
    unittest.main()
