"""Keep the guest's fingerprint sensor read-only, the way One UI's is.

The container shares the phone's fingerprint HAL.  One UI's enrolments live in
that HAL and in One UI's /data/system/users/0/settings_fingerprint*.xml; the
guest gets a mirror of the settings file but must never make an enrolment,
delete one, rename it, invalidate the authenticator id or clear the shared
lockout.  Authentication is the only operation it needs.

InternalCleanupClient is already stopped from deleting unknown HAL templates
(patches/guest_no_biometric_cleanup.py), but Settings can still call
IFingerprintService.enroll/remove/removeAll directly.  This patch guards the
binder implementation at its entry: the call gets the usual onError callback
and returns without touching a provider.  It also refuses the test-session
escape hatch and answers invalidation without changing an authenticator id.

The guard is deliberately *not* a best-effort skip.  If a build has one of the
required methods but its header cannot be edited safely, the build fails
instead of shipping a guest that can still write the phone's fingerprints.
The patch only touches FingerprintService$1; a same-named method in FaceService
or ServiceProvider is never a target.  Vendor extCmd is intentionally left
alone: on HyperOS it is used by FOD/AOD, not by enrolment/removal.

Usage: guest_fingerprint_readonly.py <smali root dir>
Prints the smali_* directories (dex files) it changed, one per line.
"""
import os
import re
import stat
import sys

RECEIVER = "Landroid/hardware/fingerprint/IFingerprintServiceReceiver;"
INVALIDATION = "Landroid/hardware/biometrics/IInvalidationCallback;"
TARGET_FILE = "FingerprintService$1.smali"
TARGET_CLASS = "Lcom/android/server/biometrics/sensors/fingerprint/FingerprintService$1;"
MARK = "# OneOS: fingerprint templates belong to One UI; the guest may authenticate only."

# name: (descriptor, required)
METHODS = {
    "enroll": (
        "(Landroid/os/IBinder;[BILandroid/hardware/fingerprint/IFingerprintServiceReceiver;"
        "Ljava/lang/String;ILandroid/hardware/fingerprint/FingerprintEnrollOptions;)J",
        True,
    ),
    "remove": (
        "(Landroid/os/IBinder;IILandroid/hardware/fingerprint/IFingerprintServiceReceiver;"
        "Ljava/lang/String;)V",
        True,
    ),
    "removeAll": (
        "(Landroid/os/IBinder;ILandroid/hardware/fingerprint/IFingerprintServiceReceiver;"
        "Ljava/lang/String;)V",
        True,
    ),
    "resetLockout": (
        "(Landroid/os/IBinder;II[BLjava/lang/String;)V",
        False,
    ),
    "invalidateAuthenticatorId": (
        "(IILandroid/hardware/biometrics/IInvalidationCallback;)V",
        False,
    ),
    "rename": (
        "(IILjava/lang/String;)V",
        False,
    ),
    "createTestSession": (
        "(ILandroid/hardware/biometrics/ITestSessionCallback;Ljava/lang/String;)"
        "Landroid/hardware/biometrics/ITestSession;",
        False,
    ),
}

# Number of local registers each guard needs.  We use v0/v1 for the error pair
# and the -1L return value.  Raising .locals is refused when the method already
# addresses a parameter through an absolute vN register: that would silently
# change the register mapping of existing code.
GUARD_LOCALS = {
    "enroll": 2,
    "remove": 2,
    "removeAll": 2,
    "createTestSession": 1,
    "resetLockout": 0,
    "invalidateAuthenticatorId": 0,
    "rename": 0,
}

GUARDS = {
    "enroll": """\
    {mark}
    if-eqz p4, :oneos_guest_fp_enroll_null
    const/4 v0, 0x2
    const/4 v1, 0x0
    invoke-interface {{p4, v0, v1}}, {recv}->onError(II)V
    :oneos_guest_fp_enroll_null
    const-wide/16 v0, -0x1
    return-wide v0
""",
    "remove": """\
    {mark}
    if-eqz p4, :oneos_guest_fp_remove_null
    const/4 v0, 0x2
    const/4 v1, 0x0
    invoke-interface {{p4, v0, v1}}, {recv}->onError(II)V
    :oneos_guest_fp_remove_null
    return-void
""",
    "removeAll": """\
    {mark}
    if-eqz p3, :oneos_guest_fp_remove_all_null
    const/4 v0, 0x2
    const/4 v1, 0x0
    invoke-interface {{p3, v0, v1}}, {recv}->onError(II)V
    :oneos_guest_fp_remove_all_null
    return-void
""",
    "resetLockout": """\
    {mark}
    # The lockout lives in the shared phone sensor; clearing it would change One UI's state.
    return-void
""",
    "invalidateAuthenticatorId": """\
    {mark}
    if-eqz p3, :oneos_guest_fp_invalidate_null
    invoke-interface {{p3}}, {invalidation}->onCompleted()V
    :oneos_guest_fp_invalidate_null
    # The authenticator id is shared with One UI; report completion without rotating it.
    return-void
""",
    "rename": """\
    {mark}
    return-void
""",
    "createTestSession": """\
    {mark}
    # Test sessions can reach enrolment/removal paths; there is no test session in the guest.
    const/4 v0, 0x0
    return-object v0
""",
}


def method_header(name, descriptor):
    # .method public enroll(...)J, possibly with a synthetic/bridge marker.
    return re.compile(r"^\s*\.method[^\n]*\b" + re.escape(name + descriptor) + r"\s*$", re.M)


