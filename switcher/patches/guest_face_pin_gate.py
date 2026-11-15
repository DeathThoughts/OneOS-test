"""Face in the guest: only the guest's own, and only right after the guest's PIN.

Two face HALs can serve the guest's FaceService:

- facehal-rs, the guest's own (guest_start.sh starts it when its payload is
  staged and writes /data/system/oneos_facehal_active): templates live in the guest.
- Samsung's face HAL, the phone's, when facehal-rs does not run (the Galaxy S23
  has no payload): its templates are One UI's face.

Enrolling or removing a face must never reach Samsung's HAL, and with
facehal-rs it must follow the guest lock's credential, as One UI asks for its
PIN.  FaceManager.remove/removeAll carry no token at all, so the gate sits in
the binder implementation, FaceService$FaceServiceWrapper, right after its
permission check: OneOSGuestLock.faceWriteAllowed(userId, now) (facehal-rs serving
and the guest credential matched within the last minutes) or the caller gets
FACE_ERROR_UNABLE_TO_PROCESS and nothing is scheduled.  enroll and
enrollRemotely get the same gate; facehal-rs also refuses an enrollment whose
token is not the fresh one the guest lock issued.

invalidateAuthenticatorId carries no token either and would rotate One UI's
face authenticator id on Samsung's HAL (and with it the keys bound to it):
without facehal-rs it answers completion and changes nothing.
createTestSession reaches enrollment and removal without any check: the guest
has none.

The internal cleanup (FaceInternalCleanupClient) does not come through this
wrapper; patches/guest_no_biometric_cleanup.py already keeps it from removing
templates the guest does not list.

Fails the build when a required method is missing or cannot be guarded safely.

Usage: guest_face_pin_gate.py <smali root dir>
Prints the smali_* directory (dex file) it changed.
"""
import os
import re
import sys

TARGET_FILE = "FaceService$FaceServiceWrapper.smali"
TARGET_CLASS = "Lcom/android/server/biometrics/sensors/face/FaceService$FaceServiceWrapper;"
HELPER = "Lcom/android/server/locksettings/OneOSGuestLock;"
RECEIVER = "Landroid/hardware/face/IFaceServiceReceiver;"
INVALIDATION = "Landroid/hardware/biometrics/IInvalidationCallback;"
MARK = "# OneOS: face writes only to the guest's own face HAL, right after the guest's PIN."

# name: (descriptor, required, user register, receiver register, return kind)
METHODS = {
    "enroll": (
        "(ILandroid/os/IBinder;[BLandroid/hardware/face/IFaceServiceReceiver;Ljava/lang/String;"
        "[ILandroid/view/Surface;ZLandroid/hardware/face/FaceEnrollOptions;)J",
        True, "p1", "p4", "J",
    ),
    "enrollRemotely": (
        "(ILandroid/os/IBinder;[BLandroid/hardware/face/IFaceServiceReceiver;Ljava/lang/String;[I)J",
        False, "p1", "p4", "J",
    ),
    "remove": (
        "(Landroid/os/IBinder;IILandroid/hardware/face/IFaceServiceReceiver;Ljava/lang/String;)V",
        True, "p3", "p4", "V",
    ),
    "removeAll": (
        "(Landroid/os/IBinder;ILandroid/hardware/face/IFaceServiceReceiver;Ljava/lang/String;)V",
        True, "p2", "p3", "V",
    ),
    "invalidateAuthenticatorId": (
        "(IILandroid/hardware/biometrics/IInvalidationCallback;)V",
        False, None, "p3", "invalidate",
    ),
    "createTestSession": (
        "(ILandroid/hardware/biometrics/ITestSessionCallback;Ljava/lang/String;)"
        "Landroid/hardware/biometrics/ITestSession;",
        False, None, None, "none",
    ),
}
NEEDED_LOCALS = 3


