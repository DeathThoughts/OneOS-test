#!/usr/bin/env python3
"""MediaProvider's mount scan waits for OneOS's albums: the services.jar patcher.

guest_start.sh can bind One UI's albums into the guest's /data/media/0/OneUI only after the
guest's vold built its storage views, and StorageManagerService tells MediaProvider about the
mounted emulated volume at once: its scan found OneUI/ empty, dropped every row and indexed
them all again (S25 Ultra, ColorOS 17 coloros17-findx9u-in-1105, session a12bf19e1931e81a).
patches/guest_media_mount_hold.py calls OneOSMediaHold.await right before
StorageSessionController.notifyVolumeStateChanged in onVolumeStateChangedAsync.

Here, on small smali trees with the shape of the ColorOS 17 framework (image 1105):
- the call lands right before notifyVolumeStateChanged, after mLock's monitor-exit, takes
  p1 .. p3 as a range and has its own catch Throwable that goes on to the notify call;
- the helper is copied next to StorageManagerService; its content: MOUNTED (2), emulated (2),
  user 0, the two properties, 100 ms steps, 15000 ms at most, once only, catch Throwable;
- a second run changes nothing; a framework taking VolumeInfo gets the helper with that type;
- a missing or doubled anchor, a call under the lock, another type or a different hold already
  there stop it, with the tree untouched;
- make-overlays.sh runs it with the other services.jar patches and names mediahold in
  guards.txt, which nothing on the phone requires (older sets keep starting);
- the x9u-in-1105-sultra pins: the r6 jar and guards.txt, with sizes and a download source.
"""
import hashlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / 'switcher/patches/guest_media_mount_hold.py'
HELPER = ROOT / 'switcher/patches/OneOSMediaHold.smali'
WVI = 'Lcom/android/server/storage/WatchedVolumeInfo;'
VI = 'Landroid/os/storage/VolumeInfo;'
SMS = 'com/android/server/StorageManagerService.smali'
SSC = 'Lcom/android/server/storage/StorageSessionController;'
NOTIFY_WVI = f'invoke-virtual {{v1, v2}}, {SSC}->notifyVolumeStateChanged(Lcom/android/server/storage/ImmutableVolumeInfo;)V'
NOTIFY_VI = f'invoke-virtual {{v1, p1}}, {SSC}->notifyVolumeStateChanged({VI})V'


