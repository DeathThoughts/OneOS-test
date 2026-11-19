#!/usr/bin/env python3
"""Exercise the One UI media share's boot shell with simulated mount operations.

The gate (before the pivot), the stage (pins One UI's media/0 before
`umount -l /postinstall`) and the late publish (started in the background
next to the zygote, after the guest's vold has built its storage views) are
cut out of guest_start.sh by their markers and run with a fake busybox. The
fake keeps a mount table, writes it in the real mountinfo layout (paths
escaped as the kernel does: "\\040" for a space), and plays the guest's vold:
after a few of the late loop's one-second sleeps it adds vold's views of the
guest's /data/media (S25 Ultra session 9a5159cc76ee3748: the early binds were
replaced by vold's, so apps never saw the album).

A mount is materialized: the fake moves what the mount point held aside and
copies the source's files into it, and puts them back on unmount. So a bind
stays valid after its source's mount is unmounted (as the kernel's does: a
bind is a mount of its own), a cover shows an empty directory, and a plain
umount of a mount with mounts below it fails (EBUSY). The storage views alias
the guest's /data/media/0: the fake's mkdir and rmdir there act on every view
too, and a stat of a view outside any mount is a stat of /data/media/0.
find, awk, sort and tr run through the installed busybox when there is one,
like the guest's (a BusyBox tr without character classes garbled the media
log lines: S25 Ultra sessions db19aee410bd79ff, 75e538ac0ed725df).

MEDIA_TEST_SH picks the shell (default sh); run the suite under dash,
busybox sh and mksh where they are installed.
"""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
START = (ROOT / "switcher/assets/guest_start.sh").read_text()
ROOT_SCRIPT = (ROOT / "app-android/app/src/full/assets/root/oneos_root.sh").read_text()
FLAGS = "ro,nosuid,nodev,noexec,noatime,nosymfollow"
SHELL = shlex.split(os.environ.get("MEDIA_TEST_SH", "sh"))
DATA_ROOT = "/local/tmp/oneos/data"
SECRETS = ("SECRET_IMG_0001.jpg", "SECRET_VID_0002.mp4", "SECRET_SHOT_0003.png", "GUEST_OWN_0004.jpg",
           "SECRET_WA_0005.jpg", "SECRET_HIDDEN_0006.jpg", "SECRET_CHILD_0007.jpg", "SECRET_ROOT_0008.jpg")
MP_PKG = "com.android.providers.media.module"
SCAN = "android.intent.action.MEDIA_SCANNER_SCAN_FILE"

GATE = START[START.index("cd /data/local/tmp/oneos/rootfs\n"):START.index("if ! $BB pivot_root")]
HOLD = START[START.index("# One UI's albums in ColorOS (owner's decision"):
             START.index("# OneOS: pivot_root left")]
LAUNCH = START[START.index("# One UI's albums (S25 Ultra session 9a5159cc76ee3748): One UI's media/0,\n"):
               START.index('echo "[Pivoted Root] time $($BB date +%T) zygote"')]

# Shared folders (Share my files, full access): the part that binds One UI's folders once the guest's storage is
# up, and releases the media hold the launch above set for them.
SHARE_BINDS = START[START.index("# The media hold for these folders (owner's decision of 2026-11-18"):
                    START.index('n=0\nwhile [ $n -lt 50 ]; do\n    [ "$(svc_pid $VOLD_NAME)" = "$VOLD_PID" ] && break')]

# The fake content: the late step's queries are logged in content.log and answer
# with two rows; the watch's (MEDIA_TEST_WATCH, set by the fake timeout 10) are
# logged in content-watch.log and answer from watch.seq, one count per line
# ("none": an error and no row), the last line repeating; a query without a
# where clause answers watch.total rows (default 2).
CONTENT = r"""#!/bin/sh
rows() {
    i=0
    while [ "$i" -lt "$1" ]; do echo "Row: $i _id=$i"; i=$((i + 1)); done
}
log=content.log
[ -z "$MEDIA_TEST_WATCH" ] || log=content-watch.log
echo "$*" >> "$MEDIA_TEST_ROOT/$log"
case "$*" in
    *--where*) ;;
    *) rows "$(cat "$MEDIA_TEST_ROOT/watch.total" 2>/dev/null || echo 2)"; exit 0 ;;
esac
if [ -n "$MEDIA_TEST_WATCH" ] && [ -f "$MEDIA_TEST_ROOT/watch.seq" ]; then
    n=$(cat "$MEDIA_TEST_ROOT/watch.n" 2>/dev/null || echo 0)
    n=$((n + 1))
    echo "$n" > "$MEDIA_TEST_ROOT/watch.n"
    last=$(wc -l < "$MEDIA_TEST_ROOT/watch.seq")
    [ "$n" -le "$last" ] || n=$last
    v=$(sed -n "${n}p" "$MEDIA_TEST_ROOT/watch.seq")
    case "$v" in
        none) echo "Error while accessing provider:media"; exit 1 ;;
        0) echo "No result found." ;;
        *) rows "$v" ;;
    esac
    exit 0
fi
rows 2
"""

BUSYBOX = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

root = Path(os.environ["MEDIA_TEST_ROOT"])
p = root / "state"
state = json.loads(p.read_text())
mountinfo = root / "mountinfo"
mode = os.environ["MEDIA_TEST_MODE"].split(",")
flags = os.environ["MEDIA_TEST_FLAGS"]
aliases = [a for a in os.environ["MEDIA_TEST_ALIASES"].split(":") if a]
media0 = str(root / "data/media/0")
cover = os.environ.get("MEDIA_TEST_BAD", "")
cmd, *args = sys.argv[1:]

def esc(s):
    return s.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011").replace("\n", "\\012")

def save():
    # Atomic: the row watch's fake commands read the state while the late step writes it.
    tmp = root / "state.tmp"
    tmp.write_text(json.dumps(state))
    os.replace(tmp, p)
    lines = ["1 0 0:1 / / rw - tmpfs tmpfs rw",
             f"2 1 0:50 {os.environ['MEDIA_TEST_DATA_ROOT']} {root}/data rw,nosuid,nodev,noatime - f2fs /dev/block/dm-50 rw"]
    for i, m in enumerate(state["mounts"], 3):
        lines.append(f"{i} 1 {m['dev']} {esc(m['root'])} {esc(m['path'])} {m['opts']} - {m['fs']} src rw")
    mountinfo.write_text("\n".join(lines) + "\n")

def top(path):
    found = [m for m in state["mounts"] if m["path"] == path]
    return found[-1] if found else None

def below(path):
    return [m for m in state["mounts"] if m["path"].startswith(path + "/")]

def enclosing(path):
    # vold's views and the early binds alias media0; they hold nothing of their own.
    found = [m for m in state["mounts"] if path.startswith(m["path"] + "/") and not m.get("view") and not m.get("early")]
    return max(found, key=lambda m: len(m["path"])) if found else None

def real(path):
    # A view outside any mount shows the guest's /data/media/0.
    m = top(path)
    if (m is None or m.get("view") or m.get("early")) and enclosing(path) is None:
        for a in aliases:
            if path == a or path.startswith(a + "/"):
                return media0 + path[len(a):]
    return path

def clear(d):
    for e in list(Path(d).iterdir()):
        if e.is_dir() and not e.is_symlink():
            shutil.rmtree(e)
        else:
            e.unlink()

def fill(dest, src):
    # Moves what dest held aside and copies src's files in.
    state["n"] = state.get("n", 0) + 1
    shadow = root / "shadow" / str(state["n"])
    shadow.mkdir(parents=True)
    for e in list(Path(dest).iterdir()):
        os.rename(e, shadow / e.name)
    if src is not None:
        shutil.copytree(src, dest, dirs_exist_ok=True, symlinks=True)
    return str(shadow)

def unfill(m):
    if m.get("shadow"):
        clear(m["path"])
        for e in list(Path(m["shadow"]).iterdir()):
            os.rename(e, Path(m["path"]) / e.name)
        os.rmdir(m["shadow"])

def ino(path):
    st = os.stat(real(path))
    return f"{st.st_dev}:{st.st_ino}"

def peer(dest):
    # "propagate": the runtime view is a peer of pass_through.
    a, b = str(root / "mnt/pass_through/0/emulated/0/"), str(root / "mnt/runtime/default/emulated/0/")
    return b + dest[len(a):] if "propagate" in mode and dest.startswith(a) else None

def bind(src, dest, recursive):
    if not os.path.isdir(dest):
        print(f"mount: mounting {src} on {dest} failed: No such file or directory", file=sys.stderr)
        sys.exit(1)
    m = top(src)
    if src == str(root / "postinstall/data"):
        rec = {"opts": "rw", "ino": "0:999", "dev": "0:60", "root": "/", "fs": "f2fs"}
        copy = str(root / "original")
    elif src == "/proc/self/cwd":
        cwd = Path.cwd()
        st = cwd.stat()
        rec = {"opts": top(str(cwd.parents[1]))["opts"] if top(str(cwd.parents[1])) else "rw",
               "ino": f"{st.st_dev}:{st.st_ino}", "dev": "0:60", "root": "/media/0", "fs": "f2fs"}
        copy = str(cwd)
    elif m is not None:
        rec = {k: m[k] for k in ("opts", "ino", "dev", "root", "fs")}
        copy = src
    else:
        e = enclosing(src)
        rec = {"opts": e["opts"] if e else "rw", "ino": ino(src), "dev": e["dev"] if e else "0:50",
               "root": (e["root"].rstrip("/") + src[len(e["path"]):]) if e else src, "fs": e["fs"] if e else "f2fs"}
        copy = src
    if cover and dest.endswith(cover) and "/mnt/runtime/" in dest and "bad-alias" in mode:
        rec["opts"] = "rw,nosuid"
    shadow = None if src == dest else fill(dest, copy)
    state["mounts"].append(dict(rec, path=dest, shadow=shadow))
    if recursive:
        for s in below(src):
            sub = dest + s["path"][len(src):]
            copy = dict(s, path=sub, shadow=None)
            if s.get("shadow"):
                # What the sub mount covers in the copy: the same as under the source's.
                state["n"] = state.get("n", 0) + 1
                sh = root / "shadow" / str(state["n"])
                shutil.copytree(s["shadow"], sh, symlinks=True)
                copy["shadow"] = str(sh)
            if "bad-cover" in mode and "/mnt/runtime/" in sub and cover and cover in sub:
                copy["opts"] = "rw,nosuid"
            state["mounts"].append(copy)
    other = peer(dest)
    if other and not top(other):
        bind(src, other, recursive)

def umount(dest, lazy):
    m = top(dest)
    if m is None:
        return 1
    subs = below(dest)
    if subs and not lazy:
        return 1
    for s in reversed(subs):
        unfill(s)
        state["mounts"].remove(s)
    unfill(m)
    state["mounts"].remove(m)
    other = peer(dest)
    if other and top(other):
        umount(other, lazy)
    return 0

def mirrors(path):
    if path == media0 or path.startswith(media0 + "/"):
        return [a + path[len(media0):] for a in aliases if os.path.isdir(a)]
    return []

def vold():
    # The guest's vold drops guest_start.sh's early binds, then builds its own.
    state["mounts"] = [m for m in state["mounts"] if not m.get("early")]
    for path, r, dev, fs in (
            ("mnt/pass_through/0/emulated", "/media", "0:50", "f2fs"),
            ("mnt/runtime/default/emulated", "/media", "0:50", "f2fs"),
            ("mnt/user/0/emulated", "/", "0:70", "fuse"),
            ("mnt/androidwritable/0/emulated/0/Android/data", "/media/0/Android/data", "0:50", "f2fs")):
        root_field = (os.environ["MEDIA_TEST_DATA_ROOT"] + r).rstrip("/") if r != "/" else "/"
        state["mounts"].append({"path": str(root / path), "opts": "rw,nosuid,nodev,noexec,noatime",
                                "ino": "", "shadow": None, "dev": dev, "root": root_field, "fs": fs, "view": True})

