#!/usr/bin/env python3
"""Derive conservative, image-specific debloat rows from the official research.

Run against a read-only mount of the exact pinned donor image, never the host.
A matching package is necessary but not sufficient: inspect every APK in its
folder, veto protected roles, shared UIDs, library providers and ambiguous paths.
Unknown apps and official Keep entries are never promoted by this importer.
"""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RULES = runpy.run_path(str(ROOT / "scripts/debloat/make-app-list.py"))
FIELDS = ("package", "label", "rating", "reason", "images", "folder", "notes")
TARGETS = {
    "coloros17-twinity4c": ("coloros.tsv",),
    "coloros17-pyb110": ("coloros.tsv",),
    "hyperos-popsicle": ("hyperos.tsv",),
    "hyperos-madrid": ("hyperos.tsv",),
    "pixelos17-onyx": ("pixel.tsv", "infinityx-lineage.tsv"),
}
CONTAINERS = set(RULES["CONTAINERS"])
NEVER = set(RULES["NEVER"]) | {
    "com.android.systemui", "com.android.settings", "com.android.phone",
    "com.android.launcher3", "com.google.android.gms", "com.android.vending",
    "com.google.android.webview", "com.android.webview",
    "com.android.permissioncontroller", "com.google.android.permissioncontroller",
    "com.sec.imsservice", "com.fahadali.oneos.guest", "com.android.providers.media.module",
}
CRITICAL = re.compile(
    r"android\.intent\.category\.(?:HOME|SETUP_WIZARD)|"
    r"android\.permission\.BIND_(?:INPUT_METHOD|INCALL_SERVICE|TELECOM_CONNECTION_SERVICE|"
    r"VPN_SERVICE|DEVICE_ADMIN|CARRIER_SERVICES|ACCESSIBILITY_SERVICE)|"
    r"android\.intent\.action\.(?:MANAGE_DEFAULT_APP|RESPOND_VIA_MESSAGE)|"
    r"android\.app\.role\.(?:HOME|DIALER|SMS)|"
    r"com\.android\.internal\.telephony|android\.telephony\.ims\.ImsService"
)
PKG = re.compile(r"^package: name='([^']+)'", re.M)
PROVIDES = re.compile(r"^\s*E:\s+(?:library|static-library|sdk-library)\b", re.M)
SHARED = re.compile(r"^\s*A:\s+(?:android:)?sharedUserId\b", re.M)
PERSISTENT = re.compile(r"^\s*A:\s+(?:android:)?persistent\b[^\n]*(?:true|0xffffffff|0x1)\b", re.M)
# No automatic expansion of the old MysticGSI exceptions. New variants only
# inherit explicitly Safe/Caution decisions, not Keep-to-Caution overrides.

def official(image, source):
    by_pkg = defaultdict(list)
    for name in TARGETS[image]:
        with (source / name).open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                by_pkg[row["package"]].append(row)
    allowed = {}
    for pkg, rows in by_pkg.items():
        if pkg in NEVER or any(r["rating"] not in ("🟢", "🟡") or RULES["LIB_PROVIDER"].search(r["notes"]) for r in rows):
            continue
        if image == "pixelos17-onyx" and not pkg.startswith(("com.android.", "com.google.")):
            continue
        if not RULES["PKG"].fullmatch(pkg):
            continue
        chosen = next((r for r in rows if r["rating"] == "🟡"), rows[0])
        allowed[pkg] = dict(chosen)
    return allowed

def command(aapt, mode, apk):
    args = [str(aapt), "dump", mode]
    if mode == "xmltree":
        args += ["--file", "AndroidManifest.xml"]
    return subprocess.check_output([*args, str(apk)], text=True, errors="replace",
                                   stderr=subprocess.DEVNULL, timeout=45)

def manifest_ok(xml):
    return ("E: manifest" in xml and "E: application" in xml
            and not any(p.search(xml) for p in (CRITICAL, PROVIDES, SHARED, PERSISTENT)))

def inspect(aapt, folder, apks):
    packages, manifests = set(), []
    try:
        for apk in apks:
            badging = command(aapt, "badging", apk)
            match = PKG.search(badging)
            if not match:
                return None, "no package"
            packages.add(match.group(1))
            manifests.append(command(aapt, "xmltree", apk))
    except (OSError, subprocess.SubprocessError):
        return None, "unreadable manifest"
    if len(packages) != 1:
        return None, "multiple packages"
    if any(not manifest_ok(m) for m in manifests):
        return next(iter(packages)), "protected manifest"
    return next(iter(packages)), None

def collect(root):
    folders = defaultdict(list)
    for current, dirs, files in os.walk(root, followlinks=False):
        p = Path(current)
        dirs[:] = sorted(d for d in dirs if not (p / d).is_symlink())
        if p == root:
            dirs[:] = [d for d in dirs if d not in {"data", "vendor", "odm", "dev", "proc", "sys", "apex", "mnt", "storage", "metadata", "cache", "lost+found"}]
        rel = p.relative_to(root)
        if not any(c in CONTAINERS for c in rel.parts[:-1]):
            continue
        for f in sorted(files):
            if f.endswith(".apk") and (p / f).is_file() and not (p / f).is_symlink():
                folders[p].append(p / f)
    return folders

