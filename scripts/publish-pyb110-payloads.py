#!/usr/bin/env python3
"""Publish only the verified OnePlus 16 overlay files missing from the fork."""

import os
from pathlib import Path
import re
import runpy
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REPO = "DeathThoughts/OneOS-test"
BRANCH = "refs/heads/port/coloros17-pyb110-apk"
TAG = "pyb110-payloads-v2"
DESTINATION = f"gh:{REPO}:{TAG}"
MIGRATION = runpy.run_path(str(ROOT / "scripts/migrate-patch-payloads.py"))


def records(path):
    return dict(line.split(None, 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#"))


def main():
    target = os.environ.get("GITHUB_SHA", "")
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_REF") != BRANCH or not re.fullmatch(r"[0-9a-f]{40}", target)):
        raise RuntimeError("Publication requires the OnePlus 16 trial workflow")

    overlays = Path(sys.argv[1])
    packages = ROOT / "app-android/packages"
    sizes = records(packages / "sizes.txt")
    remote = records(packages / "remote.txt")
    pins = records(packages / "pyb110-s23.sha256")
    needed = {}
    reused = {}
    for sha, name in pins.items():
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", name):
            raise ValueError(f"Invalid package pin: {name}")
        size = int(sizes[sha])
        if not 0 < size <= 256 * 1024 * 1024:
            raise ValueError(f"Invalid package size: {name}")
        if DESTINATION in remote[sha].split(","):
            needed[sha] = (name, size)
        else:
            reused[sha] = (name, size)

    published = MIGRATION["assets"](TAG) or {}
    existing_shared = {}
    for sha, (name, size) in reused.items():
        fork_tags = [part.removeprefix(f"gh:{REPO}:") for part in remote[sha].split(",")
                     if part.startswith(f"gh:{REPO}:")]
        for tag in fork_tags:
            if tag not in existing_shared:
                existing_shared[tag] = MIGRATION["assets"](tag) or {}
        if not any(MIGRATION["matches"](existing_shared[tag].get(sha), sha, size) for tag in fork_tags):
            raise RuntimeError(f"No matching shared fork asset for {name}")

    with tempfile.TemporaryDirectory(prefix="oneos-pyb110-payloads-") as work:
        directory = Path(work)
        for sha, (name, size) in needed.items():
            source = overlays / name
            if not source.is_file() or source.stat().st_size != size or MIGRATION["digest"](source) != sha:
                raise ValueError(f"Artifact differs from the pin for {name}")
            if sha in published:
                if not MIGRATION["matches"](published[sha], sha, size):
                    raise RuntimeError(f"Existing hash-named asset differs: {name}")
            else:
                shutil.copyfile(source, directory / sha)
        if any(directory.iterdir()):
            MIGRATION["ensure_release"](TAG, target)
            for source in sorted(directory.iterdir()):
                MIGRATION["gh"]("release", "upload", TAG, str(source))

    published = MIGRATION["assets"](TAG) or {}
    for sha, (name, size) in needed.items():
        if not MIGRATION["matches"](published.get(sha), sha, size):
            raise RuntimeError(f"Published payload differs from the pin for {name}")
    print(f"Verified {len(needed)} OnePlus 16 and {len(reused)} shared server payloads.")


if __name__ == "__main__":
    main()