def passthrough():
    # The album listing's tools and tr as the guest's busybox has them (its tr
    # may lack character classes): the rest runs as installed.
    bb = shutil.which("busybox")
    if bb and cmd in ("find", "awk", "sort", "tr"):
        os.execv(bb, ["busybox", cmd] + args)
    os.execvp(cmd, [cmd] + args)

if cmd == "readlink":
    print("mnt:[2]" if args[0].startswith("/proc/self/") and "same-ns" not in mode else "mnt:[1]")
elif cmd == "stat":
    fmt, path = args[1], args[-1]
    m = top(path)
    if fmt == "%u:%a":
        uid = os.environ.get("MEDIA_TEST_XUID", "0") if path.endswith("oneos-oneui-media-excluded") else "0"
        print(f"{uid}:{Path(path).stat().st_mode & 0o777:o}")
    elif fmt == "%d:%i" and m and m["ino"]:
        print(m["ino"])
    else:
        try:
            st = os.stat(real(path))
        except OSError as e:
            print(f"stat: {e.strerror}", file=sys.stderr)
            sys.exit(1)
        if fmt == "%d:%i":
            print(f"{st.st_dev}:{st.st_ino}")
        elif fmt == "%a %u %g":
            print(f"{st.st_mode & 0o777:o} {st.st_uid} {st.st_gid}")
        else:
            sys.exit(f"fake stat: format {fmt}")
elif cmd == "sleep" and args[0] in ("5", "10"):
    # Only the row watch sleeps 5 or 10 s, in the background next to the
    # late step: it must not touch the shared state (a read-modify-write
    # there would race the mounts), so it moves a clock of its own, which
    # date +%s adds. MEDIA_TEST_CLOCK_SCALE makes the watch's run short.
    clock = root / "clock"
    now = int(clock.read_text()) if clock.exists() else 0
    tmp = root / "clock.tmp"
    tmp.write_text(str(now + int(args[0]) * int(os.environ.get("MEDIA_TEST_CLOCK_SCALE", "100"))))
    os.replace(tmp, clock)
elif cmd == "date" and args == ["+%s"]:
    clock = root / "clock"
    print(int(time.time()) + (int(clock.read_text()) if clock.exists() else 0))
elif cmd == "timeout" and args[0] == "10":
    # The watch's queries (timeout 10): the fake content tells them from the late step's.
    os.environ["MEDIA_TEST_WATCH"] = "1"
    passthrough()
elif cmd == "sleep":
    state["sleeps"] += 1
    if os.environ.get("MEDIA_TEST_VOLD_AT") and state["sleeps"] == int(os.environ["MEDIA_TEST_VOLD_AT"]):
        vold()
    save()
elif cmd == "pidof":
    sys.exit(1)
elif cmd == "ls" and args[:1] == ["-Zd"]:
    if "no-selinux" in mode:
        sys.exit(1)
    print(f"u:object_r:media_rw_data_file:s0 {args[1]}")
elif cmd == "chcon":
    with open(root / "chcon.log", "a") as f:
        f.write(" ".join(args) + "\n")
elif cmd == "mkdir":
    rc = subprocess.call(["mkdir"] + args)
    if rc == 0:
        for m in mirrors(args[-1]):
            os.makedirs(m, exist_ok=True)
    sys.exit(rc)
elif cmd == "rmdir":
    rc = 0
    for path in args:
        if any(top(x) for x in [path] + mirrors(path)):
            rc = 1
            continue
        if subprocess.call(["rmdir", path], stderr=subprocess.DEVNULL) != 0:
            rc = 1
            continue
        for x in mirrors(path):
            if os.path.isdir(x):
                os.rmdir(x)
    sys.exit(rc)
elif cmd == "mount":
    if args[0] == "--make-rprivate":
        sys.exit("not-private" in mode)
    dest = args[-1]
    if args[0] == "-o":
        if "remount-fails" in mode:
            print(f"mount: mounting {args[-2]} on {dest} failed: Invalid argument", file=sys.stderr)
            print("second line", file=sys.stderr)
            sys.exit(1)
        if "cover-remount-fails" in mode and dest.endswith("/cover"):
            sys.exit(1)
        top(dest)["opts"] = flags.removesuffix(",nosymfollow") if "lack-nosymfollow" in mode else flags
    else:
        if "rbind-fails" in mode and args[0] == "--rbind" and "/mnt/runtime/" in dest and cover and dest.endswith(cover):
            sys.exit(1)
        bind(args[-2], dest, args[0] == "--rbind")
    save()
elif cmd == "umount":
    dest = args[-1]
    lazy = "-l" in args
    if "busy-parent" in mode and dest.endswith("/host"):
        sys.exit(1)
    if "stuck-alias" in mode and "/mnt/runtime/" in dest:
        sys.exit(1)
    rc = umount(dest, lazy)
    save()
    sys.exit(rc)
else:
    passthrough()
