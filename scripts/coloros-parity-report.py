#!/usr/bin/env python3
"""Inventory rebuilt candidates without changing release pins or publishing."""
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "findn6": ("n6-f8u", "n6-global-f8"),
    "twinity": ("twinity-s23",),
    "pyb110": ("pyb110-s23",),
}
def pins(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        sha, name = line.split(None, 1)
        name = name.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or Path(name).name != name or name in result:
            raise ValueError(f"Invalid pin: {line}")
        result[name] = sha
    return result

def inventory(folder):
    result = {}
    for p in sorted(folder.iterdir()):
        if p.is_file() and not p.is_symlink():
            with p.open("rb") as f:
                result[p.name] = {"sha256": hashlib.file_digest(f, "sha256").hexdigest(), "bytes": p.stat().st_size}
    return result

def main():
    image, folder, destination = sys.argv[1:]
    folder, destination = Path(folder), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    files = inventory(folder)
    for name, sha in pins(folder / "guards.txt").items():
        if name not in files or files[name]["sha256"] != sha:
            raise ValueError(f"Guard hash mismatch: {name}")
    ui = folder.parent / "systemui"
    if ui.is_dir():
        files.update(inventory(ui))
    packages = ROOT / "app-android/packages"
    ref = pins(packages / "x9u-in-1105-sultra.sha256")
    comparison = {}
    for target in TARGETS[image]:
        baseline = pins(packages / (target + ".sha256"))
        comparison[target] = {
            "candidate_replacements": sorted(name for name in baseline.keys() & files.keys() if baseline[name] != files[name]["sha256"]),
            "existing_files_not_rebuilt_keep_current_pin": sorted(baseline.keys() - files.keys()),
            "new_files_need_catalog_and_runtime_review": sorted(files.keys() - baseline.keys()),
            "reference_files_not_generated": sorted(ref.keys() - files.keys()),
        }
    (destination / "candidate-inventory.json").write_text(json.dumps({
        "image": image, "candidate_only": True, "files": files, "targets": comparison,
        "note": "Existing set pins remain active. Do not install this inventory as a complete set."
    }, indent=2) + "\n")
    (destination / "candidate.sha256").write_text("".join(f"{v['sha256']}  {k}\n" for k, v in files.items()))
    print(json.dumps(comparison, indent=2))

if __name__ == "__main__":
    main()
