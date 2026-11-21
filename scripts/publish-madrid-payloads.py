#!/usr/bin/env python3
"""Publish the pinned Madrid overlays from the verified Actions artifact."""
import argparse
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = runpy.run_path(str(ROOT / "scripts/migrate-patch-payloads.py"))
REPO = MIGRATION["REPO"]
SHARED_TAG = MIGRATION["TAG"]
MADRID_TAG = "madrid-payloads-v1"
BRANCH = "refs/heads/test/hyperos-madrid-apk"


def index(path):
    entries = {}
    for row in path.read_text().splitlines():
        if not row or row.startswith("#"):
            continue
        sha, value = row.split()
        if not MIGRATION["PIN"].fullmatch(sha) or sha in entries:
            raise ValueError(f"{path}: invalid or duplicate digest {sha}")
        entries[sha] = value
    return entries


def inventory(packages):
    sizes = index(packages / "sizes.txt")
    sources = index(packages / "remote.txt")
    payloads = {SHARED_TAG: {}, MADRID_TAG: {}}
    for sha, name in index(packages / "hyper-madrid.sha256").items():
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._+-]*", name):
            raise ValueError(f"Invalid Madrid overlay name: {name}")
        size = int(sizes[sha])
        if not 0 < size <= 268435456:
            raise ValueError(f"Invalid indexed size for {name}: {size}")
        source = sources[sha]
        tag = next((tag for tag in payloads if source == f"gh:{REPO}:{tag}"), None)
        if tag is None:
            raise ValueError(f"Unexpected Madrid payload source for {name}: {source}")
        payloads[tag][sha] = (name, size)
    if tuple(map(len, payloads.values())) != (17, 23):
        raise ValueError("Expected 17 shared and 23 Madrid-specific payloads")
    return payloads


def retry(operation):
    for attempt in range(3):
        try:
            return operation()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def check_asset(asset, sha, size):
    if not MIGRATION["matches"](asset, sha, size):
        raise RuntimeError(f"Release asset unavailable or differs from its pin: {sha}")


def published_assets(tag, allow_missing=False):
    assets = retry(lambda: MIGRATION["assets"](tag))
    if assets is None and allow_missing:
        return {}
    release = json.loads(retry(lambda: subprocess.check_output([
        "gh", "api", f"repos/{REPO}/releases/tags/{tag}",
    ], timeout=60)))
    if (release.get("tag_name") != tag or release.get("draft") is not False
            or (tag == MADRID_TAG and release.get("prerelease") is not True)):
        raise RuntimeError(f"Unexpected release state: {REPO} {tag}")
    return assets or {}


def check_release(tag, pins):
    published = published_assets(tag)
    for sha, (_, size) in pins.items():
        check_asset(published.get(sha), sha, size)


def ensure_release(target):
    try:
        MIGRATION["ensure_release"](MADRID_TAG, target)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # An interrupted create may already have succeeded.
        if MIGRATION["assets"](MADRID_TAG) is None:
            raise


def upload(path, size):
    try:
        MIGRATION["gh"]("release", "upload", MADRID_TAG, str(path))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Handle a completed upload whose response was lost, without clobbering assets.
        asset = (retry(lambda: MIGRATION["assets"](MADRID_TAG)) or {}).get(path.name)
        if asset is None:
            raise
        check_asset(asset, path.name, size)


def publish(overlays, target, payloads):
    madrid = payloads[MADRID_TAG]
    with tempfile.TemporaryDirectory(prefix="oneos-madrid-publish-") as tmp:
        directory = Path(tmp)
        # Validate all Madrid-specific files before any GitHub mutation.
        for sha, (name, size) in madrid.items():
            staged = directory / sha
            shutil.copyfile(overlays / name, staged)
            if staged.stat().st_size != size or MIGRATION["digest"](staged) != sha:
                raise ValueError(f"Artifact payload differs from the Madrid manifest or size index: {name}")

        check_release(SHARED_TAG, payloads[SHARED_TAG])
        retry(lambda: ensure_release(target))
        published = published_assets(MADRID_TAG)
        for sha, (_, size) in madrid.items():
            if sha in published:
                check_asset(published[sha], sha, size)
        for sha, (_, size) in madrid.items():
            if sha not in published:
                retry(lambda: upload(directory / sha, size))
        check_release(MADRID_TAG, madrid)
    print("Verified 40 hash-named Madrid payloads across the two fork releases.")


def missing_payloads(payloads):
    check_release(SHARED_TAG, payloads[SHARED_TAG])
    existing = published_assets(MADRID_TAG, allow_missing=True)
    for sha, (_, size) in payloads[MADRID_TAG].items():
        if sha in existing:
            check_asset(existing[sha], sha, size)
    return payloads[MADRID_TAG].keys() - existing.keys()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Verify remote pins; exit 2 for missing Madrid assets.")
    mode.add_argument("--publish", type=Path, metavar="ARTIFACT", help="Publish missing assets from the verified artifact.")
    args = parser.parse_args()
    payloads = inventory(ROOT / "app-android/packages")
    if args.check:
        missing = missing_payloads(payloads)
        if missing:
            print(f"{len(missing)} Madrid assets need publication.")
            return 2
        print("Verified all 40 Madrid payloads. No source artifact needed.")
        return 0

    target = os.environ.get("GITHUB_SHA", "")
    checkout = subprocess.check_output(["git", "-C", ROOT, "rev-parse", "HEAD"], text=True).strip()
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_REF") != BRANCH or target != checkout):
        raise RuntimeError(f"Publication requires the checked-out {REPO} Actions build on {BRANCH}")
    if missing_payloads(payloads):
        publish(args.publish, target, payloads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