"""


class Base(unittest.TestCase):
    # How much a 5 or 10 s sleep of the row watch moves the fake clock: the
    # watch ends after a few samples. Tests of the watch itself use 1.
    clock_scale = "100"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = w = Path(tmp.name)
        self.state = w / "state"
        self.state.write_text(json.dumps({"mounts": [], "sleeps": 0}))
        (w / "mountinfo").write_text("1 0 0:1 / / rw - tmpfs tmpfs rw\n")
        self.stage = w / "data/local/tmp/oneos"
        self.opt_in = self.stage.parent / "oneos-oneui-media"
        self.media0 = w / "data/media/0"
        self.one_ui = w / "original/media/0"
        self.exclusions = w / "original/local/tmp/oneos-oneui-media-excluded"
        self.vold_bases = ("mnt/pass_through/0/emulated/0", "mnt/runtime/default/emulated/0")
        self.early_bases = ("storage/emulated/0", "mnt/user/0/emulated")
        for path in ("data/local/tmp/oneos/rootfs", "postinstall/data", "data/media/0/DCIM",
                     "data/media/0/Pictures", "original/local/tmp", "props", "system/bin", "fakebin",
                     *(b + "/DCIM" for b in self.vold_bases + self.early_bases),
                     *(b + "/Pictures" for b in self.vold_bases + self.early_bases)):
            (w / path).mkdir(parents=True, exist_ok=True)
        self.photo("DCIM/Camera", SECRETS[0])
        self.photo("DCIM/Camera", SECRETS[1])
        self.photo("DCIM/Screenshots", SECRETS[2])
        (self.stage / ".oneos_stage").write_text('  "image": "coloros17-findx9u-in-1105",\n')
        self.opt_in.mkdir(mode=0o700)
        (w / "props/sys.boot_completed").write_text("1")
        self.script(w / "bb", BUSYBOX)
        self.script(w / "fakebin/getprop", '#!/bin/sh\ncat "$MEDIA_TEST_ROOT/props/$1" 2>/dev/null\n')
        # Every setprop in order; the mount table as it was when the media hold was released.
        self.script(w / "fakebin/setprop", '#!/bin/sh\nprintf %s "$2" > "$MEDIA_TEST_ROOT/props/$1"\n'
                    'echo "$1=$2" >> "$MEDIA_TEST_ROOT/setprop.log"\n'
                    '[ "$1=$2" != oneos.media.published=1 ] || cp "$MEDIA_TEST_ROOT/state" "$MEDIA_TEST_ROOT/published.state"\n')
        self.script(w / "fakebin/pidof", "#!/bin/sh\nexit 1\n")
        self.script(w / "system/bin/sh", f'#!/bin/sh\nexec {" ".join(SHELL)} "$@"\n')
        self.script(w / "data/oneos_genv.sh", '#!/bin/sh\nexec "$@"\n')
        self.script(w / "system/bin/am", '#!/bin/sh\necho "$*" >> "$MEDIA_TEST_ROOT/am.log"\n'
                    'echo "Broadcasting: Intent { act=$4 dat=$6 }"\necho "Broadcast completed: result=0"\n')
        self.script(w / "system/bin/content", CONTENT)
        # system_server's hold line (tag OneOSMediaHold) answers from hold.out.
        self.script(w / "fakebin/logcat", '#!/bin/sh\necho "$*" >> "$MEDIA_TEST_ROOT/logcat.log"\n'
                    'case "$*" in *OneOSMediaHold*) cat "$MEDIA_TEST_ROOT/hold.out" 2>/dev/null; exit 0 ;; esac\n'
                    'cat "$MEDIA_TEST_ROOT/logcat.out" 2>/dev/null\n')
        self.proc(4242, MP_PKG, "Uid:\t10077\t10077\t10077\t10077\nGid:\t10077\t10077\t10077\t10077\n"
                  "Groups:\t1023 1078 9997 20077 50077\n",
                  ["7 1 0:50 /local/tmp/oneos/data/media / rw - f2fs x rw",
                   "8 1 0:60 /media/0/DCIM/Camera /storage/emulated/0/OneUI/Camera\\040(One\\040UI) "
                   + FLAGS + " master:9 - f2fs y rw",
                   "9 1 0:3 / /apex ro - tmpfs tmpfs rw"])
        self.proc(5151, "com.example.app", "Name:\tapp\nUid:\t10234\t10234\t10234\t10234\n",
                  [f"{n} 1 0:70 / /mnt/user/0/emulated/{n} rw - fuse fuse rw" for n in range(25)])
        self.proc(1, "/system/bin/init", "Uid:\t0\t0\t0\t0\n", [])

    def script(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    def photo(self, rel, name, text="x"):
        d = self.one_ui / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text)

    def guest_dir(self, rel):
        """A directory of the guest's own in its media/0, as every storage view shows it."""
        for d in [self.media0] + [self.work / b for b in self.vold_bases + self.early_bases]:
            (d / rel).mkdir(parents=True, exist_ok=True)
        return self.media0 / rel

    def exclude(self, *paths, mode=0o600, raw=None):
        self.exclusions.write_bytes(raw if raw is not None else "".join(p + "\n" for p in paths).encode())
        self.exclusions.chmod(mode)

    def proc(self, pid, name, status, mountinfo):
        d = self.work / "proc" / str(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(name.encode() + b"\0")
        (d / "status").write_text(status)
        # Field 22 (starttime, 5 ticks after boot) comes after a comm with a space and a parenthesis.
        (d / "stat").write_text(f"{pid} (media (x) y) " + " ".join(["S"] + ["0"] * 18 + ["5"]) + "\n")
        (d / "mountinfo").write_text("".join(l + "\n" for l in mountinfo))

    def mounts(self):
        return json.loads(self.state.read_text())["mounts"]

    def early(self):
        # guest_start.sh's own binds below "Setting up /data/media".
        st = json.loads(self.state.read_text())
        for b in self.early_bases:
            st["mounts"].append({"path": str(self.work / b), "opts": "rw", "ino": "", "shadow": None,
                                 "dev": "0:50", "root": DATA_ROOT + "/media/0", "fs": "f2fs", "early": True})
        self.state.write_text(json.dumps(st))

    def reset(self):
        """A new boot is a new namespace: the last boot's mounts are gone, and what they covered shows again."""
        st = json.loads(self.state.read_text())
        for m in reversed(st["mounts"]):
            d = Path(m["path"])
            if m.get("shadow") and d.is_dir():
                for e in list(d.iterdir()):
                    shutil.rmtree(e) if e.is_dir() and not e.is_symlink() else e.unlink()
                for e in list(Path(m["shadow"]).iterdir()):
                    os.rename(e, d / e.name)
        st["mounts"] = []
        self.state.write_text(json.dumps(st))
        for log in ("am.log", "content.log", "content-watch.log", "logcat.log", "chcon.log",
                    "setprop.log", "published.state"):
            (self.work / log).unlink(missing_ok=True)

    def boot(self, mode="", vold_at=3, bad="", xuid="0", keep=False, env=None):
        """keep: the mounts a test set up stay (otherwise each boot starts with none). env: more variables."""
        if not keep:
            self.reset()
        st = json.loads(self.state.read_text())
        st["sleeps"] = 0
        self.state.write_text(json.dumps(st))
        for f in ("clock", "watch.n"):
            (self.work / f).unlink(missing_ok=True)
        shell = GATE + HOLD + LAUNCH + "wait\n"
        shell = shell.replace("/proc/self/mountinfo", str(self.work / "mountinfo"))
        shell = shell.replace("/proc/$", str(self.work) + "/proc/$")
        shell = shell.replace("/proc/[0-9]", str(self.work) + "/proc/[0-9]")
        shell = re.sub(r"""(?<![\w/.])/(data|postinstall|mnt|storage|system)(?=[/\s'"])""",
                       lambda m: str(self.work / m[1]), shell)
        aliases = ":".join(str(self.work / b) for b in self.vold_bases + self.early_bases)
        result = subprocess.run(
            SHELL + ["-c", shell],
            env={**os.environ, "BB": str(self.work / "bb"), "MEDIA_TEST_ROOT": str(self.work),
                 "MEDIA_TEST_MODE": mode, "MEDIA_TEST_FLAGS": FLAGS, "MEDIA_TEST_BAD": bad,
                 "MEDIA_TEST_XUID": xuid, "MEDIA_TEST_CLOCK_SCALE": self.clock_scale,
                 "MEDIA_TEST_ALIASES": aliases, "MEDIA_TEST_DATA_ROOT": DATA_ROOT,
                 "MEDIA_TEST_VOLD_AT": str(vold_at or ""),
                 "PATH": str(self.work / "fakebin") + ":" + os.environ["PATH"], **(env or {})},
            capture_output=True, text=True, timeout=300,
        )
        for secret in SECRETS:
            self.assertNotIn(secret, result.stdout + result.stderr)
        for line in result.stdout.splitlines():
            self.assertFalse(line.startswith("[Pivoted Root] time "), line)
        return result

    def lines(self, result):
        return [l for l in result.stdout.splitlines() if "Media prototype" in l]

    def view(self, base, rel=""):
        return str(self.work / base / "OneUI" / rel) if rel else str(self.work / base / "OneUI")

    def targets(self, name):
        return {self.view(b, name) for b in self.vold_bases}

    def assert_released(self):
        self.assertEqual([], list((self.work / "data").glob("oneos_media_hold.*")))
        self.assertEqual([], [m["path"] for m in self.mounts() if "oneos_media_hold" in m["path"]])

    def album_mounts(self):
        """Album mount points (not covers) under OneUI/, by name."""
        out = {}
        for m in self.mounts():
            for b in self.vold_bases:
                pre = self.view(b) + "/"
                if m["path"].startswith(pre) and "/" not in m["path"][len(pre):]:
                    out.setdefault(m["path"][len(pre):], set()).add(m["path"])
        return out

    def names(self):
        return set(self.album_mounts())

    def cover_mounts(self):
        return [m["path"] for m in self.mounts()
                if any(m["path"].startswith(self.view(b) + "/") and "/" in m["path"][len(self.view(b)) + 1:]
                       for b in self.vold_bases)]

    def shown(self, name, base=None):
        """Every file and directory an app sees through the album at the pass_through view."""
        d = Path(self.view(base or self.vold_bases[0], name))
        return sorted(str(p.relative_to(d)) for p in d.rglob("*"))


class GuestMediaGate(Base):
    def test_without_the_marker_nothing_is_printed_or_mounted(self):
        self.opt_in.rmdir()
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.mounts())
        self.assertNotIn("Media prototype", result.stdout)
        # The test album's marker is no longer honored, and not mentioned.
        (self.stage.parent / "oneos-media-share-test").mkdir(mode=0o700)
        result = self.boot()
        self.assertEqual([], self.mounts())
        self.assertNotIn("Media prototype", result.stdout)
        self.assertNotIn("share-test", result.stdout)

    def test_wrong_guest_is_off_with_its_reason(self):
        (self.stage / ".oneos_stage").write_text('  "image": "lineage24-test",\n')
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.mounts())
        self.assertEqual(["[Pivoted Root] Media prototype off: image is not ColorOS "
                          "(stage marker image 'lineage24-test')"], self.lines(result))

    def test_share_my_files_keeps_the_album_prototype_off_for_the_boot(self):
        # 0.5.20 upstream's Share my files binds One UI's DCIM, Pictures, ... over the guest's folders; the
        # albums would list the same photos twice, so ONEOS_SHARE_FILES=1 turns the prototype (and, by the
        # existing no-prototype rule, the media hold) off even with the opt-in marker in place.
        self.photo("Pictures/X", "a.webp")
        result = self.boot(env={"ONEOS_SHARE_FILES": "1"})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.mounts())
        self.assertEqual([], list((self.work / "data").glob("oneos_media_hold.*")))
        self.assertEqual(
            ["[Pivoted Root] Media prototype: off (Share my files is on; its folders carry the same photos)",
             "[Pivoted Root] Media hold: off (the media prototype does not run this boot)"],
            result.stdout.splitlines())
        log = (self.work / "setprop.log").read_text().splitlines()
        self.assertEqual(["oneos.media.hold=0", "oneos.media.published=0"], [l for l in log if l.startswith("oneos.media.")])
        # Off (0) or absent: the prototype runs as before.
        for env in ({"ONEOS_SHARE_FILES": "0"}, {}):
            result = self.boot(env=env)
            self.assertNotIn("Share my files", result.stdout)
            self.assertIn("Media prototype: One UI's media/0 pinned read-only", result.stdout)
        # Without the opt-in marker nothing about the prototype is printed, shared files or not.
        self.opt_in.rmdir()
        result = self.boot(env={"ONEOS_SHARE_FILES": "1"})
        self.assertNotIn("Media prototype", result.stdout)

    def test_off_reasons_name_the_gate_check(self):
        self.opt_in.chmod(0o755)
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.mounts())
        self.assertIn("Media prototype off: opt-in marker is 0:755", result.stdout)
        self.opt_in.chmod(0o700)
        # Only plain characters of the image id are printed.
        (self.stage / ".oneos_stage").write_text('  "image": "x$(id)`;y",\n')
        result = self.boot()
        self.assertIn("Media prototype off: image is not ColorOS (stage marker image 'xidy')",
                      result.stdout)
        (self.stage / ".oneos_stage").write_text('  "image": "coloros17-findx9u-in-1105",\n')
        for mode, reason in (("not-private", "mount --make-rprivate / failed"),
                             ("same-ns", "not in a private mount namespace (same as pid 1: mnt:[1])")):
            result = self.boot(mode)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual([], self.mounts())
            self.assertEqual([f"[Pivoted Root] Media prototype off: {reason}",
                              "[Pivoted Root] Media hold: off (the media prototype does not run this boot)"],
                             result.stdout.splitlines())
        self.opt_in.rmdir()
        self.opt_in.write_text("")
        result = self.boot()
        self.assertEqual([f"[Pivoted Root] Media prototype off: opt-in marker {self.opt_in} is not a directory"],
                         self.lines(result))