def method(vol=WVI, notify=None, locked=False, twice=False):
    """onVolumeStateChangedAsync as baksmali shows it in the 1105 image (record keeping cut short)."""
    if notify is None:
        notify = NOTIFY_WVI if vol == WVI else NOTIFY_VI
    arg = ('''    invoke-virtual {p1}, Lcom/android/server/storage/WatchedVolumeInfo;->getImmutableVolumeInfo()Lcom/android/server/storage/ImmutableVolumeInfo;

    move-result-object v2

''' if vol == WVI else '')
    call = f'''    :try_start_3
    iget-object v1, p0, Lcom/android/server/StorageManagerService;->mStorageSessionController:{SSC}

{arg}    {notify}
    :try_end_3
    .catch Lcom/android/server/storage/StorageSessionController$ExternalStorageServiceException; {{:try_start_3 .. :try_end_3}} :catch_2
'''
    record = '''    :try_start_2
    invoke-virtual {p1}, %s->getFsUuid()Ljava/lang/String;

    move-result-object v2

    invoke-static {v2}, Landroid/text/TextUtils;->isEmpty(Ljava/lang/CharSequence;)Z

    move-result v2

    if-nez v2, :cond_5

    invoke-direct {p0}, Lcom/android/server/StorageManagerService;->writeSettingsLocked()V

    :cond_5
''' % vol
    if locked:
        body = record + '\n' + call + '''
    monitor-exit v1
    :try_end_2
    .catchall {:try_start_2 .. :try_end_2} :catchall_1
'''
    else:
        body = record + '''    monitor-exit v1
    :try_end_2
    .catchall {:try_start_2 .. :try_end_2} :catchall_1

''' + call
    if twice:
        body += '\n    ' + notify + '\n'
    return f'''.method private onVolumeStateChangedAsync({vol}II)V
    .locals 12
    .param p1, "vol"    # {vol}
    .param p2, "oldState"    # I
    .param p3, "newState"    # I

    .line 2067
    const/4 v0, 0x2

    if-ne p3, v0, :cond_0

    .line 2072
    :try_start_0
    invoke-direct {{p0, p1}}, Lcom/android/server/StorageManagerService;->prepareUserStorageIfNeeded({vol})V
    :try_end_0
    .catch Ljava/lang/Exception; {{:try_start_0 .. :try_end_0}} :catch_0

    goto :goto_1

    :catch_0
    move-exception v0

    return-void

    :cond_0
    :goto_1
    iget-object v1, p0, Lcom/android/server/StorageManagerService;->mLock:Ljava/lang/Object;

    monitor-enter v1

{body}
    goto :goto_3

    :catch_2
    move-exception v1

    const-string v2, "StorageManagerService"

    const-string v3, "Failed to notify volume state changed to the Storage Service"

    invoke-static {{v2, v3, v1}}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;Ljava/lang/Throwable;)I

    :goto_3
    iget-object v2, p0, Lcom/android/server/StorageManagerService;->mLock:Ljava/lang/Object;

    monitor-enter v2

    :try_start_4
    iget-object v1, p0, Lcom/android/server/StorageManagerService;->mCallbacks:Lcom/android/server/StorageManagerService$Callbacks;

    invoke-static {{v1, p1, p2, p3}}, Lcom/android/server/StorageManagerService$Callbacks;->-$$Nest$mnotifyVolumeStateChanged(Lcom/android/server/StorageManagerService$Callbacks;{vol}II)V

    monitor-exit v2
    :try_end_4
    .catchall {{:try_start_4 .. :try_end_4}} :catchall_0

    return-void

    :catchall_0
    move-exception v0

    :try_start_5
    monitor-exit v2
    :try_end_5
    .catchall {{:try_start_5 .. :try_end_5}} :catchall_0

    throw v0

    :catchall_1
    move-exception v0

    :try_start_6
    monitor-exit v1
    :try_end_6
    .catchall {{:try_start_6 .. :try_end_6}} :catchall_1

    throw v0
.end method
'''


def sms(*methods):
    return ('.class public Lcom/android/server/StorageManagerService;\n'
            '.super Landroid/os/storage/IStorageManager$Stub;\n\n'
            '.field private final mLock:Ljava/lang/Object;\n\n'
            '.method private writeSettingsLocked()V\n    .locals 0\n\n    return-void\n.end method\n\n'
            + '\n'.join(methods))


class Tree:
    def __init__(self, text, d='smali_classes2'):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dir = d
        self.path = self.root / d / SMS
        self.path.parent.mkdir(parents=True)
        self.path.write_text(text)
        # Another dex without StorageManagerService.
        other = self.root / ('smali_classes9' if d == 'smali' else 'smali') / 'com/android/server/Other.smali'
        other.parent.mkdir(parents=True)
        other.write_text('.class public Lcom/android/server/Other;\n.super Ljava/lang/Object;\n')

    def run(self, *extra):
        return subprocess.run([sys.executable, '-I', str(PATCHER), str(self.root), *extra],
                              capture_output=True, text=True)

    def files(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in sorted(self.root.rglob('*')) if p.is_file()}

    @property
    def helper(self):
        return self.root / self.dir / 'com/android/server/OneOSMediaHold.smali'

    def close(self):
        self.tmp.cleanup()


