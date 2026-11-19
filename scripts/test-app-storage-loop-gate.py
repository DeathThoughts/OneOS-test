#!/usr/bin/env python3
"""guest_prepare.sh's app_storage_loop.sh runs ensure_app_storage.sh only when
it can matter: the first round after boot, a packages.list change, an md5sum
that failed, and every 30 rounds as a safety net (coloros native audit
2026-11-16, S25 Ultra: ~2,440 stat calls every 10 s with nothing installed).
Runs the real heredoc in every shell present, with sleep/getprop/md5sum/am
stubbed and the loop ended by the stub sleep."""
from pathlib import Path
import os, shutil, subprocess, tempfile, unittest
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'switcher/assets/guest_prepare.sh').read_text()
HEAD = "app_storage_loop.sh\" <<'APPLOOP'\n"
START = SCRIPT.index(HEAD) + len(HEAD)
BODY = SCRIPT[START:SCRIPT.index('\nAPPLOOP\n', START) + 1]
SUBS = [('/system/bin/sh /data/local/tmp/ensure_app_storage.sh', '"$T/ensure.sh"'),
        ('/data/system/packages.list', '"$T/packages.list"'),
        ('/data/app_storage.log', '"$T/app_storage.log"'),
        # 0.5.20 (merged from upstream): the one-time full compile two minutes after boot keeps its marker and
        # log in /data and keys on the framework jars; the test points all of them into $T.
        ('/data/oneos_dexopt.done', '"$T/dexopt.done"'),
        ('/data/oneos_dexopt.log', '"$T/dexopt.log"'),
        ('/data/oneos_bin/busybox', '"$T/nobusybox"'),
        ('/system/framework/framework.jar', '"$T/fw.jar"'),
        ('/system/framework/services.jar', '"$T/svc.jar"')]
LOOP = BODY
for a, b in SUBS:
    assert a in LOOP, a
    LOOP = LOOP.replace(a, b)
assert '/data/' not in LOOP.replace('"$T/', '')
SHELLS = [s for s in ('dash', 'busybox', 'mksh', 'bash') if shutil.which(s)]
MD5 = shutil.which('md5sum')
STUBS = {
    # Round n starts when the loop's sleep returns for the n-th time; the plan
    # file's line n says what happens just before that round.
    'sleep': '''n=$(($(cat "$T/n" 2>/dev/null || echo 0) + 1)); echo $n > "$T/n"
[ "$n" -gt "$ROUNDS" ] && kill -TERM $PPID && exit 0
echo "R$n" >> "$T/events"
acts=$(sed -n "${n}p" "$T/plan")
rm -f "$T/md5fail" "$T/noboot" "$T/ensureout"
for a in $acts; do case $a in
    change) echo "com.new.app$n 10$n 0 /data/user/0/com.new.app$n default:targetSdkVersion=34 3003 0 1" >> "$T/packages.list" ;;
    *) : > "$T/$a" ;;
esac; done
exit 0
''',
    'getprop': '[ -e "$T/noboot" ] && echo 0 || echo 1\n',
    'md5sum': f'[ -e "$T/md5fail" ] && {{ echo "md5sum: read error" >&2; exit 1; }}\nexec {MD5} "$@"\n',
    'am': 'echo "am $*" >> "$T/events"\n',
}


def run(shell, plan, rounds=None):
    """Returns ({round: [events]}, log text)."""
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        b = t / 'bin'
        b.mkdir()
        for name, body in {**STUBS,
                           'ensure.sh': 'echo ensure >> "$T/events"\n[ -e "$T/ensureout" ] && echo "made dirs for com.x"\nexit 0\n'}.items():
            p = (t if name.endswith('.sh') else b) / name
            p.write_text('#!/bin/sh\n' + body)
            p.chmod(0o755)
        (t / 'packages.list').write_text('com.a 10100 0 /data/user/0/com.a default 3003 0 1\n')
        # The compile's marker already names this framework (the hash of no jar at all, as no jar is in $T): a
        # session whose apps were compiled for it does not start the compile again, so no round here runs it.
        (t / 'dexopt.done').write_text('d41d8cd98f00b204e9800998ecf8427e\n')
        (t / 'plan').write_text('\n'.join(plan) + '\n')
        # busybox sh runs sleep and md5sum as built-in applets, not from PATH,
        # so each stub is also bound as a function ahead of the loop.
        pre = ''.join(f'{n}() {{ "$T/bin/{n}" "$@"; }}\n' for n in STUBS)
        (t / 'loop.sh').write_text(LOOP.replace('\n', '\n' + pre, 1))
        argv = [shell, 'sh'] if shell == 'busybox' else [shell]
        env = {**os.environ, 'T': d, 'ROUNDS': str(rounds or len(plan)), 'PATH': f'{b}{os.pathsep}{os.environ["PATH"]}'}
        r = subprocess.run(argv + [str(t / 'loop.sh')], env=env, capture_output=True, text=True, timeout=60)
        rounds_ev, cur = {}, None
        for line in (t / 'events').read_text().splitlines():
            if line.startswith('R'):
                cur = int(line[1:]); rounds_ev[cur] = []
            else:
                rounds_ev[cur].append(line)
        log = (t / 'app_storage.log').read_text() if (t / 'app_storage.log').exists() else ''
        return rounds_ev, log


