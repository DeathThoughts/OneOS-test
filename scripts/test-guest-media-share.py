#!/usr/bin/env python3
"""Exercise the media prototype's boot shell with simulated mount operations."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
START = (ROOT / "switcher/assets/guest_start.sh").read_text()
FLAGS = "ro,nosuid,nodev,noexec,noatime,nosymfollow"
SUFFIX = "DCIM/OneOSShareTest"

# The guest code still reads the real mountinfo record layout. This fixture
# controls the flags inherited by each bind and any unmount failures.
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
mode = os.environ["MEDIA_TEST_MODE"]
flags = os.environ["MEDIA_TEST_FLAGS"]
cmd, *args = sys.argv[1:]

def save():
    p.write_text(json.dumps(state))
    content = ["1 0 0:1 / / rw - tmpfs tmpfs rw"]
    for i, (path, (opts, _)) in enumerate(state.items(), 2):
        content.append(f"{i} 1 0:1 /test {path} {opts} - tmpfs tmpfs rw")
    mountinfo.write_text("\n".join(content) + "\n")

if cmd == "readlink":
    print("mnt:[2]" if args[0].startswith("/proc/self/") and mode != "same-ns" else "mnt:[1]")
elif cmd == "stat":
    path = args[-1]
    if args[1] == "%u:%a":
        print(f"0:{Path(path).stat().st_mode & 0o777:o}")
    elif path in state:
        print(state[path][1])
    elif path in (str(root / "storage/emulated/0"),
                  str(root / "mnt/user/0/emulated"),
                  str(root / "mnt/installer/0/emulated"),
                  str(root / "mnt/androidwritable/0/emulated")):
        st = (root / "data/media/0").stat()
        print(f"{st.st_dev}:{st.st_ino}")
    else:
        os.execvp(cmd, [cmd] + args)
elif cmd == "mount":
    if args[0] == "--make-rprivate":
        sys.exit(mode == "not-private")
    dest = args[-1]
    if args[0] == "-o":
        if mode == "remount-fails":
            print(f"mount: mounting {args[-2]} on {dest} failed: Invalid argument", file=sys.stderr)
            print("second line", file=sys.stderr)
            sys.exit(1)
        state[dest][0] = flags.removesuffix(",nosymfollow") if mode == "lack-nosymfollow" else flags
    elif args[-2] == str(root / "postinstall/data"):
        shutil.copytree(root / "original", dest, dirs_exist_ok=True, symlinks=True)
        state[dest] = ["rw", "0:999"]
    elif args[-2] == "/proc/self/cwd":
        st = Path.cwd().stat()
        state[dest] = [flags, f"{st.st_dev}:{st.st_ino}"]
    else:
        state[dest] = list(state[args[-2]])
        if mode == "bad-alias" and "/mnt/user/" in dest:
            state[dest][0] = "rw,nosuid"
    save()
elif cmd == "umount":
    dest = args[-1]
    if mode == "busy-parent" and dest.endswith("/host"):
        sys.exit(1)
    del state[dest]
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
        self.work = Path(tmp.name)
        self.mountinfo = self.work / "mountinfo"
        self.mountinfo.write_text("1 0 0:1 / / rw - tmpfs tmpfs rw\n")
        self.state = self.work / "state"
        self.state.write_text("{}")
        self.stage = self.work / "data/local/tmp/oneos"
        self.opt_in = self.stage.parent / "oneos-media-share-test"
        self.roots = (
            "data/media/0", "storage/emulated/0", "mnt/user/0/emulated",
            "mnt/installer/0/emulated", "mnt/androidwritable/0/emulated",
        )
        for path in ("original/media/0/" + SUFFIX, "data/local/tmp/oneos/rootfs",
                     "postinstall/data", *(r + "/DCIM" for r in self.roots)):
            (self.work / path).mkdir(parents=True)
        (self.stage / ".oneos_stage").write_text('  "image": "coloros17-findx9u-in-1105",\n')
        self.opt_in.mkdir(mode=0o700)
        bb = self.work / "bb"
        bb.write_text(BUSYBOX)
        bb.chmod(0o700)
        self.bb = bb

    def boot(self, mode=""):
        gate = START[START.index("cd /data/local/tmp/oneos/rootfs\n"):
                     START.index('if ! $BB pivot_root')]
        hold = START[START.index("oneos_media_tmp=\n"):
                     START.index("# OneOS: pivot_root left")]
        publish = START[START.index("oneos_media_publish()"):
                        START.index('echo "[Pivoted Root] Rootfs successfully')]
        shell = gate + hold + publish
        shell = shell.replace("/proc/self/mountinfo", str(self.mountinfo))
        shell = re.sub(r"""(?<![\w/.])/(data|postinstall|mnt|storage)(?=[/\s'"])""",
                       lambda m: str(self.work / m[1]), shell)
        return subprocess.run(
            ["sh", "-c", shell],
            env={**os.environ, "BB": str(self.bb), "MEDIA_TEST_ROOT": str(self.work),
                 "MEDIA_TEST_MODE": mode, "MEDIA_TEST_FLAGS": FLAGS},
            capture_output=True, text=True, timeout=30,
        )

    def test_publish_only_protected_album_aliases(self):
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("mounted read-only (5 aliases: " + " ".join(
            str(self.work / r) for r in self.roots) + ")\n", result.stdout)
        self.assertNotIn("removed", result.stdout)
        records = self.mountinfo.read_text().splitlines()[1:]
        self.assertEqual({str(self.work / path / SUFFIX) for path in self.roots},
                         {x.split()[4] for x in records})
        self.assertEqual({FLAGS}, {x.split()[5] for x in records})
        self.assertEqual([], list((self.work / "data").glob("oneos_media_hold.*")))

    def test_disabled_or_wrong_guest(self):
        self.opt_in.rmdir()
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("{}", self.state.read_text())
        # Without the opt-in marker the prototype stays silent.
        self.assertNotIn("Media prototype", result.stdout)
        self.opt_in.mkdir(mode=0o700)
        (self.stage / ".oneos_stage").write_text('  "image": "lineage24-test",\n')
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("{}", self.state.read_text())
        self.assertIn("[Pivoted Root] Media prototype off: image is not ColorOS", result.stdout)
        self.assertIn("'lineage24-test'", result.stdout)

    def test_off_reasons_name_the_gate_check(self):
        self.opt_in.chmod(0o755)
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("{}", self.state.read_text())
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
            self.assertEqual("{}", self.state.read_text())
            self.assertEqual([f"[Pivoted Root] Media prototype off: {reason}"],
                             result.stdout.splitlines())

    def test_source_symlink_is_not_published(self):
        source = self.work / "original/media/0" / SUFFIX
        source.rmdir()
        source.symlink_to(self.work / "data/media", target_is_directory=True)
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("prototype skipped", result.stdout)
        self.assertIn("protected mount unavailable: album media/0/" + SUFFIX
                      + " in One UI is a symlink", result.stdout)
        self.assertEqual("{}", self.state.read_text())

    def test_alias_failure_rolls_back_all_mounts(self):
        result = self.boot("bad-alias")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("aliases could not be protected", result.stdout)
        alias = self.work / "mnt/user/0/emulated" / SUFFIX
        self.assertIn(f"alias lacks wanted flags: {alias} has options rw,nosuid, missing "
                      "ro,nodev,noexec,noatime,nosymfollow", result.stdout)
        self.assertEqual("{}", self.state.read_text())

    def test_missing_kernel_flags_skip_share(self):
        result = self.boot("lack-nosymfollow")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("protected mount unavailable", result.stdout)
        self.assertRegex(result.stdout, r"hold mount lacks wanted flags: \S+/host has options "
                         r"ro,nosuid,nodev,noexec,noatime, missing nosymfollow\n")
        self.assertEqual("{}", self.state.read_text())

    def test_remount_error_text_is_logged(self):
        result = self.boot("remount-fails")
        self.assertEqual(0, result.returncode, result.stderr)
        lines = [l for l in result.stdout.splitlines() if "Media prototype" in l]
        self.assertEqual(1, len(lines), result.stdout)
        self.assertIn("protected mount unavailable: remount of the hold with " + FLAGS
                      + " failed: mount: mounting ", lines[0])
        self.assertTrue(lines[0].endswith("failed: Invalid argument"), lines[0])
        self.assertEqual("{}", self.state.read_text())
        self.assertEqual([], list((self.work / "data").glob("oneos_media_hold.*")))

    def test_existing_guest_album_is_never_covered(self):
        dest = self.work / self.roots[0] / SUFFIX
        dest.mkdir()
        (dest / "photo").write_text("local")
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("aliases could not be protected", result.stdout)
        self.assertIn(f"the guest's own {dest} exists and is not empty", result.stdout)
        self.assertNotIn("photo", result.stdout)
        self.assertEqual("{}", self.state.read_text())
        self.assertEqual("local", (dest / "photo").read_text())

    def test_leftover_empty_holds_are_removed(self):
        data = self.work / "data"
        for name in ("oneos_media_hold.aaaaaa", "oneos_media_hold.bbbbbb"):
            (data / name / "host").mkdir(parents=True)
            (data / name / "album").mkdir()
        kept = data / "oneos_media_hold.cccccc"
        (kept / "album").mkdir(parents=True)
        (kept / "album/keep").write_text("x")
        # A symlinked leftover is not followed: its empty target stays.
        target = self.work / "elsewhere"
        (target / "album").mkdir(parents=True)
        (data / "oneos_media_hold.dddddd").symlink_to(target, target_is_directory=True)
        result = self.boot()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("[Pivoted Root] Media prototype: removed 2 empty hold dir(s) left by an "
                      "earlier boot\n", result.stdout)
        self.assertIn("mounted read-only (5 aliases: ", result.stdout)
        self.assertEqual(sorted([kept, data / "oneos_media_hold.dddddd"]),
                         sorted(data.glob("oneos_media_hold.*")))
        self.assertEqual("x", (kept / "album/keep").read_text())
        self.assertTrue((target / "album").is_dir())

    def test_parent_hold_unmount_failure_stops_boot(self):
        result = self.boot("busy-parent")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Not starting the guest", result.stdout)
        self.assertRegex(result.stdout, r"could not detach a mount \(\S+/host\)")
        self.assertNotIn(SUFFIX, self.mountinfo.read_text())


if __name__ == "__main__":
    unittest.main()