def app_path(root, folder):
    parts = folder.relative_to(root).parts
    path = "/" + "/".join(parts)
    # system-as-root images have /system/app; older filesystem images have
    # /app at their own root, mounted at /system by OneOS.
    if parts[0] in CONTAINERS:
        path = "/system" + path
    return path

def folder_bytes(path):
    # APK + native libraries/oat; never follow symlinks.
    return sum(f.stat().st_size for current, dirs, files in os.walk(path, followlinks=False)
               for name in files if (f := Path(current) / name).is_file() and not f.is_symlink())

def derive(image, root, aapt, source):
    allowed = official(image, source)
    folders = collect(root)
    parents = set(folders)
    ambiguous = {p for p in parents if any(a in parents for a in p.parents)}
    ambiguous |= {a for p in ambiguous for a in p.parents if a in parents}
    results = defaultdict(list)
    veto, report = set(), {"image": image, "official_candidates": len(allowed), "apk_folders": len(folders), "excluded": defaultdict(int)}
    def one(item):
        p, apks = item
        pkg, problem = inspect(aapt, p, apks)
        path = app_path(root, p)
        if p in ambiguous or len(apks) > 4 or not RULES["path_ok"](path):
            problem = "ambiguous folder"
        return p, pkg, path, problem
    with ThreadPoolExecutor(max_workers=4) as pool:
        for p, pkg, path, problem in pool.map(one, sorted(folders.items())):
            if problem:
                report["excluded"][problem] += 1
                if pkg:
                    veto.add(pkg)
                continue
            if pkg not in allowed:
                report["excluded"]["no approved official rating"] += 1
                continue
            results[pkg].append((path, folder_bytes(p)))
    rows = []
    for pkg, found in sorted(results.items()):
        if pkg in veto:
            continue
        row = {k: allowed[pkg][k] for k in FIELDS}
        row["images"] = image
        row["folder"] = ";".join(sorted(p for p, _ in found))
        row["notes"] = f"{max(1, sum(n for _, n in found) // 1024)} KB; exact donor APK manifests checked; inherited official rating"
        rows.append(row)
    if not rows or sum(len(r["folder"].split(";")) for r in rows) > 600:
        raise RuntimeError(f"{image}: empty list or more than 600 folders")
    report["safe"] = sum(r["rating"] == "🟢" for r in rows)
    report["caution"] = sum(r["rating"] == "🟡" for r in rows)
    return rows, report

def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

class Tests(unittest.TestCase):
    def test_manifest_veto(self):
        base = "E: manifest\n  E: application\n"
        self.assertTrue(manifest_ok(base))
        for extra in ('A: android:sharedUserId(0x0101000b)="android.uid.system"',
                      "A: android:persistent(0x0101000d)=(type 0x12)0xffffffff",
                      "E: library (line=2)",
                      'A: android:name="android.intent.category.HOME"',
                      'A: android:permission="android.permission.BIND_INPUT_METHOD"'):
            self.assertFalse(manifest_ok(base + "  " + extra))
        self.assertFalse(manifest_ok(""))
    def test_rating_veto(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            row = dict(package="org.example.ads", label="Ads", rating="🟢", reason="Ads",
                       images="source", folder="/system/app/Ads", notes="10 KB")
            write_rows(p / "coloros.tsv", [row])
            self.assertIn(row["package"], official("coloros17-twinity4c", p))
            write_rows(p / "coloros.tsv", [row, dict(row, rating="🔴")])
            self.assertNotIn(row["package"], official("coloros17-twinity4c", p))
            write_rows(p / "coloros.tsv", [dict(row, notes="provides lib thing needed by 2")])
            self.assertNotIn(row["package"], official("coloros17-twinity4c", p))
    def test_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            for path in ("system/app/Ads/Ads.apk", "system/app/Ads/split.apk",
                         "vendor/app/Host/Host.apk", "system/overlay/X.apk"):
                f = r / path
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"fixture")
            self.assertEqual(list(collect(r)), [r / "system/app/Ads"])
            self.assertEqual(app_path(r, r / "app/Ads"), "/system/app/Ads")

def main():
    if sys.argv[1:] == ["--self-test"]:
        unittest.main(argv=[sys.argv[0]])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", choices=TARGETS)
    parser.add_argument("root", type=Path)
    parser.add_argument("aapt", type=Path)
    parser.add_argument("out", type=Path)
    a = parser.parse_args()
    rows, report = derive(a.image, a.root.resolve(), a.aapt, ROOT / "docs/debloat")
    write_rows(a.out / (a.image + ".tsv"), rows)
    (a.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