class GuestMediaAlbums(Base):
    def test_every_album_on_every_vold_alias_with_full_flags_then_one_scan(self):
        self.photo("Pictures/X", "a.webp")
        self.photo("Pictures/A", "a.HEIC")
        self.photo("Pictures/A/B", SECRETS[6])
        self.photo("Movies", "m.MKV")
        self.photo("Download", "notes.txt")
        self.photo("Documents/scan", "s.pdf")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout
        self.assertIn("[Pivoted Root] Media prototype: One UI's media/0 pinned read-only (album exclusion list: "
                      "0 line(s)); albums are published once the guest's vold has built its storage\n", out)
        bases = " ".join(str(self.work / b) for b in self.vold_bases)
        self.assertRegex(out, r"Media prototype: the guest's vold built its storage views at "
                         r"\d\d:\d\d:\d\d \(\d+ s after the zygote launch\), settled at \d\d:\d\d:\d\d: "
                         + re.escape(bases) + "\n")
        self.assertRegex(out, r"Media prototype: One UI has 6 album\(s\) \(listed in \d+ s\), 0 unticked by the "
                         r"owner, 6 to publish\n")
        # Path order (LC_ALL=C): DCIM/Camera, DCIM/Screenshots, Movies, Pictures/A, Pictures/A/B, Pictures/X.
        want = [("Camera (One UI)", 2, 0), ("Screenshots (One UI)", 1, 0), ("Movies (One UI)", 1, 0),
                ("A (One UI)", 1, 1), ("B (One UI)", 1, 0), ("X (One UI)", 1, 0)]
        for k, (name, files, children) in enumerate(want, 1):
            self.assertIn(f"Media prototype: album {k} of 6 mounted as OneUI/{name} ({files} media files, "
                          f"{children} child dirs covered, 2 aliases)\n", out)
        self.assertIn("Media prototype: albums: 6 found, 0 excluded, 6 mounted, 0 skipped\n", out)
        albums = self.album_mounts()
        self.assertEqual({n for n, _, _ in want}, set(albums))
        for name, _, _ in want:
            self.assertEqual(self.targets(name), albums[name])
        on_views = [m for m in self.mounts() if "/OneUI/" in m["path"]]
        self.assertEqual({FLAGS}, {m["opts"] for m in on_views})
        # FUSE, Android/data and the hold are no alias bases; nothing lands in the guest's own media/0.
        self.assertFalse(any("/mnt/user/" in m["path"] or "Android" in m["path"] for m in on_views))
        self.assertEqual(0o777, (self.media0 / "OneUI").stat().st_mode & 0o777)
        self.assertEqual(0o777, (self.media0 / "OneUI/Camera (One UI)").stat().st_mode & 0o777)
        self.assertEqual([], list((self.media0 / "OneUI/Camera (One UI)").iterdir()))
        # Each album shows exactly the files directly in it: B shows inside A only as an empty dir.
        self.assertEqual([SECRETS[0], SECRETS[1]], self.shown("Camera (One UI)"))
        self.assertEqual(["B", "a.HEIC"], self.shown("A (One UI)"))
        self.assertEqual([SECRETS[6]], self.shown("B (One UI)"))
        for b in self.vold_bases:
            self.assertEqual(["B", "a.HEIC"], self.shown("A (One UI)", b))
        self.assertEqual(sorted(self.view(b, "A (One UI)/B") for b in self.vold_bases), sorted(self.cover_mounts()))
        # The albums and covers stay valid once the hold is released.
        self.assertIn("[Pivoted Root] Media prototype: hold released\n", out)
        self.assert_released()
        self.assertEqual("x", (Path(self.view(self.vold_bases[1], "B (One UI)")) / SECRETS[6]).read_text())
        self.assertEqual(["u:object_r:media_rw_data_file:s0"],
                         [l.split()[0] for l in (self.work / "chcon.log").read_text().splitlines()])
        # Diagnostics: counts and modes, MediaProvider's ids and the views.
        self.assertRegex(out, r"Media prototype: OneUI is 777 \d+ \d+, OneUI/Camera \(One UI\) is \d+ \d+ \d+ "
                         r"\(mode uid gid\), one file in it is \d+ \d+ \d+\n")
        self.assertIn(f"Media prototype: MediaProvider {MP_PKG} pid 4242: Uid: 10077 10077 10077 10077; "
                      "Gid: 10077 10077 10077 10077; Groups: 1023 1078 9997 20077 50077\n", out)
        self.assertIn("Media prototype view MediaProvider pid 4242: 0:60 /media/0/DCIM/Camera "
                      f"/storage/emulated/0/OneUI/Camera\\040(One\\040UI) {FLAGS} f2fs\n", out)
        self.assertNotIn("/apex", out)
        app = [l for l in out.splitlines() if "view app uid 10234 pid 5151:" in l]
        self.assertEqual(21, len(app), app)
        self.assertTrue(app[-1].endswith("... and 5 more"))
        # One scan request for OneUI/, to MediaProvider's package, between two counts.
        am = (self.work / "am.log").read_text().splitlines()
        self.assertEqual([f"broadcast -p {MP_PKG} -a {SCAN} -d file:///storage/emulated/0/OneUI"], am)
        self.assertRegex(out, r"Media prototype: scan of OneUI asked \d+ s after the zygote "
                         r"launch \(am status 0, \d+ s\): Broadcast completed: result=0\n")
        self.assertLess(out.index("MediaStore lists 2 item(s) under OneUI/ before the scan request\n"),
                        out.index("scan of OneUI asked"))
        self.assertIn("Media prototype: MediaStore lists 2 item(s) under OneUI/ 30 s after the scan request\n", out)
        # The late step's own queries: the two OneUI/ counts, the total after the second.
        where = "query --uri content://media/external/file --projection _id --where relative_path LIKE 'OneUI/%'"
        self.assertEqual([where, where, "query --uri content://media/external/file --projection _id"],
                         (self.work / "content.log").read_text().splitlines())

    def test_content_and_am_output_is_logged_as_they_printed_it(self):
        # S25 Ultra sessions db19aee410bd79ff, 75e538ac0ed725df: "tr -cd '[:print:]'" in the guest's
        # BusyBox tr (no character classes) logged "(rtn)" and "rtpt:rt" for these lines.
        self.assertNotIn("tr -cd '[:print:]'", START)
        self.assertNotRegex(START, r"tr -c?d '\[:[a-z]+:\]'")
        self.script(self.work / "system/bin/content", '#!/bin/sh\necho "$*" >> "$MEDIA_TEST_ROOT/content.log"\n'
                    'printf "No result found.\\r\\n"\n')
        self.script(self.work / "system/bin/am", '#!/bin/sh\necho "$*" >> "$MEDIA_TEST_ROOT/am.log"\n'
                    'echo "Broadcasting: Intent { act=$4 dat=$6 }"\nprintf "Broadcast completed: \\033result=0\\r\\n"\n')
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout
        self.assertIn("Media prototype: MediaStore lists 0 item(s) under OneUI/ before the scan request "
                      "(No result found.)\n", out)
        self.assertRegex(out, r"Media prototype: scan of OneUI asked \d+ s after the zygote "
                         r"launch \(am status 0, \d+ s\): Broadcast completed: result=0\n")
        self.assertIn("Media prototype: MediaStore lists 0 item(s) under OneUI/ 30 s after the scan request "
                      "(No result found.)\n", out)
        self.assertIn("Media watch: MediaStore has 0 row(s) in all 30 s after the scan request "
                      "(No result found.)\n", out)

    def test_nothing_under_android_is_ever_listed_or_mounted(self):
        self.photo("Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images", SECRETS[4])
        self.photo("Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Video", "v.mp4")
        self.photo("Android/data/x/files/pics", "p.jpg")
        self.photo("Android/obb/com.game", "trailer.mp4")
        self.photo("Android", "top.png")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("One UI has 2 album(s)", result.stdout)
        self.assertEqual({"Camera (One UI)", "Screenshots (One UI)"}, self.names())
        self.assertNotIn("WhatsApp", result.stdout)
        self.assertFalse(any("Android" in m["path"] or "WhatsApp" in m["path"] or "/obb" in m["path"]
                             for m in self.mounts() if not m.get("view")))
        self.assertFalse(any("Android" in m["root"] for m in self.mounts() if "/OneUI/" in m["path"]))
        # Lower case on a file system that keeps case apart.
        self.photo("android/media/x", "q.jpg")
        self.photo("ANDROID", "r.jpg")
        result = self.boot()
        self.assertIn("One UI has 2 album(s)", result.stdout)

    def test_hidden_and_nomedia_dirs_and_the_storage_root_are_not_shared(self):
        self.photo("Pictures/.hidden", SECRETS[5])
        self.photo(".Trash/photos", "t.jpg")
        self.photo("Pictures/Private", "p.jpg")
        (self.one_ui / "Pictures/Private/.nomedia").write_text("")
        self.photo("Pictures/Private/Deeper", "d.jpg")
        self.photo("Music/Covers", "c.png")
        (self.one_ui / "Music/.NOMEDIA").write_text("")
        self.photo("", SECRETS[7])
        self.photo("", "also.mp4")
        self.photo("Pictures/Shown", "s.gif")
        self.photo("Pictures/Shown", ".hidden.jpg")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("One UI has 3 album(s)", result.stdout)
        self.assertEqual({"Camera (One UI)", "Screenshots (One UI)", "Shown (One UI)"}, self.names())
        self.assertIn("[Pivoted Root] Media prototype: 2 media file(s) directly in One UI's storage root are not "
                      "shared (the root is never an album)\n", result.stdout)
        self.assertFalse(any(m["root"] in ("/media/0", "/media/0/") for m in self.mounts() if "/OneUI/" in m["path"]))
        # A .nomedia in the storage root hides every album, as in MediaStore.
        (self.one_ui / ".nomedia").write_text("")
        result = self.boot()
        self.assertIn("One UI has 0 album(s)", result.stdout)
        self.assertIn("Media prototype: albums: 0 found, 0 excluded, 0 mounted, 0 skipped\n", result.stdout)
        self.assertEqual(set(), self.names())
        self.assertFalse((self.media0 / "OneUI").exists())
        self.assertFalse((self.work / "am.log").exists())
        self.assert_released()

    def test_a_path_with_a_control_character_is_skipped(self):
        self.photo("Pictures/new\nline", "n.jpg")
        self.photo("Pictures/tab\there", "t.jpg")
        self.photo("Pictures/Fine", "f.jpg")
        # A subdirectory that could not be listed one per line: its album is not shared.
        self.photo("Pictures/Parent", "p.jpg")
        (self.one_ui / "Pictures/Parent/odd\nchild").mkdir()
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("One UI has 4 album(s)", result.stdout)
        self.assertEqual({"Camera (One UI)", "Screenshots (One UI)", "Fine (One UI)"}, self.names())
        self.assertRegex(result.stdout, r"skipped album 4 of 4 \(OneUI/Parent \(One UI\)\): guest storage aliases "
                         r"could not be protected: a subdirectory's name has a control character, so it could not "
                         r"be covered\n")
        self.assertFalse((self.media0 / "OneUI/Parent (One UI)").exists())

    def test_names_that_clash_take_the_parent_then_a_number(self):
        for rel in ("DCIM/Camera", "Pictures/Camera", "camera", "A/X/Trip", "B/X/Trip", "C/X/trip", "Solo"):
            self.photo(rel, "p.jpg")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        # Path order: A/X/Trip B/X/Trip C/X/trip DCIM/Camera DCIM/Screenshots Pictures/Camera Solo camera.
        self.assertEqual({"Trip (One UI, X)", "Trip (One UI, X) 2", "trip (One UI, X) 3", "Camera (One UI, DCIM)",
                          "Screenshots (One UI)", "Camera (One UI, Pictures)", "Solo (One UI)", "camera (One UI)"},
                         self.names())
        self.assertIn("album 1 of 8 mounted as OneUI/Trip (One UI, X) (", result.stdout)
        self.assertIn("album 3 of 8 mounted as OneUI/trip (One UI, X) 3 (", result.stdout)
        self.assertIn("album 8 of 8 mounted as OneUI/camera (One UI) (", result.stdout)
        self.assertEqual({str(self.work / "original/media/0/B/X/Trip")[len(str(self.work / "original")):]},
                         {m["root"] for m in self.mounts() if m["path"].endswith("/OneUI/Trip (One UI, X) 2")})

    def test_names_with_spaces_arabic_quotes_stars_and_dashes(self):
        odd = ["My Trip (2024)", "صور العائلة", "it's \"quoted\"", "*", "-dash", "back\\slash", "  two  spaces "]
        for name in odd:
            self.photo("Pictures/" + name, "p.jpg")
        self.photo("Pictures/*/inner", "i.jpg")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        names = {f"{n} (One UI)" for n in odd} | {"Camera (One UI)", "Screenshots (One UI)", "inner (One UI)"}
        self.assertEqual(names, self.names())
        self.assertIn("albums: 10 found, 0 excluded, 10 mounted, 0 skipped", result.stdout)
        for name in odd:
            self.assertEqual((["inner"] if name == "*" else []) + ["p.jpg"], self.shown(f"{name} (One UI)"))
        self.assertIn("view MediaProvider", result.stdout)
        # The mount table spells them escaped; the guest's own dirs are the plain names.
        mi = (self.work / "mountinfo").read_text()
        self.assertIn("OneUI/My\\040Trip\\040(2024)\\040(One\\040UI) " + FLAGS, mi)
        self.assertIn("OneUI/back\\134slash\\040(One\\040UI) " + FLAGS, mi)
        self.assertTrue((self.media0 / "OneUI/صور العائلة (One UI)").is_dir())
        self.assertEqual([self.view(b, "* (One UI)/inner") for b in self.vold_bases], sorted(self.cover_mounts()))

    def test_child_dirs_are_covered_even_hidden_and_nomedia_ones(self):
        self.photo("DCIM/Camera/.thumbnails", SECRETS[5])
        self.photo("DCIM/Camera/Private", SECRETS[6])
        (self.one_ui / "DCIM/Camera/Private/.nomedia").write_text("")
        self.photo("DCIM/Camera/Burst", "b.jpg")
        (self.one_ui / "DCIM/Camera/Empty").mkdir()
        (self.one_ui / "DCIM/Camera/link").symlink_to(self.one_ui / "DCIM/Screenshots")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("mounted as OneUI/Camera (One UI) (2 media files, 4 child dirs covered, 2 aliases)", result.stdout)
        self.assertIn("mounted as OneUI/Burst (One UI) (1 media files, 0 child dirs covered", result.stdout)
        for b in self.vold_bases:
            self.assertEqual([".thumbnails", "Burst", "Empty", "Private", SECRETS[0], SECRETS[1], "link"],
                             self.shown("Camera (One UI)", b))
        covers = [m for m in self.mounts() if m["path"] in set(self.cover_mounts())]
        self.assertEqual(8, len(covers))
        self.assertEqual({FLAGS}, {m["opts"] for m in covers})

    def test_exclusions_are_honoured_and_invalid_lines_counted(self):
        self.photo("Pictures/A", "a.jpg")
        self.photo("Pictures/A/B", "b.jpg")
        self.exclude("DCIM/Screenshots", "Pictures/A", "/DCIM/Camera", "DCIM/Camera/", "../x", "Android/media",
                     ".hidden", "DCIM//Camera", "Not/There")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout
        self.assertIn("pinned read-only (album exclusion list: 9 line(s))", out)
        self.assertIn("One UI has 4 album(s) (listed in", out)
        self.assertIn("2 unticked by the owner, 2 to publish\n", out)
        self.assertIn("Media prototype: 6 line(s) of the album exclusion list are not album paths and were ignored\n", out)
        # Unticking A leaves its child album B: the picker lists it on its own.
        self.assertEqual({"Camera (One UI)", "B (One UI)"}, self.names())
        self.assertIn("albums: 4 found, 2 excluded, 2 mounted, 0 skipped", out)
        # A missing last newline still counts.
        self.exclude(raw=b"DCIM/Camera\nDCIM/Screenshots")
        result = self.boot()
        self.assertEqual({"A (One UI)", "B (One UI)"}, self.names())
        self.assertNotIn("not album paths", result.stdout)
        # Every album unticked: nothing mounted, no OneUI/ made, no scan.
        self.exclude("DCIM/Camera", "DCIM/Screenshots", "Pictures/A", "Pictures/A/B")
        result = self.boot()
        self.assertEqual(set(), self.names())
        self.assertFalse((self.media0 / "OneUI").exists())
        self.assertIn("albums: 4 found, 4 excluded, 0 mounted, 0 skipped", result.stdout)
        self.assertFalse((self.work / "am.log").exists())
        self.assert_released()

    def test_an_exclusion_list_that_is_not_roots_own_file_shares_nothing(self):
        x = "local/tmp/oneos-oneui-media-excluded"
        cases = [
            (dict(paths=("DCIM/Camera",), mode=0o644), f"{x} is 0:644 (uid:mode), not root's 0600"),
            (dict(paths=("DCIM/Camera",), mode=0o600, xuid="2000"), f"{x} is 2000:600 (uid:mode), not root's 0600"),
            (dict(raw=b"a" * 262145), f"{x} is larger than 262144 bytes"),
            (dict(link=True), f"{x} is a symlink"),
            (dict(folder=True), f"{x} is not a regular file"),
        ]
        for case, why in cases:
            with self.subTest(why=why):
                if self.exclusions.is_symlink() or self.exclusions.is_file():
                    self.exclusions.unlink()
                elif self.exclusions.is_dir():
                    self.exclusions.rmdir()
                if case.get("link"):
                    target = self.work / "original/elsewhere"
                    target.write_text("")
                    self.exclusions.symlink_to(target)
                elif case.get("folder"):
                    self.exclusions.mkdir()
                elif "raw" in case:
                    self.exclude(raw=case["raw"])
                else:
                    self.exclude(*case["paths"], mode=case["mode"])
                result = self.boot(xuid=case.get("xuid", "0"))
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("[Pivoted Root] Media prototype skipped: source missing or protected mount unavailable: "
                              f"the album exclusion list {why}, so no album is shared\n", result.stdout)
                self.assertEqual([], self.mounts())
                self.assert_released()
                self.assertFalse((self.work / "am.log").exists())
        # 262144 bytes is still read.
        self.exclusions.rmdir()
        self.exclude(raw=b"DCIM/Camera\n" + b"x" * (262144 - 12))
        result = self.boot()
        self.assertEqual({"Screenshots (One UI)"}, self.names())

    def test_nonempty_guest_mount_point_skips_that_album_only(self):
        own = self.guest_dir("OneUI/Camera (One UI)")
        (own / SECRETS[3]).write_text("guest")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("[Pivoted Root] Media prototype skipped album 1 of 2 (OneUI/Camera (One UI)): guest storage "
                      f"aliases could not be protected: the guest's own {own} exists and is not empty\n", result.stdout)
        self.assertNotIn("rolled back", result.stdout)
        self.assertEqual({"Screenshots (One UI)"}, self.names())
        self.assertEqual("guest", (own / SECRETS[3]).read_text())
        self.assertIn("albums: 2 found, 0 excluded, 1 mounted, 1 skipped", result.stdout)
        self.assertEqual(1, len((self.work / "am.log").read_text().splitlines()))

    def test_oneui_that_is_a_link_a_file_or_a_mount_publishes_nothing(self):
        parent = self.media0 / "OneUI"
        elsewhere = self.work / "elsewhere"
        elsewhere.mkdir()
        parent.symlink_to(elsewhere, target_is_directory=True)
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"Media prototype skipped every album: the guest's own {parent} is a symlink\n", result.stdout)
        self.assertIn("albums: 2 found, 0 excluded, 0 mounted, 2 skipped", result.stdout)
        self.assertEqual([], list(elsewhere.iterdir()))
        self.assertEqual(set(), self.names())
        self.assert_released()
        parent.unlink()
        parent.write_text("guest's file")
        result = self.boot()
        self.assertIn(f"skipped every album: the guest's own {parent} exists and is not a directory\n", result.stdout)
        self.assertEqual("guest's file", parent.read_text())
        parent.unlink()
        parent.mkdir()
        st = json.loads(self.state.read_text())
        st["mounts"].append({"path": str(parent), "opts": "rw", "ino": "", "shadow": None, "dev": "0:9",
                             "root": "/", "fs": "tmpfs"})
        self.state.write_text(json.dumps(st))
        result = self.boot(keep=True)
        self.assertIn(f"skipped every album: the guest's own {parent} is a mount\n", result.stdout)

    def test_alias_flag_failure_rolls_that_album_back(self):
        result = self.boot("bad-alias", bad="/OneUI/Camera (One UI)")
        self.assertEqual(0, result.returncode, result.stderr)
        alias = self.view(self.vold_bases[1], "Camera (One UI)")
        self.assertIn("[Pivoted Root] Media prototype skipped album 1 of 2 (OneUI/Camera (One UI)): guest storage "
                      f"aliases could not be protected: alias lacks wanted flags: {alias} has options rw,nosuid, "
                      "missing ro,nodev,noexec,noatime,nosymfollow\n", result.stdout)
        self.assertIn("[Pivoted Root] Media prototype: rolled back OneUI/Camera (One UI): 2 mount(s) of the album and "
                      "its covers detached (0 with umount -l), 0 left; mount point "
                      f"{self.media0}/OneUI/Camera (One UI) removed\n", result.stdout)
        self.assertFalse((self.media0 / "OneUI/Camera (One UI)").exists())
        self.assertEqual({"Screenshots (One UI)"}, self.names())
        self.assertNotIn("FATAL", result.stdout)
        self.assert_released()

    def test_a_cover_failure_rolls_back_the_album_and_its_covers(self):
        self.photo("DCIM/Camera/Burst", SECRETS[6])
        self.photo("DCIM/Camera/.thumbs", SECRETS[5])
        result = self.boot("bad-cover", bad="/OneUI/Camera (One UI)/")
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout
        self.assertRegex(out, r"skipped album 1 of 3 \(OneUI/Camera \(One UI\)\): guest storage aliases could not be "
                         r"protected: cover lacks wanted flags: \S+/mnt/runtime/default/emulated/0/OneUI/Camera "
                         r"\(One UI\)/\S+ has options rw,nosuid, missing ro,nodev,noexec,noatime,nosymfollow\n")
        # Two covers and the album on each of the two views.
        self.assertIn("rolled back OneUI/Camera (One UI): 6 mount(s) of the album and its covers detached "
                      "(0 with umount -l), 0 left; mount point ", out)
        self.assertEqual({"Burst (One UI)", "Screenshots (One UI)"}, self.names())
        self.assertEqual([], self.cover_mounts())
        for b in self.vold_bases:
            self.assertEqual([], list(Path(self.view(b)).glob("Camera (One UI)*")))
        self.assert_released()
        # An rbind that fails on the second view takes the first one back too.
        result = self.boot("rbind-fails", bad="/OneUI/Camera (One UI)")
        self.assertIn("could not be protected: rbind onto alias " + self.view(self.vold_bases[1], "Camera (One UI)")
                      + " failed\n", result.stdout)
        self.assertIn("rolled back OneUI/Camera (One UI): 3 mount(s) of the album and its covers detached", result.stdout)
        self.assertEqual({"Burst (One UI)", "Screenshots (One UI)"}, self.names())
        self.assert_released()

    def test_alias_that_cannot_be_detached_asks_for_one_ui(self):
        result = self.boot("bad-alias,stuck-alias", bad="/OneUI/Camera (One UI)")
        self.assertEqual(0, result.returncode, result.stderr)
        alias = self.view(self.vold_bases[1], "Camera (One UI)")
        out = result.stdout
        self.assertIn(f"[Pivoted Root] Media prototype FATAL: could not detach {alias} even with umount -l; "
                      "asking the host to return to One UI\n", out)
        self.assertLess(out.index("skipped album 1 of 2"), out.index("FATAL"))
        self.assertEqual("media:unprotected-mount", (self.work / "props/oneos.guest.powerctl").read_text())
        self.assertIn("rolled back OneUI/Camera (One UI): 1 mount(s) of the album and its covers detached (0 with "
                      f"umount -l), 1 left; mount point {self.media0}/OneUI/Camera (One UI) kept\n", out)
        self.assertTrue((self.media0 / "OneUI/Camera (One UI)").is_dir())
        # A return already asked for is not replaced.
        (self.work / "props/oneos.guest.powerctl").write_text("app:app")
        self.boot("bad-alias,stuck-alias", bad="/OneUI/Camera (One UI)")
        self.assertEqual("app:app", (self.work / "props/oneos.guest.powerctl").read_text())

    def test_propagated_alias_is_not_bound_twice(self):
        self.photo("DCIM/Camera/Burst", "b.jpg")
        result = self.boot("propagate")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("mounted as OneUI/Camera (One UI) (2 media files, 1 child dirs covered, 2 aliases)", result.stdout)
        paths = [m["path"] for m in self.mounts() if "/OneUI/" in m["path"]]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(8, len(paths))

    def test_no_vold_view_times_out_and_releases_the_hold(self):
        result = self.boot(vold_at=0)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("[Pivoted Root] Media prototype skipped: the guest's vold built no storage view of "
                      f"{self.work}/data/media in 180 s (seen: none)\n",
                      result.stdout)
        self.assertEqual(180, json.loads(self.state.read_text())["sleeps"])
        self.assertEqual([], self.mounts())
        self.assertFalse((self.media0 / "OneUI").exists())
        self.assertFalse((self.work / "am.log").exists())
        self.assert_released()

    def test_guest_starts_own_early_binds_are_no_sign_of_vold(self):
        self.early()
        result = self.boot(vold_at=0, keep=True)
        self.assertEqual(0, result.returncode, result.stderr)
        seen = " ".join(sorted(str(self.work / b) for b in self.early_bases))
        self.assertIn(f"the guest's vold built no storage view of {self.work}/data/media in 180 s (seen: "
                      f"{seen})\n", result.stdout)
        self.assertEqual(set(), self.names())
        self.assert_released()
        # Once vold replaced them, only vold's views are used.
        self.early()
        result = self.boot(vold_at=2, keep=True)
        self.assertEqual({"Camera (One UI)": self.targets("Camera (One UI)"),
                          "Screenshots (One UI)": self.targets("Screenshots (One UI)")}, self.album_mounts())

    def test_leftover_mount_points_and_holds_are_removed_with_rmdir_only(self):
        data = self.work / "data"
        for name, subs in (("aaaaaa", ("host", "album")), ("bbbbbb", ("album_camera", "album_screenshots", "host")),
                           ("eeeeee", ("media0", "cover", "album"))):
            for sub in subs:
                (data / f"oneos_media_hold.{name}" / sub).mkdir(parents=True)
        (data / "oneos_media_hold.eeeeee/excluded").write_text("DCIM/Camera\n")
        (data / "oneos_media_hold.eeeeee/albums").write_text("1\tDCIM/Camera\tCamera (One UI)\n")
        kept = data / "oneos_media_hold.cccccc"
        (kept / "media0").mkdir(parents=True)
        (kept / "media0/keep").write_text("x")
        # A symlinked leftover is not followed: its empty target stays.
        target = self.work / "elsewhere"
        (target / "album_camera").mkdir(parents=True)
        (target / "excluded").write_text("x")
        (data / "oneos_media_hold.dddddd").symlink_to(target, target_is_directory=True)
        (self.media0 / "DCIM/OneUI_Camera").mkdir()
        (self.media0 / "Pictures/OneUI_Screenshots").mkdir()
        full = self.media0 / "DCIM/OneUI_Screenshots"
        full.mkdir()
        (full / SECRETS[3]).write_text("guest")
        (self.media0 / "DCIM/OneOSShareTest").mkdir()
        for gone in ("Old Album (One UI)", "Camera (One UI)"):
            self.guest_dir("OneUI/" + gone)
        guest = self.guest_dir("OneUI/Guest's own")
        (guest / SECRETS[3]).write_text("guest")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout
        self.assertIn("[Pivoted Root] Media prototype: removed 3 empty hold dir(s) left by an earlier boot\n", out)
        self.assertIn("[Pivoted Root] Media prototype: removed 4 empty mount-point dir(s) left by an earlier boot\n", out)
        self.assertEqual(sorted([kept, data / "oneos_media_hold.dddddd"]),
                         sorted(data.glob("oneos_media_hold.*")))
        self.assertEqual("x", (kept / "media0/keep").read_text())
        self.assertTrue((target / "album_camera").is_dir())
        self.assertEqual("x", (target / "excluded").read_text())
        self.assertTrue((self.media0 / "DCIM/OneOSShareTest").is_dir())
        self.assertFalse((self.media0 / "Pictures/OneUI_Screenshots").exists())
        self.assertFalse((self.media0 / "DCIM/OneUI_Camera").exists())
        self.assertFalse((self.media0 / "OneUI/Old Album (One UI)").exists())
        self.assertEqual("guest", (full / SECRETS[3]).read_text())
        self.assertEqual("guest", (guest / SECRETS[3]).read_text())
        self.assertEqual({"Camera (One UI)", "Screenshots (One UI)"}, self.names())
        # Sharing off: nothing is touched.
        self.opt_in.rmdir()
        st = json.loads(self.state.read_text())
        st["mounts"] = []
        self.state.write_text(json.dumps(st))
        result = self.boot()
        self.assertNotIn("Media prototype", result.stdout)

    # --- the stage's failures ------------------------------------------

    def test_missing_kernel_flags_skip_share(self):
        result = self.boot("lack-nosymfollow")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("protected mount unavailable", result.stdout)
        self.assertRegex(result.stdout, r"hold mount lacks wanted flags: \S+/host has options "
                         r"ro,nosuid,nodev,noexec,noatime, missing nosymfollow\n")
        self.assertEqual([], self.mounts())
        self.assert_released()

    def test_remount_error_text_is_logged(self):
        result = self.boot("remount-fails")
        self.assertEqual(0, result.returncode, result.stderr)
        lines = self.lines(result)
        self.assertEqual(1, len(lines), result.stdout)
        self.assertIn("protected mount unavailable: remount of the hold with " + FLAGS
                      + " failed: mount: mounting ", lines[0])
        self.assertTrue(lines[0].endswith("failed: Invalid argument"), lines[0])
        self.assertEqual([], self.mounts())
        self.assert_released()

    def test_cover_that_cannot_be_made_read_only_shares_nothing(self):
        result = self.boot("cover-remount-fails")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("protected mount unavailable: remount of the cover dir with " + FLAGS + " failed\n",
                      result.stdout)
        self.assertEqual([], self.mounts())
        self.assert_released()

    def test_unlabelled_cover_is_logged_and_still_used(self):
        self.photo("DCIM/Camera/Burst", "b.jpg")
        result = self.boot("no-selinux")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Media prototype: the empty cover dir keeps its own SELinux label (One UI's media/0 label "
                      "unreadable)\n", result.stdout)
        self.assertIn("mounted as OneUI/Camera (One UI) (2 media files, 1 child dirs covered", result.stdout)

    def test_parent_hold_unmount_failure_stops_boot(self):
        result = self.boot("busy-parent")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Not starting the guest", result.stdout)
        self.assertRegex(result.stdout, r"could not detach a mount \(\S+/host\)")
        self.assertEqual({}, self.album_mounts())

    def test_symlinked_media0_in_one_ui_shares_nothing(self):
        real = self.work / "original/media/real0"
        os.rename(self.one_ui, real)
        self.one_ui.symlink_to(real, target_is_directory=True)
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("protected mount unavailable: One UI's media/0 is a symlink\n", result.stdout)
        self.assertEqual([], self.mounts())
        self.assert_released()


