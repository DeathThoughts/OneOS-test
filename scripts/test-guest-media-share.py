#!/usr/bin/env python3
"""Exercise the One UI media share's boot shell with simulated mount operations.

The gate (before the pivot), the stage (pins One UI's albums before
`umount -l /postinstall`) and the late publish (started in the background
next to the zygote, after the guest's vold has built its storage views) are
cut out of guest_start.sh by their markers and run with a fake busybox. The
fake keeps a mount table, writes it in the real mountinfo layout, and plays
the guest's vold: after a few of the late loop's one-second sleeps it adds
vold's views of the guest's /data/media (S25 Ultra session 9a5159cc76ee3748:
the early binds were replaced by vold's, so apps never saw the album).

MEDIA_TEST_SH picks the shell (default sh); run the suite under dash,
busybox sh and mksh where they are installed.
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
START = (ROOT / "switcher/assets/guest_start.sh").read_text()
FLAGS = "ro,nosuid,nodev,noexec,noatime,nosymfollow"
SHELL = shlex.split(os.environ.get("MEDIA_TEST_SH", "sh"))
DATA_ROOT = "/local/tmp/oneos/data"
SECRETS = ("SECRET_IMG_0001.jpg", "SECRET_VID_0002.mp4", "SECRET_SHOT_0003.png", "GUEST_OWN_0004.jpg")
MP_PKG = "com.android.providers.media.module"

GATE = START[START.index("cd /data/local/tmp/oneos/rootfs\n"):START.index("if ! $BB pivot_root")]
HOLD = START[START.index("# One UI's camera and screenshots in ColorOS (owner's decision"):
             START.index("# OneOS: pivot_root left")]
LAUNCH = START[START.index("# One UI's camera and screenshots (S25 Ultra session 9a5159cc76ee3748): the\n"):
               START.index('echo "[Pivoted Root] time $($BB date +%T) zygote"')]

# The guest code still reads the real mountinfo record layout. This fixture
# keeps the mounts (in order, so stacking shows), controls the flags each bind
# gets and the unmount failures, and adds vold's views after VOLD_AT sleeps.
BUSYBOX = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import sys

root = Path(os.environ["MEDIA_TEST_ROOT"])
p = root / "state"
state = json.loads(p.read_text())
mountinfo = root / "mountinfo"
mode = os.environ["MEDIA_TEST_MODE"].split(",")
flags = os.environ["MEDIA_TEST_FLAGS"]
aliases = [a for a in os.environ["MEDIA_TEST_ALIASES"].split(":") if a]
media0 = str(root / "data/media/0")
cmd, *args = sys.argv[1:]

def save():
    p.write_text(json.dumps(state))
    lines = ["1 0 0:1 / / rw - tmpfs tmpfs rw",
             f"2 1 0:50 {os.environ['MEDIA_TEST_DATA_ROOT']} {root}/data rw,nosuid,nodev,noatime - f2fs /dev/block/dm-50 rw"]
    for i, m in enumerate(state["mounts"], 3):
        lines.append(f"{i} 1 {m['dev']} {m['root']} {m['path']} {m['opts']} - {m['fs']} src rw")
    mountinfo.write_text("\n".join(lines) + "\n")

def top(path):
    found = [m for m in state["mounts"] if m["path"] == path]
    return found[-1] if found else None

def real(path):
    m = top(path)
    if m and m.get("src"):
        return m["src"]
    for a in aliases:
        if path == a or path.startswith(a + "/"):
            return media0 + path[len(a):]
    return path

def peer(dest):
    # "propagate": the runtime view is a peer of pass_through.
    a, b = str(root / "mnt/pass_through/0/emulated/0/"), str(root / "mnt/runtime/default/emulated/0/")
    return b + dest[len(a):] if "propagate" in mode and dest.startswith(a) else None

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
                                "ino": "", "src": "", "dev": dev, "root": root_field, "fs": fs})

if cmd == "readlink":
    print("mnt:[2]" if args[0].startswith("/proc/self/") and "same-ns" not in mode else "mnt:[1]")
elif cmd == "stat":
    fmt, path = args[1], args[-1]
    m = top(path)
    if fmt == "%u:%a":
        print(f"0:{Path(path).stat().st_mode & 0o777:o}")
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
elif cmd == "find":
    os.execvp("find", ["find", real(args[0])] + args[1:])
elif cmd == "sleep":
    state["sleeps"] += 1
    if os.environ.get("MEDIA_TEST_VOLD_AT") and state["sleeps"] == int(os.environ["MEDIA_TEST_VOLD_AT"]):
        vold()
    save()
elif cmd == "pidof":
    sys.exit(1)
elif cmd == "mount":
    if args[0] == "--make-rprivate":
        sys.exit("not-private" in mode)
    dest = args[-1]
    if args[0] == "-o":
        if "remount-fails" in mode:
            print(f"mount: mounting {args[-2]} on {dest} failed: Invalid argument", file=sys.stderr)
            print("second line", file=sys.stderr)
            sys.exit(1)
        top(dest)["opts"] = flags.removesuffix(",nosymfollow") if "lack-nosymfollow" in mode else flags
    elif args[-2] == str(root / "postinstall/data"):
        shutil.copytree(root / "original", dest, dirs_exist_ok=True, symlinks=True)
        state["mounts"].append({"path": dest, "opts": "rw", "ino": "0:999", "src": "",
                                "dev": "0:60", "root": "/", "fs": "f2fs"})
    elif args[-2] == "/proc/self/cwd":
        cwd = Path.cwd()
        st = cwd.stat()
        # The hold's parent view goes away; keep what the pinned leaf shows.
        stash = root / "stash" / str(len(state["mounts"]))
        shutil.copytree(cwd, stash, symlinks=True)
        state["mounts"].append({"path": dest, "opts": flags, "ino": f"{st.st_dev}:{st.st_ino}",
                                "src": str(stash), "dev": "0:60",
                                "root": "/media/0/" + str(cwd).split("/media/0/", 1)[1], "fs": "f2fs"})
    else:
        clone = dict(top(args[-2]), path=dest)
        if "bad-alias" in mode and "/mnt/runtime/" in dest and "OneUI_Camera" in dest:
            clone["opts"] = "rw,nosuid"
        state["mounts"].append(clone)
        other = peer(dest)
        if other and not top(other):
            state["mounts"].append(dict(clone, path=other))
    save()
elif cmd == "umount":
    dest = args[-1]
    if "busy-parent" in mode and dest.endswith("/host"):
        sys.exit(1)
    if "stuck-alias" in mode and "/mnt/runtime/" in dest:
        sys.exit(1)
    m = top(dest)
    if m is None:
        sys.exit(1)
    state["mounts"].remove(m)
    other = peer(dest)
    if other and top(other):
        state["mounts"].remove(top(other))
    if dest.endswith("/host"):
        shutil.rmtree(Path(dest) / "media")
    save()
else:
    os.execvp(cmd, [cmd] + args)
"""