class Patch(unittest.TestCase):
    def setUp(self):
        self.tree = Tree(sms(method()))

    def tearDown(self):
        self.tree.close()

    def test_call_right_before_notify_after_the_monitor_exit(self):
        r = self.tree.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, 'smali_classes2\n')
        lines = self.tree.path.read_text().split('\n')
        call = next(i for i, l in enumerate(lines) if 'OneOSMediaHold;->await' in l)
        self.assertEqual(lines[call].strip(),
                         f'invoke-static/range {{p1 .. p3}}, Lcom/android/server/OneOSMediaHold;->await({WVI}II)V')
        self.assertEqual(lines[call - 1].strip(), ':oneos_media_hold_start')
        self.assertEqual(lines[call + 1].strip(), ':oneos_media_hold_end')
        self.assertEqual(lines[call + 2].strip(), '.catch Ljava/lang/Throwable; '
                         '{:oneos_media_hold_start .. :oneos_media_hold_end} :oneos_media_hold_done')
        # The catch goes on to the notify call itself, and nothing comes between.
        rest = [l.strip() for l in lines[call + 3:] if l.strip()]
        self.assertEqual(rest[0], ':oneos_media_hold_done')
        self.assertEqual(rest[1], NOTIFY_WVI)
        # After mLock's monitor-exit and the argument's move-result; the lock is not held.
        before = [l.strip() for l in lines[:call] if l.strip() and not l.strip().startswith(('#', ':', '.'))]
        self.assertEqual(before[-1], 'move-result-object v2')
        mons = [l for l in before if l.startswith('monitor-')]
        self.assertEqual(mons, ['monitor-enter v1', 'monitor-exit v1'])
        self.assertEqual(sum('OneOSMediaHold' in l for l in lines if not l.strip().startswith('#')), 1)
        self.assertIn('session a12bf19e1931e81a', '\n'.join(lines[call - 6:call]))

    def test_helper_copied_next_to_the_service(self):
        self.assertEqual(self.tree.run().returncode, 0)
        self.assertEqual(self.tree.helper.read_text(), HELPER.read_text())
        self.assertFalse((self.tree.root / 'smali/com/android/server/OneOSMediaHold.smali').exists())

    def test_second_run_changes_nothing(self):
        self.assertEqual(self.tree.run().returncode, 0)
        before = self.tree.files()
        r = self.tree.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, '')
        self.assertEqual(self.tree.files(), before)

    def test_a_lost_helper_comes_back(self):
        self.assertEqual(self.tree.run().returncode, 0)
        smali = self.tree.path.read_text()
        self.tree.helper.unlink()
        r = self.tree.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, 'smali_classes2\n')
        self.assertEqual(self.tree.path.read_text(), smali)
        self.assertTrue(self.tree.helper.is_file())

    def test_a_different_hold_already_there_stops_it(self):
        self.assertEqual(self.tree.run().returncode, 0)
        self.tree.path.write_text(self.tree.path.read_text().replace('{p1 .. p3}', '{p1 .. p2}'))
        before = self.tree.files()
        r = self.tree.run()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('different OneOS media hold', r.stderr)
        self.assertEqual(self.tree.files(), before)


class VolumeInfoFramework(unittest.TestCase):
    def test_helper_takes_the_callers_type(self):
        tree = Tree(sms(method(vol=VI)), d='smali')
        try:
            r = tree.run()
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, 'smali\n')
            self.assertIn(f'invoke-static/range {{p1 .. p3}}, Lcom/android/server/OneOSMediaHold;->await({VI}II)V',
                          tree.path.read_text())
            helper = tree.helper.read_text()
            self.assertNotIn(WVI, helper)
            self.assertIn(f'.method public static await({VI}II)V', helper)
            self.assertIn(f'invoke-virtual {{p0}}, {VI}->getMountUserId()I', helper)
            self.assertEqual(helper, HELPER.read_text().replace(WVI, VI))
        finally:
            tree.close()