class GuestMediaWatch(Base):
    clock_scale = "1"
    ARGS = ("-d -b main,system -v threadtime -s MediaProvider:V ModernMediaScanner:V MediaService:V "
            "MediaReceiver:V DatabaseHelper:V ExternalStorageServiceImpl:V MediaVolume:V IdleService:V FuseDaemon:W")

    def watch_lines(self, result):
        return [l for l in result.stdout.splitlines() if l.startswith("[Pivoted Root] Media watch:")]

    def test_the_watch_logs_the_first_answer_and_each_change_only(self):
        # S25 Ultra sessions db19aee410bd79ff, 75e538ac0ed725df: the rows under OneUI/ were gone before
        # the scan request. 5 -> 5 -> 0 -> 12: nothing is logged for an unchanged sample.
        (self.work / "watch.seq").write_text("5\n5\n0\n12\n")
        (self.work / "watch.total").write_text("40\n")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        lines = self.watch_lines(result)
        pre = r"\[Pivoted Root\] Media watch: \d\d:\d\d:\d\d \(\d+ s after the zygote launch\): "
        want = [pre + r"MediaStore lists 5 item\(s\) under OneUI/ \(first answer\)$",
                r"\[Pivoted Root\] Media watch: MediaStore has 40 row\(s\) in all at the first answer$",
                pre + r"MediaStore lists 0 item\(s\) under OneUI/ \(was 5\)$",
                pre + r"MediaStore lists 12 item\(s\) under OneUI/ \(was 0\)$"]
        # The late step's own total line is a Media watch line too, after the scan request.
        late = r"\[Pivoted Root\] Media watch: MediaStore has 40 row\(s\) in all 30 s after the scan request$"
        # 240 s after boot_completed in 10 s steps: 25 samples, or 24 when the real seconds the run
        # takes (the fake clock is the real one plus the sleeps) add one.
        done = r"\[Pivoted Root\] Media watch: done after 2[45] sample\(s\), last count 12$"
        got = [l for l in lines if not re.search(late, l)]
        self.assertEqual(len(want) + 1, len(got), lines)
        for l, rx in zip(got, want + [done]):
            self.assertRegex(l, rx)
        self.assertEqual(1, len([l for l in lines if re.search(late, l)]), lines)
        samples = int(re.search(r"done after (\d+) sample", got[-1])[1])
        self.assertEqual(samples, len((self.work / "content-watch.log").read_text().splitlines()) - 1)
        # The late step's counts are the watch's neither way.
        self.assertIn("Media prototype: MediaStore lists 2 item(s) under OneUI/ before the scan request\n", result.stdout)
        self.assertIn("Media prototype: MediaStore lists 2 item(s) under OneUI/ 30 s after the scan request\n", result.stdout)

    def test_no_answer_is_logged_once_per_transition(self):
        (self.work / "watch.seq").write_text("none\nnone\n3\nnone\nnone\n")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        text = "\n".join(self.watch_lines(result))
        self.assertEqual(2, len(re.findall(r"MediaStore did not answer \(Error while accessing provider:media\)", text)), text)
        self.assertEqual(1, len(re.findall(r"lists 3 item\(s\) under OneUI/ \(first answer\)", text)), text)
        self.assertRegex(text, r"done after 2[45] sample\(s\), last count 3")

    def test_the_watch_starts_before_the_albums_and_never_stops_the_publish(self):
        (self.work / "watch.seq").write_text("none\n")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout
        self.assertLess(out.index("Media watch:"), out.index("Media prototype: album 1 of"))
        self.assertIn("Media prototype: albums: 2 found, 0 excluded, 2 mounted, 0 skipped\n", out)
        self.assert_released()

    def test_the_log_excerpt_drops_every_file_name_and_non_ascii_bytes(self):
        lines = [b"--------- beginning of main",
                 b"11-17 10:00:01.000  4242  4242 I MediaProvider: Scanning /storage/emulated/0/DCIM/Camera/IMG_1.jpg done",
                 b"11-17 10:00:02.000  4242  4242 D ModernMediaScanner: /storage/emulated/0/OneUI/Camera (One UI)/IMG_1.jpg added",
                 b"11-17 10:00:03.000  4242  4242 W MediaProvider: caf\xc3\xa9 /data/media/0/Pictures/\xd8\xa7 x.jpg",
                 b"11-17 10:00:04.000  4242  4242 W MediaProvider: open /mnt/pass_through/0/emulated/0/Movies/m.mp4 failed",
                 b"11-17 10:00:05.000  4242  4242 I MediaService: volume external_primary attached",
                 b"11-17 10:00:06.000  4242  4242 I MediaProvider: " + b"y" * 400]
        (self.work / "logcat.out").write_bytes(b"\n".join(lines) + b"\n")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        boot = [l[len("[Pivoted Root] Media log (boot): "):] for l in result.stdout.splitlines()
                if l.startswith("[Pivoted Root] Media log (boot): ")]
        self.assertRegex(boot[0], r"^6 line\(s\) kept of 6; 0 quota-type error line\(s\) left out; MediaProvider "
                         + re.escape(MP_PKG) + r" pid 4242 running \d+ s$")
        got = boot[1:]
        self.assertEqual(6, len(got), got)
        self.assertTrue(got[0].endswith("I MediaProvider: Scanning /storage/emulated/0/\u2026"), got[0])
        self.assertTrue(got[1].endswith("D ModernMediaScanner: /storage/emulated/0/OneUI/\u2026"), got[1])
        self.assertTrue(got[2].endswith("W MediaProvider: caf /data/media/0/\u2026"), got[2])
        self.assertTrue(got[3].endswith("W MediaProvider: open /mnt/pass_through/0/emulated/0/\u2026"), got[3])
        self.assertTrue(got[4].endswith("I MediaService: volume external_primary attached"), got[4])
        self.assertEqual(300, len(got[5].encode()))
        out = result.stdout
        for leak in ("IMG_1", "DCIM", "Camera", "One UI)", "Pictures", "Movies", "m.mp4", "x.jpg", "\xc3", "beginning of main"):
            self.assertNotIn(leak, "\n".join(l for l in out.splitlines() if "Media log" in l))
        self.assertTrue(all(ord(c) < 128 or c == "\u2026" for c in out))
        # Two captures: the boot part takes everything, the late part only what came after it.
        # (and one of system_server's media hold line, see GuestMediaHold)
        log = [l for l in (self.work / "logcat.log").read_text().splitlines() if "OneOSMediaHold" not in l]
        self.assertEqual(2, len(log), log)
        self.assertEqual(self.ARGS, log[0])
        self.assertRegex(log[1], r"^-d -b main,system -v threadtime -T \d+\.000 -s MediaProvider:V ")
        self.assertIn("Media log (late): 6 line(s) kept of 6;", out)
        self.assertLess(out.index("Media log (boot)"), out.index("Media watch: done after"))
        self.assertLess(out.index("Media watch: done after"), out.index("Media log (late)"))

    def test_the_excerpt_keeps_the_last_250_and_150_lines(self):
        (self.work / "logcat.out").write_text("".join(f"11-17 10:00:00.000  1  1 I MediaProvider: line {i}\n" for i in range(300)))
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Media log (boot): 250 line(s) kept of 300;", result.stdout)
        self.assertIn("Media log (late): 150 line(s) kept of 300;", result.stdout)
        self.assertIn("Media log (boot): 11-17 10:00:00.000  1  1 I MediaProvider: line 299\n", result.stdout)
        self.assertNotIn("line 49\n", result.stdout)
        self.assertNotIn("Media log (late): 11-17 10:00:00.000  1  1 I MediaProvider: line 149\n", result.stdout)

    def test_a_missing_media_provider_is_logged_and_a_failing_logcat_stops_nothing(self):
        shutil.rmtree(self.work / "proc/4242")
        self.script(self.work / "fakebin/logcat", "#!/bin/sh\nexit 1\n")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Media log (boot): 0 line(s) kept of 0; 0 quota-type error line(s) left out; MediaProvider not running\n", result.stdout)
        self.assertIn("Media log (late): 0 line(s) kept of 0; 0 quota-type error line(s) left out; MediaProvider not running\n", result.stdout)
        self.assertIn("Media hold: system_server logged nothing (the set's services_patched.jar has no hold yet, "
                      "or logcat had rotated)\n", result.stdout)
        self.assertIn("Media prototype: MediaStore lists 2 item(s) under OneUI/ 30 s after the scan request\n", result.stdout)


