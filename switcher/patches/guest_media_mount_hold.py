"""Hold MediaProvider's mount scan until OneOS's One UI albums are mounted.

guest_start.sh binds One UI's photo albums into the guest's
/data/media/0/OneUI/<album>, and it can only do that after the guest's vold
has built its storage views: vold's BindMount is MS_BIND, not recursive, so a
bind made earlier never shows in the views. As soon as vold reports the
emulated volume MOUNTED, StorageManagerService.onVolumeStateChangedAsync calls
StorageSessionController.notifyVolumeStateChanged, and MediaProvider's
ExternalStorageServiceImpl.onVolumeStateChanged attaches the volume and queues
a full scan (MediaService.queueVolumeScan, REASON_MOUNTED). That scan walked
the volume a few seconds before the albums were there, found OneUI/ empty and
deleted every row, then everything was indexed again (S25 Ultra, ColorOS 17
coloros17-findx9u-in-1105, session a12bf19e1931e81a, guest/takeover.log:
MediaStore listed 4560 items under OneUI/ 25 s after the zygote started, kept
from the last session; 0 at 38 s; OneOS's own scan request only at 70 s, then
~100 s of re-indexing and ~106,000 MediaProvider log lines).

So the call to notifyVolumeStateChanged is preceded by a call to
OneOSMediaHold.await (OneOSMediaHold.smali, copied next to
StorageManagerService): for user 0's emulated volume going MOUNTED, once per
system_server and only when guest_start.sh set oneos.media.hold=1, it polls
oneos.media.published every 100 ms, for 15 s at most, and logs how long it
waited. The call sits after mLock's monitor-exit (the framework calls the
storage service without the lock on purpose), on StorageManagerService's own
handler thread; the Watchdog only checks mLock (addMonitor), not that thread.
It takes p1 .. p3 as a range, so it needs no free register whatever the
method's register count, and it has its own catch Throwable that goes straight
on to the notify call: the helper catches everything it throws itself, but a
helper class that failed to load or verify would otherwise throw out of the
handler thread and take system_server down. Without oneos.media.hold=1 (an
older guest_start.sh, or no albums to share) it returns at once.

A framework whose onVolumeStateChangedAsync takes android.os.storage.VolumeInfo
instead of WatchedVolumeInfo gets the helper with that type written in (both
have getType, getMountUserId and getId). A framework of any other shape stops
the build: the anchor must be found exactly once.

Idempotent: a second run over the same tree changes nothing and prints nothing.

Usage: guest_media_mount_hold.py <smali root dir> [helper smali]
Prints the smali_* directory (dex file) it changed.
"""
import os
import re
import sys

SMS = os.path.join("com", "android", "server", "StorageManagerService.smali")
CLS = "Lcom/android/server/StorageManagerService;"
HELPER_NAME = "OneOSMediaHold.smali"
HELPER = "Lcom/android/server/OneOSMediaHold;"
WATCHED = "Lcom/android/server/storage/WatchedVolumeInfo;"
VOLUME_TYPES = (WATCHED, "Landroid/os/storage/VolumeInfo;")
METHOD = re.compile(r"^\.method (?:[a-z-]+ )*onVolumeStateChangedAsync\((L[^;]+;)II\)V$")
NOTIFY = re.compile(
    r"^([ \t]*)invoke-virtual \{[vp]\d+, [vp]\d+\}, Lcom/android/server/storage/StorageSessionController;"
    r"->notifyVolumeStateChanged\(L[^;]+;\)V$")
MONITOR = re.compile(r"^\s*monitor-(enter|exit) [vp]\d+$")
MARK = ":oneos_media_hold_start"


def fail(msg):
    sys.exit("guest_media_mount_hold: " + msg + "; not patched")


def block(ind, vol):
    return [
        f"{ind}# OneOS (S25 Ultra, ColorOS 17, session a12bf19e1931e81a): MediaProvider's mount scan",
        f"{ind}# waits up to 15 s for OneOS's albums under OneUI/ (OneOSMediaHold); no lock held here.",
        f"{ind}# A helper that cannot load or verify must not stop the mount: its catch goes on below.",
        f"{ind}{MARK}",
        f"{ind}invoke-static/range {{p1 .. p3}}, {HELPER}->await({vol}II)V",
        f"{ind}:oneos_media_hold_end",
        f"{ind}.catch Ljava/lang/Throwable; {{{MARK} .. :oneos_media_hold_end}} :oneos_media_hold_done",
        "",
        f"{ind}:oneos_media_hold_done",
    ]


