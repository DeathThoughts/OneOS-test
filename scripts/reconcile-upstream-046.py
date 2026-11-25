#!/usr/bin/env python3
"""One-off reviewed semantic reconciliation of the pinned OneOS 0.5.46 merge."""
import re, subprocess, sys
from pathlib import Path

BASE, UPSTREAM = sys.argv[1:]
def git(*args): return subprocess.check_output(["git", *args], text=True)
def run(*args): subprocess.run(["git", *args], check=True)
def stage(path): run("add", "--", path)
def replace_once(s, old, new):
    if s.count(old) != 1: raise ValueError("Unexpected anchor: " + old[:200])
    return s.replace(old, new, 1)
def union_lists(s, oxygen, fold=None):
    s = s.replace(oxygen + ",", oxygen + ", " + oxygen.replace("OXYGEN16", "OXYGEN17_PAD") + ",")
    if fold:
        s = replace_once(s, fold + ",", fold + ", " + fold.replace("ONEUI95_F8", "ONEUI95_A16") + ",")
    return s
def resolve(path, a, b, c):
    name = Path(path).name
    if name in ("LastSessionActivity.kt", "FindingsActivity.kt", "SystemsActivity.kt"):
        if ("ReportSender.telegramSendOffered" in c and ("Cards.row" in a or "run {" in a)) or (name == "SystemsActivity.kt" and "SystemsNav.download" in c and "SystemsNav.open" in a):
            return a
    if name == "strings.xml" and "Telegram" in c and "GitHub" in a: return a
    if name == "Images.kt" and "val ALL:" in a and "OXYGEN17_PAD" in c:
        return union_lists(a, "OXYGEN16")
    if name == "PackageSets.kt" and "val ALL:" in a and "ONEUI95_A16" in c:
        return union_lists(a, "OXYGEN16", "ONEUI95_F8")
    if name == "Plain.kt" and "raw ext4 or erofs image" in a and "damagedDownload(system)" in c:
        return replace_once(a, '"It may not have finished. Download $system again."', "damagedDownload(system)")
    if name == "ImagesTest.kt":
        if "SystemFamily.OXYGENOS" in a and "SystemFamily.OXYGENOS, SystemFamily.OXYGENOS" in c:
            return replace_once(a, "SystemFamily.OXYGENOS,", "SystemFamily.OXYGENOS, SystemFamily.OXYGENOS,")
        if "Images.OXYGEN17_PAD" in c and "Images.OXYGEN16" in a:
            return union_lists(a, "Images.OXYGEN16")
    if name == "PackageSetsTest.kt" and "PackageSets.ONEUI95_A16" in c:
        return union_lists(a, "PackageSets.OXYGEN16", "PackageSets.ONEUI95_F8")
    if name == "ProfilesTest.kt" and "Profiles.ID_S26_FE" in a and "Profiles.ID_FLIP5" in c: return a + c
    if name == "StageScriptTest.kt" and "albumRuleIsTheSameInTheGuestAndTheRootScript" in a and "oneUi95OnOneUi8OnlyInTheTesterScript" in c:
        return a + "    }\n\n    /**\n" + c
    if path.endswith("/app/build.gradle.kts"):
        if not a.strip() and '"oxygen17pad"' in c: return c
        if '"twinity-s23"' in a and '"oneui95-a16"' in c:
            return "".join(l for l in a.splitlines(True) if '"twinity-s23"' in l or '"pyb110-s23"' in l) + c
        if "versionCode = 188" in a and 'versionName = "0.5.46"' in c:
            return c.replace("versionCode = 189", "versionCode = 190").replace('"0.5.46"', '"0.5.46-fork.1"')
        if "for (id in seriesPackageSets)" in a and "A17_ON_A16" in c:
            return replace_once(c, "for ((id, _) in packageSets)", "for (id in seriesPackageSets)")
    if path.endswith("/guest/build.gradle.kts") and "versionCode = 188" in a and "knoxAuthority" in c:
        return c.replace("versionCode = 15", "versionCode = 190").replace('"0.15"', '"0.5.46-fork.1"')
    if name == "oneos_root.sh" and "SCRIPTS=" in a and "oneos_bpf_ringbuf" in c and "SET_PYB=" in a:
        return replace_once(replace_once(a, "oneos_essential_key OneOSOverlayDimen.dex", "oneos_essential_key oneos_bpf_ringbuf OneOSOverlayDimen.dex"), "OneOSRcdCorners.apk host_installd.txt", "OneOSRcdCorners.apk OneOSTabS9FeBrightness.apk OneOSTabS9FeCamCompat.jar host_installd.txt")
    if name == "RootScript.kt" and '"guest_partitions.sh"' in a and '"oneos_bpf_ringbuf"' in c:
        return replace_once(replace_once(a, '"oneos_essential_key", "OneOSOverlayDimen.dex"', '"oneos_essential_key", "oneos_bpf_ringbuf", "OneOSOverlayDimen.dex"'), '"OneOSRcdCorners.apk", "host_installd.txt"', '"OneOSRcdCorners.apk", "OneOSTabS9FeBrightness.apk", "OneOSTabS9FeCamCompat.jar", "host_installd.txt"')
    if name == "guest_prepare.sh" and not a.strip() and "A17_ON_A16=0" in c and 'for d in "$GUEST_SYSTEM_EXT" "$GUEST_PRODUCT"' in b:
        if not c.endswith(b): raise ValueError("Unexpected partition validation context")
        return c[:-len(b)]
    if name == "guest_start.sh" and "ONEOS_AMP_TEMP" in a and "leaving Dolby and volume leveler to SoundAlive" in c:
        amp = [l for l in a.splitlines(True) if "ONEOS_AMP_TEMP" in l]
        if len(amp) != 1: raise ValueError("Amp guard anchor")
        return c + amp[0]
    if name == "oneos_switch.sh":
        if "DolbySoundSettingsProvider" in a and "HOST_TAB_S9_FE" in c:
            old = next(l for l in a.splitlines(True) if "if [" in l and "ONEOS_GUEST_DOLBY:-1" in l)
            new = next(l for l in c.splitlines(True) if "if [" in l and "ONEOS_GUEST_DOLBY:-1" in l)
            return replace_once(a, old, new)
        if "OneOSIslandY" in a and "OneOSCutoutFill" in c:
            return replace_once(a, "OneOSCutoutRect|", "OneOSCutoutRect|OneOSCutoutFill|")
    # The fork deliberately retains its existing reboot fallback; no new sysrq writes.
    if name == "return_to_oneui.sh" and "reboot -f" in a and c.startswith(a) and "/proc/sysrq-trigger" in c:
        return a
    raise ValueError("Unreviewed conflict")