class GuestMediaHold(Base):
    """The media hold's signal (S25 Ultra session a12bf19e1931e81a: MediaProvider's mount scan found
    OneUI/ empty and deleted every row). The guest's services.jar waits while oneos.media.hold is 1
    until oneos.media.published is 1; guest_start.sh sets both and sends published once."""
    RELEASED = r"\[Pivoted Root\] Media hold: released at \d\d:\d\d:\d\d \(\d+ s after the zygote launch, "
    ON = "[Pivoted Root] Media hold: on (StorageManagerService waits up to 15 s for the albums before MediaProvider's mount scan)"

    def media_props(self):
        log = self.work / "setprop.log"
        return [l for l in log.read_text().splitlines() if l.startswith("oneos.media.")] if log.exists() else []

    def released(self, result):
        return [l for l in result.stdout.splitlines() if l.startswith("[Pivoted Root] Media hold: released")
                or l.startswith("[Pivoted Root] Media hold: setprop oneos.media.published 1")]

    def stale(self):
        # The last boot's values: the props may survive it.
        (self.work / "props/oneos.media.hold").write_text("1")
        (self.work / "props/oneos.media.published").write_text("1")

    def prop(self, name):
        return (self.work / "props" / name).read_text()

    def late(self, stubs):
        """oneos_media_late with stubs after the real functions, started as next to the zygote."""
        bb = self.work / "bb-stub"
        self.script(bb, '#!/bin/sh\ncase "$1" in sleep) exit 0 ;; esac\nexec "$@"\n')
        shell = (HOLD + stubs + "\noneos_media_tmp=/nonexistent-hold\noneos_media_hold_on=1\noneos_media_hold_sent=0\n"
                 '( oneos_media_late; oneos_media_hold_done "oneos_media_late ended without releasing it" )\n')
        return subprocess.run(SHELL + ["-c", shell], capture_output=True, text=True, timeout=120,
                              env={**os.environ, "BB": str(bb), "MEDIA_TEST_ROOT": str(self.work),
                                   "PATH": str(self.work / "fakebin") + ":" + os.environ["PATH"]})

    def test_published_goes_to_0_before_hold_1_and_to_1_once_after_the_albums(self):
        self.stale()
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout
        self.assertEqual(["oneos.media.published=0", "oneos.media.hold=1", "oneos.media.published=1"], self.media_props())
        self.assertEqual("1", self.prop("oneos.media.hold"))
        self.assertEqual("1", self.prop("oneos.media.published"))
        self.assertEqual(1, out.count(self.ON + "\n"))
        rel = self.released(result)
        self.assertEqual(1, len(rel), rel)
        self.assertRegex(rel[0], self.RELEASED + r"albums published: 2 mounted, 0 skipped\)$")
        summary = out.index("Media prototype: albums: 2 found, 0 excluded, 2 mounted, 0 skipped\n")
        self.assertLess(out.index(self.ON), out.index("Media prototype: the guest's vold built"))
        self.assertLess(summary, out.index(rel[0]))
        self.assertLess(out.index(rel[0]), out.index("Media prototype: hold released\n"))
        self.assertNotIn("ended without releasing", out)
        # Every album was mounted on every vold view when published went to 1.
        at = {m["path"] for m in json.loads((self.work / "published.state").read_text())["mounts"]}
        for name in ("Camera (One UI)", "Screenshots (One UI)"):
            self.assertLessEqual(self.targets(name), at, name)

    def test_the_knob_turns_the_hold_off_and_the_albums_are_still_published(self):
        self.stale()
        result = self.boot(env={"ONEOS_GUEST_MEDIA_HOLD": "0"})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("[Pivoted Root] Media hold: off (ONEOS_GUEST_MEDIA_HOLD=0)\n", result.stdout)
        self.assertEqual(["oneos.media.hold=0", "oneos.media.published=0"], self.media_props())
        self.assertEqual("0", self.prop("oneos.media.hold"))
        self.assertEqual([], self.released(result))
        self.assertIn("Media prototype: albums: 2 found, 0 excluded, 2 mounted, 0 skipped\n", result.stdout)
        self.assertIn("[Pivoted Root] Media hold: system_server logged nothing (the hold was off this boot)\n", result.stdout)
        # Any other value is the default: on.
        result = self.boot(env={"ONEOS_GUEST_MEDIA_HOLD": "1"})
        self.assertIn(self.ON + "\n", result.stdout)
        self.assertEqual(1, len(self.released(result)))

    def test_without_the_media_prototype_the_hold_is_off_on_every_boot(self):
        self.stale()
        self.opt_in.rmdir()
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["[Pivoted Root] Media hold: off (the media prototype does not run this boot)"],
                         [l for l in result.stdout.splitlines() if "Media hold" in l])
        self.assertEqual(["oneos.media.hold=0", "oneos.media.published=0"], self.media_props())
        self.assertEqual("0", self.prop("oneos.media.hold"))
        self.assertEqual("0", self.prop("oneos.media.published"))

    def test_no_vold_view_releases_the_hold_once(self):
        result = self.boot(vold_at=0)
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout
        rel = self.released(result)
        self.assertEqual(1, len(rel), rel)
        self.assertRegex(rel[0], self.RELEASED + r"skipped: vold built no storage view\)$")
        self.assertLess(out.index("Media prototype skipped: the guest's vold built no storage view"), out.index(rel[0]))
        self.assertLess(out.index(rel[0]), out.index("Media prototype: hold released\n"))
        self.assertEqual(["oneos.media.published=0", "oneos.media.hold=1", "oneos.media.published=1"], self.media_props())

    def test_no_album_mounted_releases_the_hold_after_the_summary(self):
        self.exclude("DCIM/Camera", "DCIM/Screenshots")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        rel = self.released(result)
        self.assertEqual(1, len(rel), rel)
        self.assertRegex(rel[0], self.RELEASED + r"no album mounted: 0 skipped\)$")
        self.assertLess(result.stdout.index("albums: 2 found, 2 excluded, 0 mounted, 0 skipped\n"),
                        result.stdout.index(rel[0]))

    def test_views_that_never_settle_release_the_hold(self):
        result = self.late('oneos_media_views() { n=$(cat "$MEDIA_TEST_ROOT/n" 2>/dev/null || echo 0); n=$((n + 1)); '
                           'echo "$n" > "$MEDIA_TEST_ROOT/n"; echo "/mnt/pass_through/0/emulated/$n"; }\n'
                           'oneos_media_release_late() { echo release_late; }\noneos_media_watch() { :; }\n')
        self.assertEqual(0, result.returncode, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(3, len(lines), lines)
        self.assertIn("Media prototype skipped: the guest's storage views did not settle", lines[0])
        self.assertRegex(lines[1], self.RELEASED + r"skipped: the storage views did not settle\)$")
        self.assertEqual("release_late", lines[2])
        self.assertEqual(["oneos.media.published=1"], self.media_props())

    def test_albums_that_cannot_be_listed_release_the_hold(self):
        result = self.late('oneos_media_views() { echo /mnt/pass_through/0/emulated/0; }\n'
                           'oneos_media_albums() { echo nonsense; }\n'
                           'oneos_media_release_late() { echo release_late; }\noneos_media_watch() { :; }\n')
        self.assertEqual(0, result.returncode, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual("[Pivoted Root] Media prototype skipped: One UI's albums could not be listed in the hold", lines[-3])
        self.assertRegex(lines[-2], self.RELEASED + r"skipped: the albums could not be listed\)$")
        self.assertEqual("release_late", lines[-1])
        self.assertEqual(["oneos.media.published=1"], self.media_props())

    def test_the_safety_net_releases_a_hold_a_path_forgot_and_only_once(self):
        result = self.late("oneos_media_late() { oneos_media_t0=$(date +%s); }\n")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertRegex(result.stdout, "^" + self.RELEASED + r"oneos_media_late ended without releasing it\)\n$")
        self.assertEqual(["oneos.media.published=1"], self.media_props())
        (self.work / "setprop.log").unlink()
        result = self.late('oneos_media_late() { oneos_media_t0=$(date +%s); oneos_media_hold_done "first"; '
                           'oneos_media_hold_done "second"; }\n')
        self.assertRegex(result.stdout, "^" + self.RELEASED + r"first\)\n$")
        self.assertEqual(["oneos.media.published=1"], self.media_props())

    def test_every_early_return_before_the_summary_releases_the_hold(self):
        body = START[START.index("oneos_media_late() {\n"):]
        body = body[:body.index("\n}\n")]
        lines = body.split("\n")
        summary = [i for i, l in enumerate(lines) if 'Media prototype: albums: $oneos_media_listed found' in l]
        self.assertEqual(1, len(summary))
        returns = [i for i, l in enumerate(lines[:summary[0]]) if re.search(r"\breturn\b", l)]
        self.assertEqual(2, len(returns), returns)
        for i in returns:
            self.assertTrue(any("oneos_media_hold_done " in l for l in lines[i - 6:i]), lines[i - 6:i + 1])
        # The normal path's release is the first one after the summary, before the hold dir goes.
        after = lines[summary[0]:]
        first = next(i for i, l in enumerate(after) if "oneos_media_hold_done " in l)
        self.assertLess(first, next(i for i, l in enumerate(after) if l.strip() == "oneos_media_release_late"))
        # Nothing releases it while the albums are being mounted.
        loop = "\n".join(lines[lines.index("    oneos_media_mounted=0"):summary[0]])
        self.assertIn("oneos_media_publish_album", loop)
        self.assertNotIn("oneos_media_hold_done", loop)

    def test_system_servers_hold_line_is_logged_after_media_providers(self):
        (self.work / "hold.out").write_bytes(b"--------- beginning of system\n" + b"".join(
            b"11-18 10:00:0%d.000  1500  1530 I OneOSMediaHold: waited %d ms caf\xc3\xa9\n" % (i, i * 100)
            for i in range(5)))
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        got = [l for l in result.stdout.splitlines() if l.startswith("[Pivoted Root] Media hold: system_server: ")]
        self.assertEqual([f"[Pivoted Root] Media hold: system_server: 11-18 10:00:0{i}.000  1500  1530 I OneOSMediaHold: "
                          f"waited {i * 100} ms caf" for i in (2, 3, 4)], got)
        self.assertLess(result.stdout.index("Media log (boot): "), result.stdout.index(got[0]))
        log = [l for l in (self.work / "logcat.log").read_text().splitlines() if "OneOSMediaHold" in l]
        self.assertEqual(["-d -b main,system -s OneOSMediaHold"], log)

    def test_quota_type_warnings_and_their_stack_lines_are_left_out(self):
        # Both S25 Ultra sessions of 2026-11-18: every kept line was one of these.
        lines = ["11-18 10:00:01.000  4242  4300 W MediaProvider: Failed to update quota type for /storage/emulated/0/OneUI/A (One UI)/a.jpg",
                 "11-18 10:00:01.000  4242  4300 W MediaProvider: java.io.IOException: Operation not supported",
                 "11-18 10:00:01.000  4242  4242 I MediaProvider: other thread in between",
                 "11-18 10:00:01.000  4242  4300 W MediaProvider: \tat android.os.FileUtils.x(FileUtils.java:1)",
                 "11-18 10:00:01.000  4242  4300 W MediaProvider: \tat com.android.providers.media.y(M.java:2)",
                 "11-18 10:00:02.000  4242  4300 I MediaProvider: same thread next",
                 "11-18 10:00:02.000  4242  4300 W MediaProvider: \tat stray.Stack(S.java:3)",
                 "11-18 10:00:03.000  4242  4301 W MediaProvider: Failed to update quota type for /storage/emulated/0/OneUI/B (One UI)/b.png",
                 "11-18 10:00:03.000  4242  4301 W MediaProvider: java.io.IOException: x",
                 "11-18 10:00:04.000  4242  4242 I ModernMediaScanner: Scan of external_primary done"]
        (self.work / "logcat.out").write_text("\n".join(lines) + "\n")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        for part in ("boot", "late"):
            pre = f"[Pivoted Root] Media log ({part}): "
            got = [l[len(pre):] for l in result.stdout.splitlines() if l.startswith(pre)]
            self.assertRegex(got[0], r"^4 line\(s\) kept of 10; 6 quota-type error line\(s\) left out; MediaProvider ")
            self.assertEqual(["other thread in between", "same thread next", "at stray.Stack(S.java:3)",
                              "Scan of external_primary done"], [l.split(": ", 1)[1] for l in got[1:]])


class GuestMediaShareHold(Base):
    """The media hold for Share my files (full access in "One UI files in the other system", owner's decision of
    2026-11-18, S25 Ultra): the same race as the albums, so the same hold. The launch sets it when folders are held
    and the album prototype is off; the share_folders part releases it after its binds."""
    ON = "[Pivoted Root] Media hold: on (StorageManagerService waits up to 15 s for the shared folders before MediaProvider's mount scan)"
    SHARE = {"ONEOS_SHARE_FILES": "1", "SHARE_HELD": "DCIM Pictures"}

    def setUp(self):
        super().setUp()
        self.note = self.work / "data/oneos_share_hold_set"
        self.log = self.work / "data/oneos_share.log"
        (self.work / "hold").mkdir()
        self.script(self.work / "fakebin/sleep", "#!/bin/sh\nexit 0\n")
        self.script(self.work / "bb-share", '#!/bin/sh\ncase "$1" in mount|umount) exit 0 ;; esac\nexec "$@"\n')
        self.script(self.work / "system/bin/content", "#!/bin/sh\nexit 0\n")

    def props(self):
        return [l for l in (self.work / "setprop.log").read_text().splitlines() if l.startswith("oneos.media.")] \
            if (self.work / "setprop.log").exists() else []

    def binds(self, storage_up=True, note=None, held="DCIM Pictures"):
        """The share_folders part alone, in the background as in guest_start.sh, with the launch's note."""
        (self.work / "setprop.log").unlink(missing_ok=True)
        pt = self.work / "mnt/pass_through/0/emulated"
        (pt / "0").mkdir(parents=True, exist_ok=True)
        (self.work / "mountinfo").write_text("1 0 0:1 / / rw - tmpfs tmpfs rw\n"
                                             + (f"9 1 0:70 / {pt} rw - fuse fuse rw\n" if storage_up else ""))
        self.log.unlink(missing_ok=True)
        self.note.unlink(missing_ok=True)
        shell = SHARE_BINDS + "wait\n"
        # The launch's note is written after the block removes last boot's, as the zygote launch does later on.
        if note is not None:
            shell = shell.replace("rm -f /data/oneos_share_hold_set\n",
                                  f"rm -f /data/oneos_share_hold_set\necho {note} > /data/oneos_share_hold_set\n", 1)
        shell = shell.replace("/proc/self/mountinfo", str(self.work / "mountinfo"))
        shell = re.sub(r"""(?<![\w/.])/(data|postinstall|mnt|storage|system)(?=[/\s'"])""",
                       lambda m: str(self.work / m[1]), shell)
        result = subprocess.run(
            SHELL + ["-c", shell], capture_output=True, text=True, timeout=120,
            env={**os.environ, "BB": str(self.work / "bb-share"), "MEDIA_TEST_ROOT": str(self.work),
                 "SHARE_HELD": held, "SHARE_HOLD": str(self.work / "hold"), "VOLD_NAME": "vold",
                 "PATH": str(self.work / "fakebin") + ":" + os.environ["PATH"]})
        self.assertEqual(0, result.returncode, result.stderr)
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_hold_is_on_with_the_share_and_the_note_tells_the_binds_part(self):
        (self.work / "props/oneos.media.published").write_text("1")  # last boot's
        result = self.boot(env=self.SHARE)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["oneos.media.published=0", "oneos.media.hold=1"], self.props())
        self.assertEqual(1, result.stdout.splitlines().count(self.ON))
        self.assertEqual("1\n", self.note.read_text())
        # Not released by the launch: the binds part does that.
        self.assertNotIn("Media hold: released", result.stdout)
        self.assertIn("Media prototype: off (Share my files is on", result.stdout)
        # The album hold's own line stays out of it.
        self.assertNotIn("for the albums", result.stdout)

    def test_the_knob_and_a_failing_setprop_leave_it_off_with_a_note_of_0(self):
        result = self.boot(env={**self.SHARE, "ONEOS_GUEST_MEDIA_HOLD": "0"})
        self.assertIn("[Pivoted Root] Media hold: off (ONEOS_GUEST_MEDIA_HOLD=0)\n", result.stdout)
        self.assertEqual(["oneos.media.hold=0", "oneos.media.published=0"], self.props())
        self.assertEqual("0\n", self.note.read_text())
        self.script(self.work / "fakebin/setprop", "#!/bin/sh\nexit 1\n")
        result = self.boot(env=self.SHARE)
        self.assertIn("[Pivoted Root] Media hold: off (setprop failed)\n", result.stdout)
        self.assertEqual("0\n", self.note.read_text())

    def test_the_hold_is_never_set_when_neither_feature_is_on(self):
        self.note.write_text("1\n")  # last boot's
        # The albums on, nothing shared: the album hold, no note.
        result = self.boot()
        self.assertIn("Media hold: on (StorageManagerService waits up to 15 s for the albums", result.stdout)
        self.assertNotIn("shared folders", result.stdout)
        self.assertEqual(["oneos.media.published=0", "oneos.media.hold=1", "oneos.media.published=1"], self.props())
        # Share on but nothing could be held (One UI's storage not reachable): off, and no note.
        self.note.unlink()
        result = self.boot(env={"ONEOS_SHARE_FILES": "1"})
        self.assertIn("[Pivoted Root] Media hold: off (the media prototype does not run this boot)\n", result.stdout)
        self.assertEqual(["oneos.media.hold=0", "oneos.media.published=0"], self.props())
        self.assertFalse(self.note.exists())
        # Neither on.
        self.opt_in.rmdir()
        result = self.boot()
        self.assertEqual(["oneos.media.hold=0", "oneos.media.published=0"], self.props())
        self.assertFalse(self.note.exists())

    def test_released_after_the_binds_and_before_the_rescan(self):
        lines = self.binds(note="1")
        self.assertEqual(["oneos.media.published=1"], self.props())
        self.assertRegex(lines[0], r"^\d\d:\d\d:\d\d shared from One UI after 0s: DCIM Pictures$")
        self.assertRegex(lines[1], r"^\d\d:\d\d:\d\d Media hold: released \(after the binds\)$")
        self.assertRegex(lines[2], r"media rescan asked$")

    def test_released_when_the_guests_storage_never_came_up(self):
        lines = self.binds(storage_up=False, note="1")
        self.assertEqual(["oneos.media.published=1"], self.props())
        self.assertEqual(2, len(lines), lines)
        self.assertRegex(lines[0], r"never came up; nothing shared$")
        self.assertRegex(lines[1], r"^\d\d:\d\d:\d\d Media hold: released \(the guest's storage never came up\)$")

    def test_no_release_without_a_hold(self):
        # The launch's note says 0 (knob or failed setprop), or never came: nothing to release.
        for note in ("0", None):
            lines = self.binds(note=note)
            self.assertEqual([], self.props(), note)
            self.assertFalse(any("Media hold" in l for l in lines), lines)
            lines = self.binds(storage_up=False, note=note)
            self.assertEqual([], self.props(), note)
            self.assertFalse(any("Media hold" in l for l in lines), lines)

    def test_a_failing_setprop_is_logged_and_the_rest_goes_on(self):
        self.script(self.work / "fakebin/setprop", "#!/bin/sh\nexit 1\n")
        lines = self.binds(note="1")
        self.assertRegex(lines[1], r"Media hold: setprop oneos.media.published 1 failed \(after the binds\); "
                                   r"StorageManagerService waits out its 15 s$")
        self.assertRegex(lines[2], r"media rescan asked$")

    def test_last_boots_note_is_removed_before_the_background_part_starts(self):
        start = START.index("if [ -n \"$SHARE_HELD\" ]; then\n    # Last boot's note")
        block = START[start:start + 400]
        self.assertLess(block.index("rm -f /data/oneos_share_hold_set"), block.index("    ("))
        # And the launch writes the note after it sets the hold, never before.
        self.assertLess(START.index("oneos_media_share_hold=1"), START.index("> /data/oneos_share_hold_set"))
        self.assertLess(start, START.index("oneos_media_share_hold=1"))


class GuestMediaShareOrder(unittest.TestCase):
    """Where the pieces sit in guest_start.sh."""

    def test_stage_before_detach_publish_only_in_the_background_after_storage(self):
        lines = START.split("\n")
        def at(text):
            found = [i for i, l in enumerate(lines) if l.strip() == text]
            self.assertEqual(1, len(found), text)
            return found[0]
        stage = at('if [ "$oneos_media_enabled" = 1 ] && ! oneos_media_stage; then')
        detach = at("$BB umount -l /postinstall 2>/dev/null")
        chown = at('chown -R 1023:1023 "$d" 2>/dev/null || true')
        chmod = at('chmod -R 777 "$d" 2>/dev/null || true')
        launch = at('( oneos_media_late; oneos_media_hold_done "oneos_media_late ended without releasing it" ) </dev/null &')
        zygote = at('echo "[Pivoted Root] time $($BB date +%T) zygote"')
        self.assertLess(stage, detach)
        self.assertLess(detach, chown)
        self.assertLess(chmod, launch)
        self.assertLess(launch, zygote)
        # Nothing between the stage and the launch binds an album or calls the publish.
        between = "\n".join(lines[stage + 5:launch])
        self.assertNotIn("oneos_media_late\n", between.replace("oneos_media_late() {", ""))
        calls = [i for i, l in enumerate(lines) if re.search(r"\boneos_media_publish_album \"", l)]
        late = at("oneos_media_late() {")
        self.assertTrue(calls and all(late < i < launch for i in calls), calls)
        # The row watch starts once the views are ready, before any album is listed or published,
        # in the background and from /, and nothing is awaited for it.
        watch = at("( cd / 2>/dev/null; oneos_media_watch ) </dev/null &")
        ready = at('if [ "$oneos_media_ready" != 1 ]; then')
        listed = at("oneos_media_sum=$(oneos_media_albums)")
        self.assertLess(ready, watch)
        self.assertLess(watch, listed)
        self.assertNotIn("oneos-media-share-test\n", START)
        self.assertNotIn("/data/local/tmp/oneos-media-share-test", START)

    def test_album_rule_matches_the_root_scripts(self):
        # The app's picker (oneos_root.sh) and the guest must call the same directories albums.
        guest = re.findall(r'^oneos_media_ext="([^"]*)"$', START, re.M)
        app = re.findall(r'^OMEDIA_EXT="([^"]*)"$', ROOT_SCRIPT, re.M)
        self.assertEqual(1, len(guest))
        self.assertEqual(guest, app)
        self.assertEqual("jpg jpeg png gif webp heic heif avif bmp dng mp4 m4v 3gp 3g2 mkv webm mov avi ts", guest[0])
        self.assertIn('oneos_media_xname=local/tmp/oneos-oneui-media-excluded', START)
        self.assertIn('"$OLD_PARENT/oneos-oneui-media-excluded"', ROOT_SCRIPT)


if __name__ == "__main__":
    unittest.main()