class Refused(unittest.TestCase):
    def refused(self, text, why, *extra):
        tree = Tree(text)
        try:
            before = tree.files()
            r = tree.run(*extra)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(why, r.stderr)
            self.assertIn('not patched', r.stderr)
            self.assertEqual(r.stdout, '')
            self.assertEqual(tree.files(), before)
        finally:
            tree.close()

    def test_no_notify_call(self):
        self.refused(sms(method(notify='nop')), 'notifyVolumeStateChanged 0 times')

    def test_two_notify_calls(self):
        self.refused(sms(method(twice=True)), 'notifyVolumeStateChanged 2 times')

    def test_no_such_method(self):
        self.refused(sms(method().replace('onVolumeStateChangedAsync', 'onVolumeStateChangedAsyncX')),
                     'found 0 times')

    def test_two_such_methods(self):
        self.refused(sms(method(), method()), 'found 2 times')

    def test_notify_under_the_lock(self):
        self.refused(sms(method(locked=True)), "not after mLock's monitor-exit")

    def test_another_volume_type(self):
        text = sms(method()).replace(f'onVolumeStateChangedAsync({WVI}II)V',
                                     'onVolumeStateChangedAsync(Ljava/lang/Object;II)V')
        self.refused(text, 'takes Ljava/lang/Object;')

    def test_no_helper(self):
        self.refused(sms(method()), 'no helper smali', '/nonexistent/OneOSMediaHold.smali')

    def test_service_in_two_dexes(self):
        tree = Tree(sms(method()))
        try:
            twin = tree.root / 'smali_classes3' / SMS
            twin.parent.mkdir(parents=True)
            twin.write_text(sms(method()))
            before = tree.files()
            r = tree.run()
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('exactly one smali dir, found 2', r.stderr)
            self.assertEqual(tree.files(), before)
        finally:
            tree.close()