def guard(name, user, receiver, kind):
    label = "oneos_face_" + re.sub(r"[^a-z]", "_", name.lower())
    if kind == "none":
        return [MARK, "const/4 v0, 0x0", "return-object v0"], True
    if kind == "invalidate":
        return [
            MARK,
            f"invoke-static {{}}, {HELPER}->faceHalIsGuests()Z",
            "move-result v0",
            f"if-nez v0, :{label}_ok",
            f"if-eqz {receiver}, :{label}_null",
            f"move-object/from16 v0, {receiver}",
            f"invoke-interface {{v0}}, {INVALIDATION}->onCompleted()V",
            f":{label}_null",
            "return-void",
            f":{label}_ok",
        ], False
    out = [
        MARK,
        "invoke-static {}, Landroid/os/SystemClock;->elapsedRealtime()J",
        "move-result-wide v0",
        f"move/from16 v2, {user}",
        f"invoke-static {{v2, v0, v1}}, {HELPER}->faceWriteAllowed(IJ)Z",
        "move-result v0",
        f"if-nez v0, :{label}_ok",
        f"if-eqz {receiver}, :{label}_null",
        f"move-object/from16 v0, {receiver}",
        "const/4 v1, 0x2",
        "const/4 v2, 0x0",
        f"invoke-interface {{v0, v1, v2}}, {RECEIVER}->onError(II)V",
        f":{label}_null",
    ]
    out += ["const-wide/16 v0, -0x1", "return-wide v0"] if kind == "J" else ["return-void"]
    out.append(f":{label}_ok")
    return out, False


def find_target(root):
    hits = [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f == TARGET_FILE]
    if len(hits) != 1:
        sys.exit(f"guest_face_pin_gate: expected one {TARGET_FILE}, found {len(hits)}")
    return hits[0]


def method_span(lines, name, descriptor):
    want = re.compile(r"^\.method\b[^\n]*\s" + re.escape(name + descriptor) + r"\s*$")
    starts = [i for i, line in enumerate(lines) if want.match(line.rstrip("\n"))]
    if not starts:
        return None
    if len(starts) != 1:
        sys.exit(f"guest_face_pin_gate: {name}{descriptor} found {len(starts)} times")
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith(".end method")), None)
    if end is None:
        sys.exit(f"guest_face_pin_gate: {name} has no .end method")
    return start, end


def first_instruction(lines, start, end):
    i = start + 1
    in_annotation = False
    while i < end:
        s = lines[i].strip()
        if in_annotation:
            in_annotation = not s.startswith(".end annotation")
        elif s.startswith(".annotation"):
            in_annotation = True
        elif not (s == "" or s.startswith("#") or s.startswith(".locals") or s.startswith(".param")
                  or s.startswith(".end param") or s.startswith(".line") or s.startswith(".prologue")):
            return i
        i += 1
    sys.exit("guest_face_pin_gate: a guarded method has no code")


def ensure_locals(lines, start, end, name):
    at = next((i for i in range(start + 1, end) if re.fullmatch(r"\s*\.locals\s+\d+\s*", lines[i])), None)
    if at is None:
        sys.exit(f"guest_face_pin_gate: {name} has no .locals line; refusing to guess its registers")
    count = int(re.search(r"\d+", lines[at]).group())
    if count >= NEEDED_LOCALS:
        return
    # Raising .locals renumbers the parameters; only safe when the body names them as pN.
    for line in lines[at + 1:end]:
        for m in re.finditer(r"\bv(\d+)\b", line.split("#", 1)[0]):
            if int(m.group(1)) >= count:
                sys.exit(f"guest_face_pin_gate: {name}: cannot raise .locals safely ({m.group(0)})")
    lines[at] = re.sub(r"\.locals\s+\d+", f".locals {NEEDED_LOCALS}", lines[at], count=1)


def main(root):
    path = find_target(root)
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    class_line = next((l for l in lines if l.startswith(".class ")), "")
    if TARGET_CLASS not in class_line:
        sys.exit(f"guest_face_pin_gate: {path} is not {TARGET_CLASS}")
    if MARK in "".join(lines):
        return
    spans = []
    for name, (descriptor, required, user, receiver, kind) in METHODS.items():
        span = method_span(lines, name, descriptor)
        if span is None:
            if required:
                sys.exit(f"guest_face_pin_gate: required method {name}{descriptor} not found")
            continue
        spans.append((span[0], span[1], name, user, receiver, kind))
    for start, end, name, user, receiver, kind in sorted(spans, reverse=True):
        ensure_locals(lines, start, end, name)
        first = first_instruction(lines, start, end)
        at = first + 1 if re.match(r"\s*invoke-\S+ \{p0\}, \S+->\w+_enforcePermission\(\)V\s*$", lines[first]) else first
        body, replaces = guard(name, user, receiver, kind)
        text = ["    " + b + "\n" for b in body]
        if replaces:
            lines[at:end] = text
        else:
            lines[at:at] = text + ["\n"]
    tmp = path + ".oneos-tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write("".join(lines))
    os.replace(tmp, path)
    print(os.path.relpath(path, root).split(os.sep)[0])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: guest_face_pin_gate.py <smali root dir>")
    main(sys.argv[1])