class GuestMediaShareTest(unittest.TestCase):
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
        self.vold_bases = ("mnt/pass_through/0/emulated/0", "mnt/runtime/default/emulated/0")
        self.early_bases = ("storage/emulated/0", "mnt/user/0/emulated")
        for path in ("data/local/tmp/oneos/rootfs", "postinstall/data", "data/media/0/DCIM",
                     "data/media/0/Pictures", "original/media/0/DCIM/Camera",
                     "original/media/0/DCIM/Screenshots", "props", "system/bin", "fakebin",
                     *(b + "/DCIM" for b in self.vold_bases + self.early_bases),
                     *(b + "/Pictures" for b in self.vold_bases + self.early_bases)):
            (w / path).mkdir(parents=True, exist_ok=True)
        (w / "original/media/0/DCIM/Camera" / SECRETS[0]).write_text("a")
        (w / "original/media/0/DCIM/Camera" / SECRETS[1]).write_text("b")
        (w / "original/media/0/DCIM/Screenshots" / SECRETS[2]).write_text("c")
        (self.stage / ".oneos_stage").write_text('  "image": "coloros17-findx9u-in-1105",\n')
        self.opt_in.mkdir(mode=0o700)
        (w / "props/sys.boot_completed").write_text("1")
        self.script(w / "bb", BUSYBOX)
        self.script(w / "fakebin/getprop", '#!/bin/sh\ncat "$MEDIA_TEST_ROOT/props/$1" 2>/dev/null\n')
        self.script(w / "fakebin/setprop", '#!/bin/sh\nprintf %s "$2" > "$MEDIA_TEST_ROOT/props/$1"\n')
        self.script(w / "fakebin/pidof", "#!/bin/sh\nexit 1\n")
        self.script(w / "system/bin/sh", f'#!/bin/sh\nexec {" ".join(SHELL)} "$@"\n')
        self.script(w / "data/oneos_genv.sh", '#!/bin/sh\nexec "$@"\n')
        self.script(w / "system/bin/am", '#!/bin/sh\necho "$*" >> "$MEDIA_TEST_ROOT/am.log"\n'
                    'echo "Broadcasting: Intent { act=$4 dat=$6 }"\necho "Broadcast completed: result=0"\n')
        self.script(w / "system/bin/content", '#!/bin/sh\necho "$*" >> "$MEDIA_TEST_ROOT/content.log"\n'
                    'echo "Row: 0 _id=11"\necho "Row: 1 _id=12"\n')
        self.proc(4242, MP_PKG, "Uid:\t10077\t10077\t10077\t10077\nGid:\t10077\t10077\t10077\t10077\n"
                  "Groups:\t1023 1078 9997 20077 50077\n",
                  ["7 1 0:50 /local/tmp/oneos/data/media / rw - f2fs x rw",
                   "8 1 0:60 /media/0/DCIM/Camera /storage/emulated/0/DCIM/OneUI_Camera "
                   + FLAGS + " master:9 - f2fs y rw",
                   "9 1 0:3 / /apex ro - tmpfs tmpfs rw"])
        self.proc(5151, "com.example.app", "Name:\tapp\nUid:\t10234\t10234\t10234\t10234\n",
                  [f"{n} 1 0:70 / /mnt/user/0/emulated/{n} rw - fuse fuse rw" for n in range(25)])
        self.proc(1, "/system/bin/init", "Uid:\t0\t0\t0\t0\n", [])

    def script(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    def proc(self, pid, name, status, mountinfo):
        d = self.work / "proc" / str(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(name.encode() + b"\0")
        (d / "status").write_text(status)
        (d / "mountinfo").write_text("".join(l + "\n" for l in mountinfo))

    def mounts(self):
        return json.loads(self.state.read_text())["mounts"]

    def early(self):
        # guest_start.sh's own binds below "Setting up /data/media".
        st = json.loads(self.state.read_text())
        for b in self.early_bases:
            st["mounts"].append({"path": str(self.work / b), "opts": "rw", "ino": "", "src": "",
                                 "dev": "0:50", "root": DATA_ROOT + "/media/0", "fs": "f2fs", "early": True})
        self.state.write_text(json.dumps(st))

    def boot(self, mode="", vold_at=3):
        st = json.loads(self.state.read_text())
        st["sleeps"] = 0
        self.state.write_text(json.dumps(st))
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
                 "MEDIA_TEST_MODE": mode, "MEDIA_TEST_FLAGS": FLAGS,
                 "MEDIA_TEST_ALIASES": aliases, "MEDIA_TEST_DATA_ROOT": DATA_ROOT,
                 "MEDIA_TEST_VOLD_AT": str(vold_at or ""),
                 "PATH": str(self.work / "fakebin") + ":" + os.environ["PATH"]},
            capture_output=True, text=True, timeout=120,
        )
        for secret in SECRETS:
            self.assertNotIn(secret, result.stdout + result.stderr)
        for line in result.stdout.splitlines():
            self.assertFalse(line.startswith("[Pivoted Root] time "), line)
        return result

    def lines(self, result):
        return [l for l in result.stdout.splitlines() if "Media prototype" in l]

    def targets(self, rel):
        return {str(self.work / b / rel) for b in self.vold_bases}

    def assert_released(self):
        self.assertEqual([], list((self.work / "data").glob("oneos_media_hold.*")))
        self.assertEqual([], [m["path"] for m in self.mounts() if "oneos_media_hold" in m["path"]])

    def album_mounts(self):
        return {m["path"]: m for m in self.mounts() if "OneUI_" in m["path"]}

    # --- the gate ---------------------------------------------------------

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
            self.assertEqual([f"[Pivoted Root] Media prototype off: {reason}"],
                             result.stdout.splitlines())
        self.opt_in.rmdir()
        self.opt_in.write_text("")
        result = self.boot()
        self.assertEqual([f"[Pivoted Root] Media prototype off: opt-in marker {self.opt_in} is not a directory"],
                         self.lines(result))

    # --- publishing after vold ------------------------------------------

    def test_both_albums_on_every_vold_alias_with_full_flags(self):
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout
        self.assertIn("[Pivoted Root] Media prototype: pinned read-only, published once the guest's vold "
                      "has built its storage: camera:DCIM/Camera:DCIM/OneUI_Camera "
                      "screenshots:DCIM/Screenshots:DCIM/OneUI_Screenshots\n", out)
        bases = " ".join(str(self.work / b) for b in self.vold_bases)
        self.assertRegex(out, r"Media prototype: the guest's vold built its storage views at "
                         r"\d\d:\d\d:\d\d \(\d+ s after the zygote launch\), settled at \d\d:\d\d:\d\d: "
                         + re.escape(bases) + "\n")
        for name, src, dst in (("camera", "DCIM/Camera", "DCIM/OneUI_Camera"),
                               ("screenshots", "DCIM/Screenshots", "DCIM/OneUI_Screenshots")):
            self.assertIn(f"Media prototype: {name} (One UI's {src}) mounted read-only as {dst} "
                          f"(2 aliases: {' '.join(sorted(self.targets(dst)))})\n", out)
        albums = self.album_mounts()
        self.assertEqual(self.targets("DCIM/OneUI_Camera") | self.targets("DCIM/OneUI_Screenshots"),
                         set(albums))
        self.assertEqual({FLAGS}, {m["opts"] for m in albums.values()})
        # FUSE, Android/data and the hold are no alias bases.
        self.assertFalse(any("/mnt/user/" in p or "Android" in p for p in albums))
        self.assertTrue((self.media0 / "DCIM/OneUI_Camera").is_dir())
        self.assertEqual(0o777, (self.media0 / "DCIM/OneUI_Camera").stat().st_mode & 0o777)
        self.assertIn("[Pivoted Root] Media prototype: hold released\n", out)
        self.assert_released()
        # Diagnostics: counts and modes, MediaProvider's ids and the views.
        self.assertRegex(out, r"Media prototype: DCIM/OneUI_Camera is \d+ \d+ \d+ \(mode uid gid\), "
                         r"2 regular file\(s\), one is \d+ \d+ \d+\n")
        self.assertIn("1 regular file(s)", out)
        self.assertIn(f"Media prototype: MediaProvider {MP_PKG} pid 4242: Uid: 10077 10077 10077 10077; "
                      "Gid: 10077 10077 10077 10077; Groups: 1023 1078 9997 20077 50077\n", out)
        self.assertIn("Media prototype view MediaProvider pid 4242: 0:60 /media/0/DCIM/Camera "
                      f"/storage/emulated/0/DCIM/OneUI_Camera {FLAGS} f2fs\n", out)
        self.assertNotIn("/apex", out)
        app = [l for l in out.splitlines() if "view app uid 10234 pid 5151:" in l]
        self.assertEqual(21, len(app), app)
        self.assertTrue(app[-1].endswith("... and 5 more"))
        # One scan request per album, to MediaProvider's package, then a count.
        am = (self.work / "am.log").read_text().splitlines()
        self.assertEqual([f"broadcast -p {MP_PKG} -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
                          f"-d file:///storage/emulated/0/{d}" for d in ("DCIM/OneUI_Camera", "DCIM/OneUI_Screenshots")], am)
        self.assertRegex(out, r"Media prototype: scan of DCIM/OneUI_Camera asked \d+ s after the zygote "
                         r"launch \(am status 0, \d+ s\): Broadcast completed: result=0\n")
        self.assertIn("Media prototype: MediaStore lists 2 item(s) under DCIM/OneUI_Screenshots/ "
                      "30 s after the scan request\n", out)
        self.assertIn("relative_path LIKE 'DCIM/OneUI_Camera/%'", (self.work / "content.log").read_text())

    def test_pictures_screenshots_when_one_ui_has_no_dcim_screenshots(self):
        shots = self.work / "original/media/0/DCIM/Screenshots"
        (shots / SECRETS[2]).unlink()
        shots.rmdir()
        (self.work / "original/media/0/Pictures/Screenshots").mkdir(parents=True)
        (self.work / "original/media/0/Pictures/Screenshots" / SECRETS[2]).write_text("c")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Media prototype: screenshots (One UI's Pictures/Screenshots) mounted read-only as "
                      "Pictures/OneUI_Screenshots (2 aliases: ", result.stdout)
        self.assertEqual(self.targets("DCIM/OneUI_Camera") | self.targets("Pictures/OneUI_Screenshots"),
                         set(self.album_mounts()))
        self.assertFalse((self.media0 / "DCIM/OneUI_Screenshots").exists())

    def test_one_album_missing_in_one_ui_the_other_is_shared(self):
        cam = self.work / "original/media/0/DCIM/Camera"
        for f in cam.iterdir():
            f.unlink()
        cam.rmdir()
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("[Pivoted Root] Media prototype skipped camera: source missing or protected mount "
                      "unavailable: album media/0/DCIM/Camera in One UI is missing or reachable only "
                      "through a symlink\n", result.stdout)
        self.assertEqual(self.targets("DCIM/OneUI_Screenshots"), set(self.album_mounts()))
        self.assertFalse((self.media0 / "DCIM/OneUI_Camera").exists())
        self.assert_released()

    def test_symlinked_source_is_skipped_and_no_album_means_no_share(self):
        cam = self.work / "original/media/0/DCIM/Camera"
        for f in cam.iterdir():
            f.unlink()
        cam.rmdir()
        cam.symlink_to(self.work / "data/media", target_is_directory=True)
        shots = self.work / "original/media/0/DCIM/Screenshots"
        (shots / SECRETS[2]).unlink()
        shots.rmdir()
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([
            "[Pivoted Root] Media prototype skipped camera: source missing or protected mount unavailable: "
            "album media/0/DCIM/Camera in One UI is a symlink",
            "[Pivoted Root] Media prototype skipped screenshots: source missing or protected mount unavailable: "
            "album media/0/Pictures/Screenshots in One UI is missing or reachable only through a symlink",
            "[Pivoted Root] Media prototype skipped: source missing or protected mount unavailable: "
            "none of One UI's albums could be pinned (reasons above)"], self.lines(result))
        self.assertEqual([], self.mounts())
        self.assert_released()

    def test_nonempty_guest_target_skips_that_album_only(self):
        own = self.media0 / "DCIM/OneUI_Camera"
        own.mkdir()
        (own / SECRETS[3]).write_text("guest")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("[Pivoted Root] Media prototype skipped camera: guest storage aliases could not be "
                      f"protected: the guest's own {own} exists and is not empty\n", result.stdout)
        self.assertNotIn("rolled back camera", result.stdout)
        self.assertEqual(self.targets("DCIM/OneUI_Screenshots"), set(self.album_mounts()))
        self.assertEqual("guest", (own / SECRETS[3]).read_text())
        self.assertEqual(1, len((self.work / "am.log").read_text().splitlines()))

    def test_alias_flag_failure_rolls_that_album_back(self):
        result = self.boot("bad-alias")
        self.assertEqual(0, result.returncode, result.stderr)
        alias = self.work / "mnt/runtime/default/emulated/0/DCIM/OneUI_Camera"
        self.assertIn("[Pivoted Root] Media prototype skipped camera: guest storage aliases could not be "
                      f"protected: alias lacks wanted flags: {alias} has options rw,nosuid, missing "
                      "ro,nodev,noexec,noatime,nosymfollow\n", result.stdout)
        self.assertIn("[Pivoted Root] Media prototype: rolled back camera: 2 alias mount(s) detached "
                      f"(0 with umount -l), 0 left; mount point {self.media0}/DCIM/OneUI_Camera removed\n",
                      result.stdout)
        self.assertFalse((self.media0 / "DCIM/OneUI_Camera").exists())
        self.assertEqual(self.targets("DCIM/OneUI_Screenshots"), set(self.album_mounts()))
        self.assertNotIn("FATAL", result.stdout)
        self.assert_released()

    def test_alias_that_cannot_be_detached_asks_for_one_ui(self):
        result = self.boot("bad-alias,stuck-alias")
        self.assertEqual(0, result.returncode, result.stderr)
        alias = self.work / "mnt/runtime/default/emulated/0/DCIM/OneUI_Camera"
        out = result.stdout
        self.assertIn(f"[Pivoted Root] Media prototype FATAL: could not detach {alias} even with umount -l; "
                      "asking the host to return to One UI\n", out)
        self.assertLess(out.index("skipped camera"), out.index("FATAL"))
        self.assertEqual("media:unprotected-mount", (self.work / "props/oneos.guest.powerctl").read_text())
        self.assertIn("rolled back camera: 1 alias mount(s) detached (0 with umount -l), 1 left; "
                      f"mount point {self.media0}/DCIM/OneUI_Camera kept\n", out)
        self.assertTrue((self.media0 / "DCIM/OneUI_Camera").is_dir())
        # A return already asked for is not replaced.
        (self.work / "props/oneos.guest.powerctl").write_text("app:app")
        st = json.loads(self.state.read_text())
        st["mounts"] = [m for m in st["mounts"] if "OneUI_" not in m["path"]]
        self.state.write_text(json.dumps(st))
        (self.media0 / "DCIM/OneUI_Camera").rmdir()
        self.boot("bad-alias,stuck-alias")
        self.assertEqual("app:app", (self.work / "props/oneos.guest.powerctl").read_text())

    def test_propagated_alias_is_not_bound_twice(self):
        result = self.boot("propagate")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("camera (One UI's DCIM/Camera) mounted read-only as DCIM/OneUI_Camera (2 aliases: ",
                      result.stdout)
        paths = [m["path"] for m in self.mounts() if "OneUI_" in m["path"]]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(4, len(paths))

    def test_no_vold_view_times_out_and_releases_the_hold(self):
        result = self.boot(vold_at=0)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("[Pivoted Root] Media prototype skipped: the guest's vold built no storage view of "
                      f"{self.work}/data/media in 180 s (seen: none)\n",
                      result.stdout)
        self.assertEqual(180, json.loads(self.state.read_text())["sleeps"])
        self.assertEqual([], self.mounts())
        self.assertFalse((self.media0 / "DCIM/OneUI_Camera").exists())
        self.assertFalse((self.work / "am.log").exists())
        self.assert_released()

    def test_guest_starts_own_early_binds_are_no_sign_of_vold(self):
        self.early()
        result = self.boot(vold_at=0)
        self.assertEqual(0, result.returncode, result.stderr)
        seen = " ".join(sorted(str(self.work / b) for b in self.early_bases))
        self.assertIn(f"the guest's vold built no storage view of {self.work}/data/media in 180 s (seen: "
                      f"{seen})\n", result.stdout)
        self.assertEqual({}, self.album_mounts())
        self.assert_released()
        # Once vold replaced them, only vold's views are used.
        self.early()
        result = self.boot(vold_at=2)
        self.assertEqual(self.targets("DCIM/OneUI_Camera") | self.targets("DCIM/OneUI_Screenshots"),
                         set(self.album_mounts()))

    def test_leftover_mount_points_and_holds_are_removed_with_rmdir_only(self):
        data = self.work / "data"
        for name, subs in (("aaaaaa", ("host", "album")), ("bbbbbb", ("album_camera", "album_screenshots", "host"))):
            for sub in subs:
                (data / f"oneos_media_hold.{name}" / sub).mkdir(parents=True)
        kept = data / "oneos_media_hold.cccccc"
        (kept / "album_camera").mkdir(parents=True)
        (kept / "album_camera/keep").write_text("x")
        # A symlinked leftover is not followed: its empty target stays.
        target = self.work / "elsewhere"
        (target / "album_camera").mkdir(parents=True)
        (data / "oneos_media_hold.dddddd").symlink_to(target, target_is_directory=True)
        (self.media0 / "DCIM/OneUI_Camera").mkdir()
        (self.media0 / "Pictures/OneUI_Screenshots").mkdir()
        full = self.media0 / "DCIM/OneUI_Screenshots"
        full.mkdir()
        (full / SECRETS[3]).write_text("guest")
        (self.media0 / "DCIM/OneOSShareTest").mkdir()
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout
        self.assertIn("[Pivoted Root] Media prototype: removed 2 empty hold dir(s) left by an earlier boot\n", out)
        self.assertIn("[Pivoted Root] Media prototype: removed 2 empty mount-point dir(s) left by an earlier boot\n", out)
        self.assertEqual(sorted([kept, data / "oneos_media_hold.dddddd"]),
                         sorted(data.glob("oneos_media_hold.*")))
        self.assertEqual("x", (kept / "album_camera/keep").read_text())
        self.assertTrue((target / "album_camera").is_dir())
        self.assertTrue((self.media0 / "DCIM/OneOSShareTest").is_dir())
        self.assertFalse((self.media0 / "Pictures/OneUI_Screenshots").exists())
        self.assertEqual("guest", (full / SECRETS[3]).read_text())
        self.assertIn("skipped screenshots: guest storage aliases could not be protected: "
                      f"the guest's own {full} exists and is not empty\n", out)
        self.assertEqual(self.targets("DCIM/OneUI_Camera"), set(self.album_mounts()))

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

    def test_parent_hold_unmount_failure_stops_boot(self):
        result = self.boot("busy-parent")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Not starting the guest", result.stdout)
        self.assertRegex(result.stdout, r"could not detach a mount \(\S+/host\)")
        self.assertEqual({}, self.album_mounts())


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
        chown = at("chown -R 1023:1023 /data/media 2>/dev/null || true")
        chmod = at("chmod -R 777 /data/media 2>/dev/null || true")
        launch = at("( oneos_media_late ) </dev/null &")
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
        self.assertNotIn("oneos-media-share-test\n", START)
        self.assertNotIn("/data/local/tmp/oneos-media-share-test", START)


if __name__ == "__main__":
    unittest.main()