class Helper(unittest.TestCase):
    """OneOSMediaHold.smali as it is checked in (hand-written smali)."""

    @classmethod
    def setUpClass(cls):
        cls.text = HELPER.read_text()
        cls.code = [l.strip() for l in cls.text.split('\n') if l.strip() and not l.strip().startswith('#')]

    def index(self, line):
        self.assertIn(line, self.code)
        return self.code.index(line)

    def test_one_public_static_method(self):
        self.assertTrue(self.text.startswith('.class public final Lcom/android/server/OneOSMediaHold;\n'))
        methods = [l for l in self.code if l.startswith('.method')]
        self.assertEqual(methods, [f'.method public static await({WVI}II)V'])
        self.assertEqual([l for l in self.code if l.startswith('.field')], ['.field private static sWaited:Z'])

    def test_registers_fit_the_locals(self):
        n = int(re.search(r'^\s*\.locals (\d+)$', self.text, re.M).group(1))
        used = {int(x) for x in re.findall(r'\bv(\d+)\b', '\n'.join(self.code))}
        self.assertLess(max(used), n)
        self.assertTrue({int(x) for x in re.findall(r'\bp(\d+)\b', '\n'.join(self.code))} <= {0, 1, 2})
        # Wide values in pairs no other value shares: start v3/v4, elapsed v5/v6, the cap v7/v8.
        for l in self.code:
            m = re.match(r'(?:const-wide/16|move-result-wide) v(\d+)', l)
            if m:
                self.assertIn(int(m.group(1)), (3, 5, 7), l)

    def test_only_user_0s_emulated_volume_going_mounted(self):
        state = self.index('if-ne p2, v0, :done')
        self.assertEqual(self.code[state - 1], 'const/4 v0, 0x2')  # VolumeInfo.STATE_MOUNTED
        t = self.index(f'invoke-virtual {{p0}}, {WVI}->getType()I')
        self.assertEqual(self.code[t + 1:t + 3], ['move-result v1', 'if-ne v1, v0, :done'])  # TYPE_EMULATED
        u = self.index(f'invoke-virtual {{p0}}, {WVI}->getMountUserId()I')
        self.assertEqual(self.code[u + 1:u + 3], ['move-result v1', 'if-nez v1, :done'])
        i = self.index(f'invoke-virtual {{p0}}, {WVI}->getId()Ljava/lang/String;')
        self.assertEqual(self.code[i + 3], 'const-string v2, "emulated"')
        self.assertEqual(self.code[i + 4], 'invoke-virtual {v1, v2}, Ljava/lang/String;->startsWith(Ljava/lang/String;)Z')
        self.assertLess(state, t)

    def test_only_with_oneos_media_hold_and_once(self):
        flag = self.index('sget-boolean v1, Lcom/android/server/OneOSMediaHold;->sWaited:Z')
        self.assertEqual(self.code[flag + 1], 'if-nez v1, :done')
        hold = self.index('const-string v0, "oneos.media.hold"')
        self.assertEqual(self.code[hold - 1], 'const-string v1, "1"')
        self.assertEqual(self.code[hold + 3:hold + 6], [
            'invoke-virtual {v1, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z',
            'move-result v0', 'if-eqz v0, :done'])
        mark = self.index('sput-boolean v0, Lcom/android/server/OneOSMediaHold;->sWaited:Z')
        self.assertEqual(self.code[mark - 1], 'const/4 v0, 0x1')
        self.assertLess(hold, mark)
        self.assertLess(mark, self.index(':poll'))

    def test_polls_published_every_100_ms_for_15_s_at_most(self):
        poll = self.index(':poll')
        self.assertEqual(self.code[poll + 1], 'const-string v0, "oneos.media.published"')
        self.assertIn('if-nez v0, :published', self.code[poll:])
        cap = self.index('const-wide/16 v7, 0x3a98')
        self.assertEqual(0x3a98, 15000)
        self.assertEqual(self.code[cap + 1:cap + 3], ['cmp-long v0, v5, v7', 'if-gez v0, :timed_out'])
        sleep = self.index('invoke-static {v5, v6}, Ljava/lang/Thread;->sleep(J)V')
        self.assertEqual(self.code[sleep - 1], 'const-wide/16 v5, 0x64')
        self.assertEqual(self.code[sleep + 1], 'goto :poll')
        self.assertEqual(self.text.count('Landroid/os/SystemClock;->elapsedRealtime()J'), 3)

    def test_logs_how_long_and_why(self):
        for s in ('const-string v2, "published"', 'const-string v2, "timed out"', 'const-string v2, "interrupted"',
                  'const-string v1, "waited "', 'const-string v1, " ms for OneOS\\\'s albums ("',
                  'const-string v1, ")"', 'const-string v1, "OneOSMediaHold"',
                  'invoke-static {v1, v0}, Landroid/util/Slog;->i(Ljava/lang/String;Ljava/lang/String;)I'):
            self.index(s)

    def test_never_throws(self):
        start, end = self.index(':try_start_0'), self.index(':try_end_0')
        self.assertLess(start, self.index('const/4 v0, 0x2'))
        self.assertGreater(end, self.index('invoke-static {v1, v0}, Landroid/util/Slog;->i(Ljava/lang/String;Ljava/lang/String;)I'))
        self.assertEqual(self.code[end + 1:end + 3], [
            '.catch Ljava/lang/InterruptedException; {:try_start_0 .. :try_end_0} :interrupted',
            '.catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :failed'])
        # Only the start time is set before the try (a long both handlers see).
        self.assertEqual(self.code[start - 1], 'const-wide/16 v3, 0x0')
        failed = self.index(':failed')
        self.assertEqual(self.code[failed + 1:], ['return-void', '.end method'])
        intr = self.index(':interrupted')
        self.assertEqual(self.code[intr + 1:intr + 6], [
            'invoke-static {}, Ljava/lang/Thread;->currentThread()Ljava/lang/Thread;', 'move-result-object v0',
            'invoke-virtual {v0}, Ljava/lang/Thread;->interrupt()V', 'const-string v2, "interrupted"', 'goto :log'])
        # Instructions only between the try's labels or in its handlers.
        outside = self.code[self.index('.locals 9') + 1:start] + self.code[end + 3:intr]
        self.assertEqual([l for l in outside if not l.startswith('.')], ['const-wide/16 v3, 0x0', ':done', 'return-void'])
        for op in ('invoke-custom', 'invoke-polymorphic', 'monitor-enter', 'throw '):
            self.assertNotIn(op, self.text)


