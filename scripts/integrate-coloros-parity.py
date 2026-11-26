#!/usr/bin/env python3
"""Integrate only verified candidates from the reviewed ColorOS parity rebuild.

Keep device-specific camera, Fold8, AI and theme payloads; no cross-ROM binary
copies. Publish new content-addressed assets, never clobber old assets.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REPO = "DeathThoughts/OneOS-test"
BRANCH = "refs/heads/experiment/coloros-patch-parity"
TAG = "coloros-parity-r1"
CHANNEL = "fork-fold8-experimental-v3"
REMOTE = f"gh:{REPO}:{TAG}"
P = ROOT / "app-android/packages"
FIXTURES = ROOT / "app-android/core/model/src/test/resources/packages"
M = runpy.run_path(str(ROOT / "scripts/migrate-patch-payloads.py"))
TARGETS = {"findn6": ("n6-f8u", "n6-global-f8"), "twinity": ("twinity-s23",), "pyb110": ("pyb110-s23",)}
GESTURE = ("OplusGestureUI_patched.apk", "OplusGestureUI_patched.apk.src_sha256")
WAVE = ("ColorPanoramicSystemUI.apk", "SystemUI_wave.dex", "OneOSWaveRepack.dex")
EXTRA = {"findn6": GESTURE, "twinity": WAVE + GESTURE, "pyb110": WAVE + GESTURE}
KEEP = {"OneOSGuest.apk", "OneOSWallpapersFoldGrid.apk", "Fold8SystemUI.apk",
        "Fold8AngleRelay.dex", "GlobalVerifiedAddons.tar", "KeKeThemeSpace_patched.apk",
        "oplus_odm_ai.tar", "samcam.tar", "samcam_bar.tar", "samcam_a.tar"}
SHA = re.compile(r"[a-f0-9]{64}")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")

def records(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, value = line.split(None, 1)
        if key in result:
            raise ValueError(f"Duplicate key in {path}: {key}")
        result[key] = value.strip()
    return result

def pins(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        sha, name = line.split(None, 1)
        name = name.strip()
        if not SHA.fullmatch(sha) or not NAME.fullmatch(name) or name in result:
            raise ValueError(f"Invalid pin in {path}: {line}")
        result[name] = sha
    return result

def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Expected one source anchor: {old}")
    return text.replace(old, new)

def load_candidate(root, image):
    report = json.loads((root / image / "report/candidate-inventory.json").read_text())
    if report["image"] != image or report["candidate_only"] is not True:
        raise ValueError("Wrong candidate provenance")
    files = {}
    for path in (root / image / "candidate").rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlink in candidate: {path}")
        if path.is_file():
            if path.name in files:
                raise ValueError(f"Duplicate candidate basename: {path.name}")
            files[path.name] = path
    if set(files) != set(report["files"]):
        raise ValueError(f"{image}: candidate contents differ from report")
    for name, path in files.items():
        rec = report["files"][name]
        if not NAME.fullmatch(name) or not 0 < rec["bytes"] <= 2 * 1024**3:
            raise ValueError(f"Invalid candidate entry: {name}")
        if path.stat().st_size != rec["bytes"] or digest(path) != rec["sha256"]:
            raise ValueError(f"Candidate hash or size differs: {name}")
    return files, report["files"]

def publish(tag, paths, target):
    M["ensure_release"](tag, target)
    current = M["assets"](tag) or {}
    for name, path in sorted(paths.items()):
        sha, size = digest(path), path.stat().st_size
        if name in current:
            if not M["matches"](current[name], sha, size):
                raise RuntimeError(f"Refusing to replace existing asset: {tag}/{name}")
        else:
            M["gh"]("release", "upload", tag, str(path))
    current = M["assets"](tag) or {}
    for name, path in paths.items():
        if not M["matches"](current.get(name), digest(path), path.stat().st_size):
            raise RuntimeError(f"Unverified published asset: {tag}/{name}")

def main():
    root, target = Path(sys.argv[1]), sys.argv[2]
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REPOSITORY") != REPO
        or os.environ.get("GITHUB_REF") != BRANCH or os.environ.get("GITHUB_SHA") != target
        or not re.fullmatch(r"[a-f0-9]{40}", target)):
        raise RuntimeError("Restricted to reviewed parity integration branch")
    sizes = {k: int(v) for k, v in records(P / "sizes.txt").items()}
    remotes = records(P / "remote.txt")
    manifests, guards, new, totals = {}, {}, {}, {}
    with tempfile.TemporaryDirectory(prefix="oneos-parity-") as tmp:
        store = Path(tmp)
        for image, sets in TARGETS.items():
            files, inventory = load_candidate(root, image)
            for set_id in sets:
                old = pins(P / f"{set_id}.sha256")
                old_guards = pins(P / f"{set_id}.guards.txt")
                fresh_guards = pins(files["guards.txt"])
                if set(fresh_guards) != set(old_guards):
                    raise ValueError(f"Guard coverage differs for {set_id}: {set(fresh_guards)^set(old_guards)}")
                names = list(old) + list(EXTRA[image])
                if len(set(names)) != len(names):
                    raise ValueError(f"Duplicate set member: {set_id}")
                updated = {}
                for name in names:
                    if name in KEEP or name not in files:
                        if name not in old:
                            raise ValueError(f"Missing required candidate: {name}")
                        updated[name] = old[name]
                        continue
                    rec = inventory[name]
                    sha, size = rec["sha256"], rec["bytes"]
                    updated[name] = sha
                    if sha in sizes and sizes[sha] != size:
                        raise ValueError(f"Conflicting size: {sha}")
                    if sha not in remotes:
                        if sha not in new:
                            dest = store / sha
                            shutil.copyfile(files[name], dest)
                            new[sha] = dest
                        sizes[sha], remotes[sha] = size, REMOTE
                for name, sha in fresh_guards.items():
                    if updated.get(name) != sha:
                        raise ValueError(f"Guard mismatch: {set_id}/{name}")
                if updated["guards.txt"] != digest(files["guards.txt"]):
                    raise ValueError("Guard manifest itself is not pinned")
                guards[set_id] = files["guards.txt"].read_bytes()
                manifests[set_id] = updated
                totals[set_id] = sum(sizes[h] for h in updated.values())
        shared = manifests["n6-f8u"]
        if any(manifests["n6-global-f8"][k] != v for k, v in shared.items()):
            raise ValueError("Fold8 common pins diverge")
        # Rebase the opt-in channel without altering the published v2 snapshot.
        channel_path = ROOT / "scripts/patch-migration/channel-v3/f8-n6-global-f8.json"
        old_channel = json.loads((ROOT / "scripts/patch-migration/channel-v2/f8-n6-global-f8.json").read_text())
        overrides = {x["name"]: x for x in old_channel["files"] if x["name"] in {"Fold8SystemUI.apk", "GlobalVerifiedAddons.tar"}}
        old_channel["revision"] = "fork-coloros-parity-r1"
        old_channel["files"] = [overrides.get(n, {"name": n, "sha256": h, "bytes": sizes[h], "source": remotes[h]})
                                for n, h in sorted(manifests["n6-global-f8"].items())]
        channel_temp = store / channel_path.name
        channel_temp.write_text(json.dumps(old_channel, indent=2) + "\n")
        # The release is a content store only; old APKs keep their old sources.
        if new:
            publish(TAG, new, target)
        publish(CHANNEL, {channel_temp.name: channel_temp}, target)
        for set_id, mapping in manifests.items():
            content = "# OneOS ColorOS parity candidate, verified rebuild 45727722152; device-specific extras retained.\n" + "".join(f"{sha}  {name}\n" for name, sha in mapping.items())
            for directory in (P, FIXTURES):
                (directory / f"{set_id}.sha256").write_text(content)
                (directory / f"{set_id}.guards.txt").write_bytes(guards[set_id])
        for file, values in (("sizes.txt", sizes), ("remote.txt", remotes)):
            (P / file).write_text("".join(f"{k} {v}\n" for k, v in sorted(values.items())))
        channel_path.parent.mkdir(parents=True, exist_ok=True)
        channel_path.write_bytes(channel_temp.read_bytes())

    # Model and root allowlists must agree with all four manifests.
    model = ROOT / "app-android/core/model/src/main/kotlin/com/fahadali/oneos/core/model/PackageSets.kt"
    s = model.read_text()
    def change_block(text, name, fn):
        start = text.index(f"    val {name} = PackageSet(")
        end = text.index("\n    )", start) + len("\n    )")
        return text[:start] + fn(text[start:end]) + text[end:]
    def file_expr(names):
        return " +\n            ".join(f'PackageFile("{n}", FileRole.GUEST_FIX)' for n in names)
    def extend(block, old, extra, size):
        block = replace(block, old, old.rstrip(",") + " +\n            " + file_expr(extra) + ",")
        return re.sub(r"approxBytes = [^\n]+,", f"approxBytes = {size}L,", block, count=1)
    s = change_block(s, "N6_F8U", lambda b: extend(b, "PackageFile(ODM_AI, FileRole.GUEST_FIX),", GESTURE, totals["n6-f8u"]))
    s = change_block(s, "TWINTY_S23", lambda b: extend(b, 'PackageFile("samcam_bar.tar", FileRole.GUEST_FIX),', WAVE + GESTURE, totals["twinity-s23"]))
    s = change_block(s, "PYB110_S23", lambda b: re.sub(
        r"approxBytes = [^\n]+,", f'approxBytes = {totals["pyb110-s23"]}L,',
        replace(b, '        ),\n        omitted = listOf(TETHERING),',
                '        ) + ' + file_expr(WAVE + GESTURE) + ',\n        omitted = listOf(TETHERING),'), count=1))
    model.write_text(s)
    root_script = ROOT / "app-android/app/src/full/assets/root/oneos_root.sh"
    s = root_script.read_text()
    for set_id, extra in (("n6-global-f8", GESTURE), ("n6-f8u", GESTURE), ("twinity-s23", WAVE + GESTURE), ("pyb110-s23", WAVE + GESTURE)):
        pattern = rf'(^        {re.escape(set_id)}\) echo "[^"\n]+)(" ;;)$'
        s, count = re.subn(pattern, lambda m: m[1] + " " + " ".join(extra) + m[2], s, flags=re.M)
        if count != 1:
            raise ValueError(f"Missing root allowlist: {set_id}")
    root_script.write_text(s)
    tests = ROOT / "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/PackageSetsTest.kt"
    s = tests.read_text()
    s = replace(s, "assertEquals(29, twinity.files.size)", "assertEquals(34, twinity.files.size)")
    s = replace(s, "assertEquals(36, n6.files.size)", "assertEquals(38, n6.files.size)")
    s = replace(s, 'listOf("samcam_bar.tar"), twinity.files.filter', 'listOf("samcam_bar.tar", ' + ", ".join(json.dumps(n) for n in WAVE + GESTURE) + '), twinity.files.filter')
    s = replace(s, "assertEquals(424_083_730L, twinity.approxBytes)", f'assertEquals({totals["twinity-s23"]}L, twinity.approxBytes)')
    s = replace(s, '- "oplus-framework_patched.jar").toSet(),', '- "oplus-framework_patched.jar" - "OplusGestureUI_patched.apk" - "OplusGestureUI_patched.apk.src_sha256").toSet(),')
    tests.write_text(s)
    registry = P / "patch-sources.json"
    s = registry.read_text()
    registry.write_text(replace(s, "fork-fold8-experimental-v2", CHANNEL))
    for name in ("migrate-all-patch-payloads.py", "test-patch-migration.py", "test-patch-channel.py"):
        path = ROOT / "scripts" / name
        s = path.read_text().replace("scripts/patch-migration/channel-v2/", "scripts/patch-migration/channel-v3/").replace("fork-fold8-experimental-v2", CHANNEL)
        path.write_text(s)
    print("Integrated sizes:", json.dumps(totals))

if __name__ == "__main__":
    main()