def main(root, helper):
    if not os.path.isfile(helper):
        fail(f"no helper smali at {helper}")
    dirs = [d for d in sorted(os.listdir(root))
            if d.startswith("smali") and os.path.isfile(os.path.join(root, d, SMS))]
    if len(dirs) != 1:
        fail(f"expected StorageManagerService.smali in exactly one smali dir, found {len(dirs)}")
    d = dirs[0]
    path = os.path.join(root, d, SMS)
    rel = os.path.join(d, SMS)
    with open(path) as f:
        lines = f.read().split("\n")
    if not any(re.match(r"^\.class .*" + re.escape(CLS) + r"$", l) for l in lines):
        fail(f"{rel}: not {CLS}")

    heads = [i for i, l in enumerate(lines) if METHOD.match(l)]
    if len(heads) != 1:
        fail(f"{rel}: onVolumeStateChangedAsync(<volume>II)V found {len(heads)} times")
    first = heads[0]
    vol = METHOD.match(lines[first]).group(1)
    if vol not in VOLUME_TYPES:
        fail(f"{rel}: onVolumeStateChangedAsync takes {vol}, not one of {', '.join(VOLUME_TYPES)}")
    if " static " in lines[first]:
        fail(f"{rel}: onVolumeStateChangedAsync is static (p1 .. p3 would not be vol, oldState, newState)")
    last = next((j for j in range(first + 1, len(lines)) if lines[j] == ".end method"), None)
    if last is None:
        fail(f"{rel}: onVolumeStateChangedAsync has no .end method")
    calls = [j for j in range(first, last) if NOTIFY.match(lines[j])]
    if len(calls) != 1:
        fail(f"{rel}: onVolumeStateChangedAsync calls StorageSessionController.notifyVolumeStateChanged "
             f"{len(calls)} times")
    call = calls[0]
    # mLock must not be held at the call: every monitor-enter before it has had
    # its monitor-exit, the last one right before.
    mons = [MONITOR.match(lines[j]).group(1) for j in range(first, call) if MONITOR.match(lines[j])]
    if not mons or mons[-1] != "exit" or mons.count("enter") != mons.count("exit"):
        fail(f"{rel}: notifyVolumeStateChanged is not after mLock's monitor-exit ({' '.join(mons) or 'no monitor'})")

    changed = False
    want = block(NOTIFY.match(lines[call]).group(1), vol)
    marks = [j for j in range(first, last) if lines[j].strip() == MARK]
    if marks:
        if len(marks) != 1 or lines[call - len(want):call] != want:
            fail(f"{rel}: a different OneOS media hold is already in onVolumeStateChangedAsync")
    else:
        if any("oneos_media_hold" in lines[j] for j in range(first, last)):
            fail(f"{rel}: onVolumeStateChangedAsync already has oneos_media_hold labels")
        lines[call:call] = want
        with open(path, "w") as f:
            f.write("\n".join(lines))
        changed = True
        print(f"{rel}: onVolumeStateChangedAsync waits for OneOS's albums ({HELPER_NAME}, {vol}) "
              "before notifyVolumeStateChanged", file=sys.stderr)

    with open(helper) as f:
        text = f.read()
    if WATCHED not in text or ".class public final " + HELPER not in text:
        fail(f"{helper} is not the OneOSMediaHold helper")
    text = text.replace(WATCHED, vol)
    out = os.path.join(os.path.dirname(path), HELPER_NAME)
    old = open(out).read() if os.path.isfile(out) else None
    if old != text:
        with open(out, "w") as f:
            f.write(text)
        changed = True
        print(f"{os.path.join(d, os.path.dirname(SMS), HELPER_NAME)}: written", file=sys.stderr)
    if changed:
        print(d)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: guest_media_mount_hold.py <smali root dir> [helper smali]")
    main(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else
         os.path.join(os.path.dirname(os.path.abspath(__file__)), HELPER_NAME))