class Wiring(unittest.TestCase):
    def test_make_overlays_runs_it_with_the_services_patches(self):
        text = (ROOT / 'scripts/make-overlays.sh').read_text()
        line = 'KEY_DIRS="$KEY_DIRS $(python3 -I "$ROOT/switcher/patches/%s.py" "$WORK/services")"\n'
        hold = line % 'guest_media_mount_hold'
        self.assertEqual(text.count(hold), 1)
        self.assertLess(text.index(line % 'guest_storage_guards'), text.index(hold))
        self.assertLess(text.index(line % 'guest_no_update_engine'), text.index(hold))
        self.assertLess(text.index(hold), text.index('java -jar "$APKTOOL" b -q -o "$WORK/rebuilt.jar" "$WORK/services"'))
        self.assertLess(text.index(hold), text.index('cp "$WORK/services.jar" "$OUT/services_patched.jar"'))
        header = next(l for l in text.split('\n') if 'echo "# framework overlays built by' in l)
        self.assertRegex(header, r'\bksuappid and mediahold guards\b')

    def test_nothing_on_the_phone_requires_the_word(self):
        # Sets built before it (no mediahold in their guards.txt) keep starting.
        for script in ('guest_prepare.sh', 'oneos_switch.sh', 'guest_google.sh'):
            self.assertNotIn('mediahold', (ROOT / 'switcher/assets' / script).read_text(), script)


class Pins(unittest.TestCase):
    """The r6 files of x9u-in-1105-sultra, as the app downloads them."""
    PKG = ROOT / 'app-android/packages'

    def pins(self, name):
        return {l.split()[1]: l.split()[0] for l in name.read_text().split('\n')
                if re.match(r'^[0-9a-f]{64}  \S+$', l)}

    def test_guards_pin_the_lists_jar_and_name_mediahold(self):
        sha = self.pins(self.PKG / 'x9u-in-1105-sultra.sha256')
        guards = self.PKG / 'x9u-in-1105-sultra.guards.txt'
        self.assertEqual(hashlib.sha256(guards.read_bytes()).hexdigest(), sha['guards.txt'])
        self.assertEqual((self.PKG / 'set-files/x9u-in-1105-sultra/guards.txt').read_bytes(), guards.read_bytes())
        g = self.pins(guards)
        for n, h in g.items():
            self.assertEqual(sha[n], h, n)
        head = [l for l in guards.read_text().split('\n') if l.startswith('#')]
        self.assertRegex(head[0], r'\bmediahold\b')
        self.assertTrue(any('guest_media_mount_hold.py' in l for l in head))
        self.assertIn('r6: r5 plus services_patched.jar re-patched in place by patches/guest_media_mount_hold.py',
                      (self.PKG / 'x9u-in-1105-sultra.sha256').read_text())

    def test_r6_files_have_a_size_and_a_source(self):
        sha = self.pins(self.PKG / 'x9u-in-1105-sultra.sha256')
        remote = dict(l.split(' ', 1) for l in (self.PKG / 'remote.txt').read_text().split('\n')
                      if re.match(r'^[0-9a-f]{64} ', l))
        sizes = dict(l.split(' ', 1) for l in (self.PKG / 'sizes.txt').read_text().split('\n')
                     if re.match(r'^[0-9a-f]{64} ', l))
        for n in ('services_patched.jar', 'guards.txt'):
            self.assertEqual(remote[sha[n]], 'gh:DeathThoughts/OneOS-test:coloros-1105-r6', n)
            self.assertGreater(int(sizes[sha[n]]), 0, n)
        self.assertEqual(int(sizes[sha['guards.txt']]), (self.PKG / 'x9u-in-1105-sultra.guards.txt').stat().st_size)
        wf = (ROOT / '.github/workflows/publish-set-files.yml').read_text()
        self.assertIn('default: "coloros-1105-r6"', wf)


if __name__ == '__main__':
    unittest.main()
