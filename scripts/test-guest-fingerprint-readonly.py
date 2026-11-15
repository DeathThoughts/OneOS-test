#!/usr/bin/env python3
"""Static checks for the guest fingerprint read-only binder guard."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'switcher' / 'patches' / 'guest_fingerprint_readonly.py'
TREE = 'smali_classes2/com/android/server/biometrics/sensors/fingerprint/FingerprintService$1.smali'
FACE_TREE = 'smali_classes2/com/android/server/biometrics/sensors/fingerprint/FaceService.smali'
PROVIDER_TREE = 'smali_classes2/com/android/server/biometrics/sensors/fingerprint/ServiceProvider.smali'
MARK = '# OneOS: fingerprint templates belong to One UI; the guest may authenticate only.'

HEADER = '''.class public final Lcom/android/server/biometrics/sensors/fingerprint/FingerprintService$1;
.super Landroid/hardware/fingerprint/IFingerprintService$Stub;
.source "FingerprintService.java"

'''

FACE_HEADER = '''.class public final Lcom/android/server/biometrics/sensors/fingerprint/FaceService;
.super Ljava/lang/Object;
.source "FaceService.java"

'''

PROVIDER_HEADER = '''.class public abstract Lcom/android/server/biometrics/sensors/fingerprint/ServiceProvider;
.super Ljava/lang/Object;
.source "ServiceProvider.java"

'''

METHODS = {
    'enroll': '''.method public enroll(Landroid/os/IBinder;[BILandroid/hardware/fingerprint/IFingerprintServiceReceiver;Ljava/lang/String;ILandroid/hardware/fingerprint/FingerprintEnrollOptions;)J
    .locals 1
    .param p4, "receiver"    # Landroid/hardware/fingerprint/IFingerprintServiceReceiver;
    invoke-static {p1}, Lcom/android/server/biometrics/sensors/fingerprint/FingerprintService;->enroll_enforcePermission()V
    const-wide/16 v0, 0x0
    return-wide v0
.end method
''',
    'remove': '''.method public remove(Landroid/os/IBinder;IILandroid/hardware/fingerprint/IFingerprintServiceReceiver;Ljava/lang/String;)V
    .locals 1
    .annotation system Ldalvik/annotation/Throws;
        value = {
            Landroid/os/RemoteException;
        }
    .end annotation
    .param p4, "receiver"    # Landroid/hardware/fingerprint/IFingerprintServiceReceiver;
    return-void
.end method
''',
    'removeAll': '''.method public removeAll(Landroid/os/IBinder;ILandroid/hardware/fingerprint/IFingerprintServiceReceiver;Ljava/lang/String;)V
    .locals 1
    .param p3, "receiver"    # Landroid/hardware/fingerprint/IFingerprintServiceReceiver;
    return-void
.end method
''',
    'resetLockout': '''.method public resetLockout(Landroid/os/IBinder;II[BLjava/lang/String;)V
    .locals 1
    return-void
.end method
''',
    'invalidateAuthenticatorId': '''.method public invalidateAuthenticatorId(IILandroid/hardware/biometrics/IInvalidationCallback;)V
    .locals 1
    .param p3, "callback"    # Landroid/hardware/biometrics/IInvalidationCallback;
    return-void
.end method
''',
    'rename': '''.method public rename(IILjava/lang/String;)V
    .locals 1
    return-void
.end method
''',
    'extCmd': '''.method public extCmd(Landroid/os/IBinder;IIILjava/lang/String;)I
    .locals 1
    const/4 v0, 0x7
    return v0
.end method
''',
    'createTestSession': '''.method public createTestSession(ILandroid/hardware/biometrics/ITestSessionCallback;Ljava/lang/String;)Landroid/hardware/biometrics/ITestSession;
    .locals 1
    const/4 v0, 0x0
    return-object v0
.end method
''',
    'authenticate': '''.method public authenticate(Landroid/os/IBinder;JLandroid/hardware/fingerprint/IFingerprintServiceReceiver;Landroid/hardware/fingerprint/FingerprintAuthenticateOptions;)J
    .locals 1
    const-wide/16 v0, 0x1
    return-wide v0
.end method
''',
}


class FingerprintReadonlyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oneos-fingerprint-')
        self.root = Path(self.temp.name)
        self.file = self.root / TREE
        self.file.parent.mkdir(parents=True)
        self.file.write_text(HEADER + ''.join(METHODS.values()))

    def tearDown(self):
        self.temp.cleanup()

    def run_patch(self):
        return subprocess.run([sys.executable, '-I', str(PATCH), str(self.root)],
                              text=True, capture_output=True)

    def text(self):
        return self.file.read_text()

    def method(self, name):
        text = self.text()
        start = text.index('.method public ' + name + '(')
        end = text.index('\n.end method', start)
        return text[start:end]

    def test_guards_every_write_path(self):
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ['smali_classes2'])
        for name in ('enroll', 'remove', 'removeAll'):
            body = self.method(name)
            self.assertIn(MARK, body)
            self.assertIn('onError(II)V', body)
            self.assertNotIn('onEnrollmentError', body)
            self.assertNotIn('onRemovalError', body)
        self.assertIn('onCompleted()V', self.method('invalidateAuthenticatorId'))
        self.assertIn('return-object v0', self.method('createTestSession'))
        for name in ('resetLockout', 'rename'):
            self.assertIn(MARK, self.method(name))
            self.assertIn('return-void', self.method(name))

    def test_receivers_go_through_a_low_register(self):
        # invoke-interface takes v0-v15 only; ColorOS 17's enroll has p4 at v17.
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        for name, recv in (('enroll', 'p4'), ('remove', 'p4'), ('removeAll', 'p3')):
            body = self.method(name)
            self.assertIn(f'move-object/from16 v2, {recv}', body)
            self.assertIn('invoke-interface {v2, v0, v1}', body)
            self.assertNotIn(f'invoke-interface {{{recv}', body)
        body = self.method('invalidateAuthenticatorId')
        self.assertIn('move-object/from16 v0, p3', body)
        self.assertIn('invoke-interface {v0}', body)

    def test_null_receivers_are_checked(self):
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('if-eqz p4, :oneos_guest_fp_enroll_null', self.method('enroll'))
        self.assertIn('if-eqz p4, :oneos_guest_fp_remove_null', self.method('remove'))
        self.assertIn('if-eqz p3, :oneos_guest_fp_remove_all_null', self.method('removeAll'))
        self.assertIn('if-eqz p3, :oneos_guest_fp_invalidate_null',
                      self.method('invalidateAuthenticatorId'))

    def test_guard_comes_after_permission(self):
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        body = self.method('enroll')
        self.assertLess(body.index('enforcePermission'), body.index(MARK))
        self.assertLess(body.index('enforcePermission'), body.index('onError(II)V'))

    def test_permission_check_further_down_is_not_the_insertion_point(self):
        text = self.text().replace(
            "    invoke-static {p1}, Lcom/android/server/biometrics/sensors/fingerprint/FingerprintService;->enroll_enforcePermission()V\n",
            "    const/4 v0, 0x0\n"
            "    invoke-static {p1}, Lcom/android/server/biometrics/sensors/fingerprint/FingerprintService;->enroll_enforcePermission()V\n",
            1,
        )
        self.file.write_text(text)
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        body = self.method('enroll')
        self.assertNotIn('enforcePermission', body)
        self.assertLess(body.index(MARK), body.index('return-wide'))

    def test_annotation_is_not_split(self):
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        body = self.method('remove')
        self.assertGreater(body.index(MARK), body.index('.end annotation'))

    def test_authentication_is_untouched(self):
        before = self.method('authenticate')
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(before, self.method('authenticate'))
        self.assertNotIn(MARK, self.method('authenticate'))

    def test_ext_cmd_is_untouched(self):
        before = self.method('extCmd')
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(before, self.method('extCmd'))
        self.assertNotIn(MARK, self.method('extCmd'))

    def test_other_classes_are_not_targets(self):
        face = self.root / FACE_TREE
        face.write_text(FACE_HEADER + '''.method public abstract createTestSession(ILandroid/hardware/biometrics/ITestSessionCallback;Ljava/lang/String;)Landroid/hardware/biometrics/ITestSession;
.end method
''')
        provider = self.root / PROVIDER_TREE
        provider.write_text(PROVIDER_HEADER + '''.method public abstract createTestSession(ILandroid/hardware/biometrics/ITestSessionCallback;Ljava/lang/String;)Landroid/hardware/biometrics/ITestSession;
.end method
''')
        before_face = face.read_text()
        before_provider = provider.read_text()
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(before_face, face.read_text())
        self.assertEqual(before_provider, provider.read_text())

    def test_locals_are_raised_for_the_guard(self):
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        for name in ('enroll', 'remove', 'removeAll'):
            self.assertIn('    .locals 3\n', self.method(name))
        for name in ('createTestSession', 'resetLockout', 'invalidateAuthenticatorId', 'rename'):
            self.assertIn('    .locals 1\n', self.method(name))

    def test_second_run_is_a_no_op(self):
        first = self.run_patch()
        self.assertEqual(first.returncode, 0, first.stderr)
        after = self.text()
        second = self.run_patch()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.strip(), '')
        self.assertEqual(after, self.text())

    def test_missing_required_method_fails_closed(self):
        text = self.text().replace(METHODS['remove'], '')
        self.file.write_text(text)
        done = self.run_patch()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('required method remove', done.stderr)

    def test_missing_optional_method_is_ok(self):
        text = self.text().replace(METHODS['rename'], '')
        self.file.write_text(text)
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('rename', done.stdout)

    def test_unexpected_header_is_not_guessed(self):
        text = self.text().replace(METHODS['enroll'].split('\n')[1], '    .registers 2')
        self.file.write_text(text)
        done = self.run_patch()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('.registers header is not supported', done.stderr)

    def test_absolute_parameter_register_blocks_locals_raise(self):
        text = self.text().replace(
            '    const-wide/16 v0, 0x0\n    return-wide v0\n.end method',
            '    const-wide/16 v0, 0x0\n    move-object v1, p0\n    return-wide v0\n.end method',
            1,
        )
        self.file.write_text(text)
        done = self.run_patch()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('cannot raise .locals safely', done.stderr)

    def test_ambiguous_method_fails_closed(self):
        text = self.text().replace(METHODS['remove'], METHODS['remove'] + METHODS['remove'])
        self.file.write_text(text)
        done = self.run_patch()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('ambiguous method remove', done.stderr)

    def test_wrong_class_fails_closed(self):
        text = self.text().replace(
            'Lcom/android/server/biometrics/sensors/fingerprint/FingerprintService$1;',
            'Lcom/example/FingerprintService$1;',
            1,
        )
        self.file.write_text(text)
        done = self.run_patch()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('is not', done.stderr)

    def test_build_runs_it_after_cleanup_and_force_aidl(self):
        text = (ROOT / 'scripts' / 'make-overlays.sh').read_text()
        here = text.index('guest_fingerprint_readonly.py')
        self.assertGreater(here, text.index('guest_no_biometric_cleanup.py'))
        self.assertGreater(here, text.index('guest_force_aidl_fingerprint.py'))
        self.assertLess(here, text.index('java -jar "$APKTOOL" b'))


if __name__ == '__main__':
    unittest.main()