def main():
    unresolved = git("diff", "--name-only", "--diff-filter=U").splitlines()
    print("Conflicts to reconcile:", len(unresolved), flush=True)
    pattern = re.compile(r"(?ms)^<<<<<<<[^\n]*\n(.*?)^\|\|\|\|\|\|\|[^\n]*\n(.*?)^=======\n(.*?)^>>>>>>>[^\n]*\n")
    errors = []
    catalog = {"app-android/packages/remote.txt", "app-android/packages/sizes.txt"}
    for path in unresolved:
        if path in catalog: continue
        if path.endswith("/ReportSender.kt"):
            run("rm", "--", path); continue
        if path in ("scripts/release-files.py", "switcher/assets/OneOSEdmStub.dex"):
            run("checkout", "--ours", "--", path); stage(path); continue
        if path == "app-android/packages/series.txt":
            rows = {}
            for ref in (BASE, UPSTREAM):
                for line in git("show", f"{ref}:{path}").splitlines():
                    if not line or line.startswith("#"): continue
                    key, *sets = line.split()
                    rows.setdefault(key, set()).update(sets)
            Path(path).write_text("".join(f"{key} {' '.join(sorted(sets))}\n" for key, sets in sorted(rows.items())))
            stage(path); continue
        p = Path(path); data = p.read_text(); found = list(pattern.finditer(data))
        if not found:
            errors.append(path + ": no recognized diff3 markers"); continue
        try:
            data = pattern.sub(lambda m: resolve(path, *m.groups()), data)
            if re.search(r"(?m)^(<<<<<<<|=======|>>>>>>>)", data): raise ValueError("Remaining conflict markers")
            p.write_text(data); stage(path); print("Resolved:", path, flush=True)
        except Exception as err:
            errors.append(f"{path}: {err}")
            for m in found:
                try: resolve(path, *m.groups())
                except Exception as e: print("UNRESOLVED", path, e, "\n", m.group()[:16000], flush=True)
    if errors: raise RuntimeError("\n".join(errors))
    # Preserve custom guarded payloads as complete, coherent sets.
    for sid in ("hyper-f8u", "x9u-in-1105-sultra"):
        for directory in ("app-android/packages", "app-android/core/model/src/test/resources/packages"):
            for suffix in (".sha256", ".guards.txt"):
                path = f"{directory}/{sid}{suffix}"
                if subprocess.run(["git","cat-file","-e",f"{BASE}:{path}"],capture_output=True).returncode == 0:
                    run("checkout", BASE, "--", path)
    for path in ("app-android/app/src/full/kotlin/com/fahadali/oneos/ui/LastSessionActivity.kt",
                 "app-android/app/src/main/kotlin/com/fahadali/oneos/ui/FindingsActivity.kt"):
        p = Path(path); data = p.read_text()
        data = re.sub(r"(?m)^import com\.fahadali\.oneos\.report\.ReportSender\n", "", data)
        if path.endswith("/FindingsActivity.kt"):
            # Upstream made the preview row clickable, but still tied it to Telegram.
            # Our SendReportActivity opens fork GitHub issues, with no service gate.
            data = data.replace("        // 0.5.46: this row looked like a greyed-out button; it opens Send to the OneOS team now.\n", "        // Open the fork's GitHub report flow from the preview row.\n")
            data = data.replace("        val canSend = ReportSender.telegramSendOffered\n", "")
            data = data.replace("onClick = if (canSend) ({ startActivity(SendReportActivity.intent(requireContext(), zip)) }) else null,", "onClick = { startActivity(SendReportActivity.intent(requireContext(), zip)) },")
        if "ReportSender." in data or "SendReportActivity.sentCode(" in data: raise ValueError("Unexpected report reference: " + path)
        p.write_text(data); stage(path)
    path = "app-android/app/build.gradle.kts"
    p = Path(path); data = p.read_text()
    data = replace_once(data, 'val a17OnA16: Boolean = (findProperty("oneos.a17OnA16") as String?)?.trim() != "false"', 'val a17OnA16: Boolean = (findProperty("oneos.a17OnA16") as String?)?.trim() == "true"')
    if "val seriesPackageSets:" not in data or "val packedSets:" in data: raise ValueError("Unexpected package selection merge")
    data = replace_once(data, '    sets\n}\nrequire(seriesPackageSets.containsAll(selectedLocalSets))', '    sets + (if ((findProperty("oneos.a17OnA16") as String?)?.trim() == "true") setOf("oneui95-a16") else emptySet())\n}\nrequire(seriesPackageSets.containsAll(selectedLocalSets))')
    p.write_text(data); stage(path)
    subprocess.run([sys.executable, "scripts/sync-upstream-catalog.py", BASE, UPSTREAM], check=True)
    if git("diff", "--name-only", "--diff-filter=U").strip(): raise RuntimeError("Unresolved index entries remain")
    run("diff", "--cached", "--check")
    print("All reviewed text conflicts reconciled; DEX rebuild remains.", flush=True)
if __name__ == "__main__": main()
