#!/usr/bin/env python3
"""Replay the A2DP event guard against synthetic dumps, without Android."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
START = (ROOT / 'switcher/assets/guest_start.sh').read_text()
WATCH = START.split("<<'BT_ACTIVEEOF'\n", 1)[1].split("\nBT_ACTIVEEOF", 1)[0]
CONNECT = 'I/A2dpService(123): connectionStateChanged, device: XX:XX:XX:XX:39:4F, state: 1->2'
ERROR = 'E/BluetoothActiveDeviceManager(123): setA2dpActiveDevice: failed for device null'
DUMP = '''BluetoothActiveDeviceManager
  A2DP:
    Connected count: 1
      XX:XX:XX:XX:39:4F: Headphones
    Active: NULL
    Fallback: XX:XX:XX:XX:39:4F: Headphones
  HFP:
    Active: NULL
'''
AUDIO = '''    Connected devices:

    APM Connected device:

- Actual mode = MODE_NORMAL
'''

class Recovery(unittest.TestCase):
    def replay(self, events, dump=DUMP, audio=AUDIO):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'events').write_text('\n'.join(events) + '\n')
            (p / 'bt').write_text(dump)
            (p / 'audio').write_text(audio)
            def executable(name, code):
                f = p / name
                f.write_text('#!/bin/sh\n' + code)
                f.chmod(0o755)
                return f
            executable('logcat', 'cat "$FIXTURE/events"')
            executable('dumpsys', 'case "$1" in bluetooth_manager) cat "$FIXTURE/bt";; audio) cat "$FIXTURE/audio";; *) exit 1;; esac')
            executable('sleep', 'exit 0')
            bb = executable('bb', 'case "$1" in timeout) shift 2; if [ "$1" = /system/bin/sh ]; then echo "$@" >> "$FIXTURE/calls"; exit 0; fi;; *) ;; esac\nexec "$@"')
            script = WATCH.replace('/data/oneos_bt_active.log', str(p / 'watch.log'))
            env = dict(os.environ, FIXTURE=tmp, PATH=tmp + ':' + os.environ['PATH'])
            subprocess.run(['sh', '-c', script, 'watch', str(bb)], env=env, check=True, timeout=10)
            f = p / 'calls'
            return f.read_text().splitlines() if f.exists() else []

    def test_exact_error_after_connect_once(self):
        calls = self.replay([CONNECT, ERROR, ERROR])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith('OneOSBtActive 39:4F'))

    def test_no_trigger_without_connection_or_after_disconnect(self):
        self.assertEqual(self.replay([ERROR]), [])
        self.assertEqual(self.replay([CONNECT, CONNECT.replace('1->2','2->0'), ERROR]), [])

    def test_no_trigger_on_other_route_or_multiple_devices(self):
        self.assertEqual(self.replay([CONNECT, ERROR], dump=DUMP.replace('Active: NULL','Active: XX:XX:XX:XX:39:4F')), [])
        self.assertEqual(self.replay([CONNECT, ERROR], dump=DUMP.replace('count: 1','count: 2')), [])
        self.assertEqual(self.replay([CONNECT, ERROR], audio=AUDIO.replace('MODE_NORMAL','MODE_IN_CALL')), [])
        self.assertEqual(self.replay([CONNECT, ERROR], audio=AUDIO.replace('Connected devices:\n','Connected devices:\n      wired headset\n')), [])
        self.assertEqual(self.replay([CONNECT, ERROR], dump=DUMP.replace('39:4F','11:22')), [])

if __name__ == '__main__':
    unittest.main()