def ensured(ev):
    return [n for n in sorted(ev) if 'ensure' in ev[n]]


class AppStorageLoopGate(unittest.TestCase):
    def each(self, plan, check):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                check(*run(shell, plan))

    def test_sh_n(self):
        for shell in SHELLS:
            argv = [shell, 'sh'] if shell == 'busybox' else [shell]
            with self.subTest(shell=shell):
                for code in (BODY, LOOP):
                    r = subprocess.run(argv + ['-n'], input=code, capture_output=True, text=True)
                    self.assertEqual((r.returncode, r.stderr), (0, ''))

    def test_first_round_then_skipped(self):
        def check(ev, log):
            self.assertEqual(sorted(ev), list(range(1, 11)))
            self.assertEqual(ensured(ev), [1])
            self.assertFalse(any(e.startswith('am') for n in ev for e in ev[n]))
            self.assertEqual(log, '')
        self.each([''] * 10, check)

    def test_change_runs_and_refreshes_launcher(self):
        plan = [''] * 12
        plan[4] = 'change'           # round 5
        def check(ev, log):
            self.assertEqual(ensured(ev), [1, 5])
            self.assertEqual([n for n in ev if 'am force-stop com.android.launcher3' in ev[n]], [5])
            self.assertEqual(ev[5], ['ensure', 'am force-stop com.android.launcher3'])
            self.assertEqual(log.count('package list changed -> launcher refreshed'), 1)
        self.each(plan, check)

    def test_change_on_first_round_no_refresh(self):
        plan = ['change'] + [''] * 3
        def check(ev, log):
            self.assertEqual(ensured(ev), [1])
            self.assertFalse(any(e.startswith('am') for n in ev for e in ev[n]))
        self.each(plan, check)

    def test_safety_net_every_30_rounds(self):
        def check(ev, log):
            self.assertEqual(ensured(ev), [1, 31, 61])
        self.each([''] * 65, check)

    def test_safety_net_counts_from_last_run(self):
        plan = [''] * 50
        plan[9] = 'change'           # round 10
        def check(ev, log):
            self.assertEqual(ensured(ev), [1, 10, 40])
        self.each(plan, check)

    def test_ensure_output_on_net_round_refreshes_launcher(self):
        plan = [''] * 32
        plan[30] = 'ensureout'       # round 31: safety net finds something to fix
        def check(ev, log):
            self.assertEqual(ensured(ev), [1, 31])
            self.assertEqual([n for n in ev if any(e.startswith('am') for e in ev[n])], [31])
            self.assertIn('launcher refreshed (made dirs for com.x)', log)
        self.each(plan, check)

    def test_md5_failure_runs_ensure(self):
        plan = [''] * 8
        plan[3] = 'md5fail'          # round 4
        def check(ev, log):
            # Round 4 can't read the md5 so it runs; round 5 then has no last
            # md5, which is the first-round case again. The empty md5 differs
            # from the last one, so round 4 refreshes the launcher, as it did
            # before the gate (the refresh condition is unchanged).
            self.assertEqual(ensured(ev), [1, 4, 5])
            self.assertEqual([n for n in ev if any(e.startswith('am') for e in ev[n])], [4])
        self.each(plan, check)

    def test_not_booted_does_nothing(self):
        plan = ['noboot'] * 3 + [''] * 3
        def check(ev, log):
            for n in (1, 2, 3):
                self.assertEqual(ev[n], [])
            self.assertEqual(ensured(ev), [4])
        self.each(plan, check)

    def test_no_guest_net_fix(self):
        # oneos_switch.sh's host net_watch is the one copy of net_fix.sh; the
        # guest shares its network namespace (coloros native audit 2026-11-16,
        # S25 Ultra: the two copies ran on the same table 4242).
        self.assertNotIn('/data/local/tmp/net_fix.sh', BODY)
        self.assertNotIn('netout', BODY)
        self.assertIn('    sleep 10\n', BODY)


if __name__ == '__main__':
    unittest.main()
