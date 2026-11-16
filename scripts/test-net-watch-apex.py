#!/usr/bin/env python3
"""oneos_switch.sh's net_watch loop runs net_fix.sh and the network snapshot
every pass (10 s) and the APEX watch only every 6th pass (60 s), reading the
loop backing files with builtins (coloros native audit 2026-11-16, S25 Ultra:
every pass forked cat/basename/dirname per loop device, 54+ forks every 10 s,
338 rounds in apex_watch.log in session bceaef29fb128e94). The apex_watch.log
format stays what it was.

The switch's own `sh -c` string is expanded by a shell as the switch expands
it, then run in every shell present with a fake /sys/block tree and with
sleep/date/ls/ip/ping stubbed; the stub sleep ends the loop by removing the
rootfs build.prop it waits on."""
from pathlib import Path
import os, shutil, subprocess, tempfile, unittest
ROOT = Path(__file__).resolve().parents[1]
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()
HEAD = 'NETFIX="$STAGE/data/local/tmp/net_fix.sh"\n$BB sh -c "'
START = SWITCH.index(HEAD) + len(HEAD)


def dq_end(text, i):
    """Index of the double quote that closes the string opened just before i."""
    while True:
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if c == '"':
            return i
        i += 1


BODY = SWITCH[START:dq_end(SWITCH, START)]
SHELLS = [s for s in ('busybox', 'dash', 'mksh', 'bash') if shutil.which(s)]
STUBS = {
    # End of pass n: a marker in apex_watch.log, and the rootfs goes away
    # after the last one so the loop's own condition ends it.
    'sleep': '''n=$(($(cat "$T/n" 2>/dev/null || echo 0) + 1)); echo $n > "$T/n"
echo "=== end $n" >> "$T/stage/apex_watch.log"
[ "$n" -ge "$ROUNDS" ] && rm -f "$T/stage/rootfs/system/build.prop"
exit 0
''',
    'date': 'echo 12:00:00\n',
    'ls': '''[ "$*" = "-l /data/apex/decompressed/" ] || { echo "ls $*"; exit 1; }
echo "total 8"
echo "-rw-r--r-- 1 root root 1234 Nov 16 12:00 com.android.x@1.decompressed.apex"
''',
    'ip': 'exit 0\n',
    'ping': 'exit 1\n',
}
# The apex_watch.log block as the old every-pass code wrote it for the fake
# tree below: loop1 (an app's file) and loop2 (no backing file) not shown.
OLD_BLOCK = '''--- 12:00:00 apex
   8
1234 Nov 16 com.android.x@1.decompressed.apex
loop0: /data/apex/x.apex
loop10: /data/apex/decompressed/com.y@1.apex
'''
LOOPS = {'loop0': '/data/apex/x.apex\n', 'loop1': '/data/app/~~a/com.b-1/base.apk\n',
         'loop2': '', 'loop10': '/data/apex/decompressed/com.y@1.apex\n'}


def run(shell, rounds, loops=LOOPS):
    """Returns ({pass: apex_watch lines}, net_watch.log text, stderr)."""
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        stage = t / 'stage'
        (stage / 'rootfs/system').mkdir(parents=True)
        (stage / 'rootfs/system/build.prop').write_text('')
        b = t / 'bin'
        b.mkdir()
        for name, body in STUBS.items():
            (b / name).write_text('#!/bin/sh\n' + body)
            (b / name).chmod(0o755)
        (t / 'net_fix.sh').write_text('echo "net route: fixed"\n')
        for name, backing in loops.items():
            (t / 'sys/block' / name / 'loop').mkdir(parents=True)
            (t / 'sys/block' / name / 'loop/backing_file').write_text(backing)
        if not loops:
            (t / 'sys/block').mkdir(parents=True)
        # The string the switch's `$BB sh -c` gets.
        (t / 'gen.sh').write_text("printf '%s' \"" + BODY + '"\n')
        env = {**os.environ, 'T': d, 'STAGE': str(stage), 'BB': 'busybox', 'NETFIX': str(t / 'net_fix.sh'),
               'ROUNDS': str(rounds), 'PATH': f'{b}{os.pathsep}{os.environ["PATH"]}'}
        g = subprocess.run(['/bin/sh', str(t / 'gen.sh')], env=env, capture_output=True, text=True, timeout=60)
        assert (g.returncode, g.stderr) == (0, ''), g.stderr
        inner = g.stdout
        for a, c in (('/system/bin/sh ', 'sh '), ('/sys/block/', f'{d}/sys/block/'),
                     ('/sys/class/net/', f'{d}/sys/class/net/')):
            assert a in inner, a
            inner = inner.replace(a, c)
        # busybox sh runs sleep, date and ls as built-in applets, not from
        # PATH, so each stub is also bound as a function ahead of the loop.
        pre = ''.join(f'{n}() {{ "$T/bin/{n}" "$@"; }}\n' for n in STUBS)
        (t / 'loop.sh').write_text(pre + inner)
        argv = [shell, 'sh'] if shell == 'busybox' else [shell]
        r = subprocess.run(argv + [str(t / 'loop.sh')], env=env, capture_output=True, text=True, timeout=60)
        passes, cur = {}, []
        for line in (stage / 'apex_watch.log').read_text().splitlines(keepends=True):
            if line.startswith('=== end '):
                passes[int(line[8:])] = ''.join(cur)
                cur = []
            else:
                cur.append(line)
        return passes, r.stdout, r.stderr


class NetWatchApex(unittest.TestCase):
    def test_sh_n(self):
        for shell in SHELLS:
            argv = [shell, 'sh'] if shell == 'busybox' else [shell]
            with self.subTest(shell=shell):
                r = subprocess.run(argv + ['-n'], input=BODY.replace('\\$', '$').replace('\\"', '"'),
                                   capture_output=True, text=True)
                self.assertEqual((r.returncode, r.stderr), (0, ''))

    def test_apex_every_6th_pass_old_format(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                passes, out, err = run(shell, 14)
                self.assertEqual(err, '')
                self.assertEqual(sorted(passes), list(range(1, 15)))
                self.assertEqual([n for n in passes if passes[n]], [1, 7, 13])
                for n in (1, 7, 13):
                    self.assertEqual(passes[n], OLD_BLOCK)

    def test_net_fix_and_snapshot_every_pass(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                _, out, err = run(shell, 8)
                self.assertEqual(out.count('12:00:00 net route: fixed\n'), 8)
                self.assertEqual(out.count('--- 12:00:00\n'), 8)
                self.assertEqual(out.count('gateway: unreachable\n'), 8)

    def test_no_loop_devices_no_noise(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                passes, out, err = run(shell, 1, loops={})
                self.assertEqual(err, '')
                self.assertEqual(passes[1], OLD_BLOCK.split('loop0')[0])

    def test_no_forks_per_loop_device(self):
        a = BODY.index('for l in /sys/block/loop*/loop/backing_file; do')
        loop = BODY[a:BODY.index('done', a)]
        for word in ('cat ', 'basename', 'dirname', '$(', '`'):
            self.assertNotIn(word, loop)


if __name__ == '__main__':
    unittest.main()
