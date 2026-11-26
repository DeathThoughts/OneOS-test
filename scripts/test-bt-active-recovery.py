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


# These fixtures exercise control flow and isolation guards, not native binaries.
APM = START.split("# OneOS APM effect routing v3 (begin)\n", 1)[1].split("# OneOS APM effect routing v3 (end)", 1)[0]

class ApmRouting(unittest.TestCase):
    def replay(self, mode='once', unsafe=False, mismatch=False, fail_extension=False):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            d = p / 'private'
            d.mkdir(mode=0o700)
            marker = d / mode
            if mode != 'absent':
                marker.touch(mode=0o600)
            if unsafe:
                marker.chmod(0o644)
            f, e = p / 'manager.so', p / 'extension.so'
            f.touch()
            e.touch()
            flag = p / 'flag'
            flag.touch()
            table = p / 'mountinfo'
            table.write_text('')
            mocked = p / 'bb'
            mocked.write_text("""#!/bin/sh
case "$1" in
stat) shift; value=$(stat "$@"); printf '0:0:%s\\n' "${value##*:}";;
readlink)
    if [ "$2" = /proc/self/ns/mnt ]; then echo 'mnt:[2]'
    elif [ "$2" = /proc/1/ns/mnt ]; then echo 'mnt:[1]'
    else shift; readlink "$@"; fi;;
sha256sum)
    case "$2" in
    "$FIXTURE/manager.so") echo "$MANAGER  $2";;
    "$FIXTURE/extension.so") echo "$EXTENSION  $2";;
    "$FIXTURE"/private/.apm.*) echo "$MANAGER_NEW  $2";;
    "$FIXTURE"/private/.ext.*) echo "$EXTENSION_NEW  $2";;
    *) exit 1;; esac;;
chcon) exit 0;;
mount)
    if [ "$2" = --bind ]; then
        [ "$FAIL_EXTENSION" = 0 ] || [ "$4" != "$FIXTURE/extension.so" ] || exit 1
        printf '20 1 0:20 / %s rw - ext4 test rw\\n' "$4" >> "$FIXTURE/mountinfo"
    else
        sed -i "s| $5 rw | $5 ro |" "$FIXTURE/mountinfo"
    fi;;
umount)
    target="$2"; [ "$2" != -l ] || target="$3"
    sed -i "\\| $target |d" "$FIXTURE/mountinfo";;
*) exec "$@";;
esac
""")
            mocked.chmod(0o755)
            script = (APM.replace('/data/oneos-apm-test', str(d))
                .replace('/data/guest_audio.flag', str(flag))
                .replace('/system/system_ext/lib64/libaudiopolicyextimpl.so', str(e))
                .replace('/system_ext/lib64/libaudiopolicyextimpl.so', str(e))
                .replace('/system/lib64/libaudiopolicymanagerdefault.so', str(f))
                .replace('/proc/self/mountinfo', str(table)))
            hashes = dict(re.findall(r'^\s*(old|new|ext_old|ext_new)=([0-9a-f]{64})$', APM, re.M))
            env = dict(os.environ, BB=str(mocked), FIXTURE=tmp,
                MANAGER='different' if mismatch else hashes['old'], MANAGER_NEW=hashes['new'],
                EXTENSION=hashes['ext_old'], EXTENSION_NEW=hashes['ext_new'],
                FAIL_EXTENSION='1' if fail_extension else '0')
            result = subprocess.run(['sh', '-c', script], env=env,
                check=True, capture_output=True, text=True, timeout=15)
            return result.stdout, marker.exists(), table.read_text()

    def test_missing_marker_and_unsafe_metadata(self):
        output, exists, mounts = self.replay(mode='absent')
        self.assertIn('not armed', output)
        self.assertFalse(exists)
        self.assertEqual(mounts, '')
        output, exists, mounts = self.replay(unsafe=True)
        self.assertIn('unsafe once marker', output)
        self.assertTrue(exists)
        self.assertEqual(mounts, '')

    def test_hash_rejection_preserves_trial(self):
        output, exists, mounts = self.replay(mismatch=True)
        self.assertIn('different library', output)
        self.assertTrue(exists)
        self.assertEqual(mounts, '')

    def test_success_and_persistence(self):
        output, exists, mounts = self.replay()
        self.assertIn('v3 APM + Oplus extension bound (once:', output)
        self.assertFalse(exists)
        self.assertEqual(mounts.count(' ro - '), 2)
        output, exists, mounts = self.replay(mode='enabled')
        self.assertIn('v3 APM + Oplus extension bound (enabled:', output)
        self.assertTrue(exists)
        self.assertEqual(mounts.count(' ro - '), 2)

    def test_extension_failure_rolls_back_manager(self):
        output, exists, mounts = self.replay(fail_extension=True)
        self.assertIn('rollback complete', output)
        self.assertFalse(exists)
        self.assertEqual(mounts, '')

if __name__ == '__main__':
    unittest.main()
