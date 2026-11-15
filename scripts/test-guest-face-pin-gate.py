#!/usr/bin/env python3
"""Static checks for patches/guest_face_pin_gate.py on a synthetic FaceServiceWrapper."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'switcher' / 'patches' / 'guest_face_pin_gate.py'
TREE = 'smali/com/android/server/biometrics/sensors/face/FaceService$FaceServiceWrapper.smali'
MARK = "# OneOS: face writes only to the guest's own face HAL, right after the guest's PIN."
HELPER = 'Lcom/android/server/locksettings/OneOSGuestLock;'
CLS = 'Lcom/android/server/biometrics/sensors/face/FaceService$FaceServiceWrapper;'

HEADER = f'''.class final {CLS}
.super Landroid/hardware/face/IFaceService$Stub;
.source "FaceService.java"

'''


def method(sig, locals_, params, body):
    return f'''.method public {sig}
    .locals {locals_}
{params}
    .line 1
{body}
.end method

'''


ENROLL_SIG = ('enroll(ILandroid/os/IBinder;[BLandroid/hardware/face/IFaceServiceReceiver;Ljava/lang/String;'
              '[ILandroid/view/Surface;ZLandroid/hardware/face/FaceEnrollOptions;)J')
METHODS = {
    'enroll': method(ENROLL_SIG, 13, '    .param p4, "receiver"    # Landroid/hardware/face/IFaceServiceReceiver;',
                     '    invoke-super {p0}, Landroid/hardware/face/IFaceService$Stub;->enroll_enforcePermission()V\n'
                     '    const-wide/16 v0, 0x5\n    return-wide v0'),
    'remove': method('remove(Landroid/os/IBinder;IILandroid/hardware/face/IFaceServiceReceiver;Ljava/lang/String;)V', 9,
                     '    .param p4, "receiver"    # Landroid/hardware/face/IFaceServiceReceiver;',
                     '    invoke-super {p0}, Landroid/hardware/face/IFaceService$Stub;->remove_enforcePermission()V\n'
                     '    return-void'),
    'removeAll': method('removeAll(Landroid/os/IBinder;ILandroid/hardware/face/IFaceServiceReceiver;Ljava/lang/String;)V', 10,
                        '    .param p3, "receiver"    # Landroid/hardware/face/IFaceServiceReceiver;',
                        '    invoke-super {p0}, Landroid/hardware/face/IFaceService$Stub;->removeAll_enforcePermission()V\n'
                        '    return-void'),
    'invalidateAuthenticatorId': method('invalidateAuthenticatorId(IILandroid/hardware/biometrics/IInvalidationCallback;)V', 3,
                                        '    .param p3, "callback"    # Landroid/hardware/biometrics/IInvalidationCallback;',
                                        '    invoke-super {p0}, Landroid/hardware/face/IFaceService$Stub;'
                                        '->invalidateAuthenticatorId_enforcePermission()V\n    return-void'),
    'createTestSession': method('createTestSession(ILandroid/hardware/biometrics/ITestSessionCallback;Ljava/lang/String;)'
                                'Landroid/hardware/biometrics/ITestSession;', 3, '',
                                '    invoke-super {p0}, Landroid/hardware/face/IFaceService$Stub;'
                                '->createTestSession_enforcePermission()V\n    new-instance v0, Ljava/lang/Object;\n'
                                '    const/4 v0, 0x0\n    return-object v0'),
    'authenticate': method('authenticate(Landroid/os/IBinder;JLandroid/hardware/face/IFaceServiceReceiver;'
                           'Landroid/hardware/face/FaceAuthenticateOptions;)J', 1, '',
                           '    const-wide/16 v0, 0x1\n    return-wide v0'),
}


class FacePinGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oneos-face-gate-')
        self.root = Path(self.temp.name)
        self.file = self.root / TREE
        self.file.parent.mkdir(parents=True)
        self.file.write_text(HEADER + ''.join(METHODS.values()))

    def tearDown(self):
        self.temp.cleanup()

    def run_patch(self):
        return subprocess.run([sys.executable, '-I', str(PATCH), str(self.root)], text=True, capture_output=True)

    def body(self, name):
        text = self.file.read_text()
        start = text.index('.method public ' + name + '(')
        return text[start:text.index('\n.end method', start)]

    def test_writes_are_gated_after_the_permission_check(self):
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ['smali'])
        for name, user, receiver in (('enroll', 'p1', 'p4'), ('remove', 'p3', 'p4'), ('removeAll', 'p2', 'p3')):
            body = self.body(name)
            self.assertLess(body.index('_enforcePermission()V'), body.index(MARK), name)
            self.assertIn('Landroid/os/SystemClock;->elapsedRealtime()J', body)
            self.assertIn(f'move/from16 v2, {user}', body)
            self.assertIn(f'invoke-static {{v2, v0, v1}}, {HELPER}->faceWriteAllowed(IJ)Z', body)
            self.assertIn(f'if-eqz {receiver}, ', body)
            self.assertIn('IFaceServiceReceiver;->onError(II)V', body)
            # The original code still runs when the gate allows it.
            self.assertGreater(body.index('return'), body.index(MARK))
        self.assertIn('const-wide/16 v0, -0x1', self.body('enroll'))
        self.assertIn('const-wide/16 v0, 0x5', self.body('enroll'))

    def test_invalidation_only_with_the_guests_face_hal(self):
        self.run_patch()
        body = self.body('invalidateAuthenticatorId')
        self.assertIn(f'{HELPER}->faceHalIsGuests()Z', body)
        self.assertIn('IInvalidationCallback;->onCompleted()V', body)

    def test_no_test_session(self):
        self.run_patch()
        body = self.body('createTestSession')
        self.assertIn(MARK, body)
        self.assertNotIn('new-instance', body)

    def test_authentication_untouched(self):
        before = self.body('authenticate')
        self.run_patch()
        self.assertEqual(before, self.body('authenticate'))

    def test_locals_are_raised_only_when_safe(self):
        text = self.file.read_text().replace(
            '.method public removeAll(Landroid/os/IBinder;ILandroid/hardware/face/IFaceServiceReceiver;Ljava/lang/String;)V\n    .locals 10',
            '.method public removeAll(Landroid/os/IBinder;ILandroid/hardware/face/IFaceServiceReceiver;Ljava/lang/String;)V\n    .locals 1')
        self.file.write_text(text)
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('.locals 3', self.body('removeAll'))
        text = self.file.read_text()
        self.file.write_text(HEADER + ''.join(METHODS.values()).replace(
            'createTestSession_enforcePermission()V\n    new-instance v0',
            'createTestSession_enforcePermission()V\n    move-object v5, p0\n    new-instance v0').replace(
            'createTestSession(ILandroid/hardware/biometrics/ITestSessionCallback;Ljava/lang/String;)'
            'Landroid/hardware/biometrics/ITestSession;\n    .locals 3',
            'createTestSession(ILandroid/hardware/biometrics/ITestSessionCallback;Ljava/lang/String;)'
            'Landroid/hardware/biometrics/ITestSession;\n    .locals 1'))
        done = self.run_patch()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('cannot raise .locals safely', done.stderr)

    def test_second_run_is_a_no_op(self):
        self.run_patch()
        after = self.file.read_text()
        done = self.run_patch()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual('', done.stdout)
        self.assertEqual(after, self.file.read_text())

    def test_missing_required_method_fails(self):
        self.file.write_text(HEADER + METHODS['enroll'] + METHODS['removeAll'])
        done = self.run_patch()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('required method remove', done.stderr)

    def test_build_runs_it_after_the_lock_helper(self):
        text = (ROOT / 'scripts' / 'make-overlays.sh').read_text()
        here = text.index('guest_face_pin_gate.py')
        self.assertGreater(here, text.index('switcher/patches/guest_local_lock.py'))
        self.assertLess(here, text.index('java -jar "$APKTOOL" b'))


if __name__ == '__main__':
    unittest.main()