def find_target(root):
    targets = []
    for directory, _, files in os.walk(root):
        for filename in files:
            if filename == TARGET_FILE:
                targets.append(os.path.join(directory, filename))
    if not targets:
        sys.exit(f"guest_fingerprint_readonly: required {TARGET_FILE} not found")
    if len(targets) != 1:
        sys.exit(
            "guest_fingerprint_readonly: ambiguous target; found "
            + ", ".join(targets)
        )
    return targets[0]


def find_methods(text):
    hits = []
    for name, (descriptor, required) in METHODS.items():
        matches = list(method_header(name, descriptor).finditer(text))
        if not matches:
            if required:
                sys.exit(f"guest_fingerprint_readonly: required method {name}{descriptor} not found")
            continue
        if len(matches) != 1:
            sys.exit(
                f"guest_fingerprint_readonly: ambiguous method {name}{descriptor}; "
                f"found {len(matches)} implementations"
            )
        match = matches[0]
        start = text.count("\n", 0, match.start())
        end = text.find("\n.end method", match.end())
        if end < 0:
            sys.exit(f"guest_fingerprint_readonly: {name}{descriptor} has no .end method")
        end_line = text.count("\n", 0, end) + 1
        hits.append((start, end_line, name))
    return hits


def _skip_header(body, local_line):
    """Return the first instruction index, refusing to enter annotations."""
    index = local_line + 1 if local_line is not None else 1
    in_annotation = False
    while index < len(body):
        stripped = body[index].strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if stripped.startswith(".annotation"):
            in_annotation = True
            index += 1
            continue
        if stripped.startswith(".end annotation"):
            in_annotation = False
            index += 1
            continue
        if in_annotation:
            index += 1
            continue
        if (stripped.startswith(".param") or stripped.startswith(".end param")
                or stripped.startswith(".prologue") or stripped.startswith(".line")
                or stripped.startswith(".local") or stripped.startswith(".end local")
                or stripped.startswith(".restart local")):
            index += 1
            continue
        break
    return index


def _find_permission(body, first_instruction):
    """Insert after the first *_enforcePermission call when one is present."""
    for index in range(first_instruction, len(body)):
        line = body[index]
        if "enforcePermission(" in line and "->" in line:
            return index + 1
    return first_instruction


def insert_guard(lines, start, end, name):
    body = lines[start:end + 1]
    local_line = next(
        (i for i, line in enumerate(body)
         if re.fullmatch(r"\s*\.locals\s+\d+\s*", line)),
        None,
    )
    register_line = next(
        (i for i, line in enumerate(body)
         if re.fullmatch(r"\s*\.registers\s+\d+\s*", line)),
        None,
    )
    if register_line is not None:
        sys.exit(f"{name}: .registers header is not supported; refusing to guess its layout")

    needed = GUARD_LOCALS[name]
    if local_line is None:
        if needed:
            sys.exit(f"{name}: no .locals in the method; refusing to guess its register layout")
    else:
        count = int(re.search(r"\d+", body[local_line]).group())
        if count < needed:
            # Raising .locals shifts pN -> v(N+locals).  Existing code that
            # spells a parameter as an absolute vN would silently change.
            for line in body[local_line + 1:]:
                code = line.split("#", 1)[0]
                for match in re.finditer(r"\bv(\d+)\b", code):
                    if int(match.group(1)) >= count:
                        sys.exit(
                            f"{name}: cannot raise .locals safely; "
                            f"absolute {match.group(0)} addresses a parameter"
                        )
            body[local_line] = re.sub(
                r"\.locals\s+\d+", f".locals {needed}", body[local_line], count=1
            )

    first_instruction = _skip_header(body, local_line)
    if first_instruction >= len(body) or body[first_instruction].strip().startswith(".end method"):
        sys.exit(f"{name}: no code position for the guard")
    insert_at = _find_permission(body, first_instruction)

    end_method = next(
        (i for i in range(len(body) - 1, -1, -1)
         if body[i].strip().startswith(".end method")),
        None,
    )
    if end_method is None:
        sys.exit(f"{name}: no .end method line")
    guard = GUARDS[name].format(mark=MARK, recv=RECEIVER, invalidation=INVALIDATION)
    # Every guard returns on every path.  Drop the original body instead of
    # leaving unreachable instructions that can confuse the verifier.
    body[insert_at:end_method] = guard.splitlines(keepends=True)
    lines[start:end + 1] = body


def atomic_write(path, text):
    tmp = path + ".oneos-tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(root):
    path = find_target(root)
    text = open(path, encoding="utf-8").read()
    class_line = next((line for line in text.splitlines() if line.startswith(".class ")), "")
    if TARGET_CLASS not in class_line:
        sys.exit(
            f"guest_fingerprint_readonly: {path} is not {TARGET_CLASS}; refusing to patch"
        )

    hits = find_methods(text)
    lines = text.splitlines(keepends=True)
    pending = []
    for start, end, name in hits:
        body_text = "".join(lines[start:end + 1])
        if MARK in body_text:
            if name in ("enroll", "remove", "removeAll") and "onError(II)V" not in body_text:
                sys.exit(
                    f"{name}: old or incomplete OneOS guard found; remove it and re-run"
                )
            continue
        pending.append((start, end, name))

    for start, end, name in sorted(pending, reverse=True):
        insert_guard(lines, start, end, name)

    if pending:
        atomic_write(path, "".join(lines))
        print(os.path.relpath(os.path.dirname(path), root).split(os.sep)[0])
        for _, _, name in pending:
            print(f"{name}: guarded", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: guest_fingerprint_readonly.py <smali root dir>")
    main(sys.argv[1])
