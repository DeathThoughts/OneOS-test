#!/usr/bin/env python3
"""Publish locally built, hash-pinned set payloads to this fork’s GitHub release.

No GSI images are handled here. Original OneOS set tooling by Fahad Ali Javed.
Run with --dry-run to verify local files without publishing. Requires gh with
contents:write on DeathThoughts/OneOS-test for publication.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRADLE = os.path.join(ROOT, "app-android/app/build.gradle.kts")
PACKAGES = os.path.join(ROOT, "app-android/packages")
GH_REPO = "DeathThoughts/OneOS-test"
GH_TAG = "fork-payloads-v1"
SHA_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9_][A-Za-z0-9._+-]*)$")


def sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sets():
    found = re.findall(r'"([a-z0-9-]+)" to \(File\(repoRoot, "([^"]+)"', Path(GRADLE).read_text())
    if not found:
        sys.exit("no packageSets in build.gradle.kts")
    return found


def wanted():
    """sha256 -> built file, for every file of every set (checked against its list)."""
    out = {}
    for sid, d in sets():
        lst = os.path.join(PACKAGES, f"{sid}.sha256")
        for line in open(lst):
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            m = SHA_LINE.match(line)
            if not m:
                sys.exit(f"{lst}: not a sha256sum line: {line}")
            sha, name = m.groups()
            path = os.path.join(ROOT, d, name)
            if sha in out:
                continue
            if not os.path.isfile(path):
                sys.exit(f"{sid}: {path} is missing (build the sets first)")
            if sha256(path) != sha:
                sys.exit(f"{sid}: {path} is not the file packages/{sid}.sha256 names")
            out[sha] = path
    return out



def main():
    want = wanted()
    print(f'{len(want)} verified local payloads')
    if '--dry-run' in sys.argv:
        return
    spec = importlib.util.spec_from_file_location('migration', Path(__file__).with_name('migrate-patch-payloads.py'))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    existing = migration.assets(GH_TAG) or {}
    target = subprocess.check_output(['git', '-C', ROOT, 'rev-parse', 'HEAD'], text=True).strip()
    migration.ensure_release(GH_TAG, target)
    for sha, filename in want.items():
        size = os.path.getsize(filename)
        if sha in existing:
            if not migration.matches(existing[sha], sha, size):
                raise RuntimeError(f'Existing payload differs: {sha}')
            continue
        # GitHub names the asset after the file basename, so link its SHA-256 name.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / sha
            os.link(filename, asset)
            migration.gh('release', 'upload', GH_TAG, str(asset))
    Path(PACKAGES, 'remote.txt').write_text(''.join(f'{sha} gh:{GH_REPO}:{GH_TAG}\n' for sha in sorted(want)))
    Path(PACKAGES, 'sizes.txt').write_text(''.join(f'{sha} {os.path.getsize(want[sha])}\n' for sha in sorted(want)))


if __name__ == '__main__':
    main()
