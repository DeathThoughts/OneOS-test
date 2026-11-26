#!/usr/bin/env python3
"""Publish the exact missing pinned package payloads, then verify every active set.
No GSI downloads, patch rebuilding, pin changes, or asset overwrites.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REPO = "DeathThoughts/OneOS-test"
TAG = "fork-payloads-v2"
PIN = re.compile(r"[0-9a-f]{64}")
SOURCE = re.compile(r"gh:DeathThoughts/OneOS-test:([a-z0-9][a-z0-9.-]{0,39})")
spec = importlib.util.spec_from_file_location("migration", Path(__file__).with_name("migrate-patch-payloads.py"))
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

def rows(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, value = line.split(None, 1)
        if not PIN.fullmatch(key) or key in result:
            raise ValueError(f"Invalid or duplicate hash in {path}: {key}")
        result[key] = value.strip()
    return result

def load():
    packages = ROOT / "app-android/packages"
    remote = rows(packages / "remote.txt")
    sizes = {h: int(n) for h, n in rows(packages / "sizes.txt").items()}
    pending = json.loads((ROOT / "scripts/patch-migration/fork-payloads-v2.json").read_text())["payloads"]
    active = {}
    for manifest in sorted(packages.glob("*.sha256")):
        for line in manifest.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            sha, name = line.split(None, 1)
            if not PIN.fullmatch(sha):
                raise ValueError(f"Invalid pin: {manifest.name}: {line}")
            active.setdefault(sha, []).append(f"{manifest.stem}/{name.strip()}")
    for sha, names in active.items():
        if not SOURCE.fullmatch(remote.get(sha, "")) or not 0 < sizes.get(sha, 0) <= 2 * 1024**3:
            raise RuntimeError(f"Missing fork source or size: {sha} {names}")
    for sha, item in pending.items():
        if remote.get(sha) != f"gh:{REPO}:{TAG}" or sizes.get(sha) != item["bytes"] or not item["sources"]:
            raise RuntimeError(f"Migration plan differs from catalog: {sha}")
    return remote, sizes, pending, active

def verify_asset(asset, sha, size, tag, directory):
    if asset is None:
        return False
    if asset.get("state") != "uploaded" or asset.get("size") != size:
        raise RuntimeError(f"Existing asset size/state differs: {tag}/{sha}")
    digest = asset.get("digest")
    if digest is not None:
        if digest != f"sha256:{sha}":
            raise RuntimeError(f"Existing asset digest differs: {tag}/{sha}")
    else:
        path = migration.fetch(sha, {"bytes": size, "sources": [f"gh:{REPO}:{tag}"]}, directory)
        path.unlink()
    return True

def verify_channel(existing, directory, publish, target):
    # Keep the old channel release intact for older APKs. Rebase only the two
    # explicitly replaceable contributor payloads onto the current stable set.
    manifest = ROOT / "scripts/patch-migration/channel-v3/f8-n6-global-f8.json"
    data = json.loads(manifest.read_text())
    packages = ROOT / "app-android/packages"
    base = {name: sha for sha, name in rows(packages / "n6-global-f8.sha256").items()}
    guards = {name: sha for sha, name in rows(packages / "n6-global-f8.guards.txt").items()}
    entries = {item["name"]: item for item in data["files"]}
    replaceable = {"Fold8SystemUI.apk", "GlobalVerifiedAddons.tar"}
    if set(entries) != set(base) or len(entries) != len(data["files"]):
        raise RuntimeError("Fold8 channel set differs")
    for name, item in entries.items():
        sha, size = item["sha256"], item["bytes"]
        if (name not in replaceable and sha != base[name]) or (name in guards and sha != guards[name]):
            raise RuntimeError(f"Fold8 channel changes a guarded or non-replaceable file: {name}")
        match = SOURCE.fullmatch(item["source"])
        if not match or not PIN.fullmatch(sha) or not 0 < size <= 268435456:
            raise RuntimeError(f"Invalid Fold8 channel pin: {name}")
        tag = match.group(1)
        if tag not in existing:
            existing[tag] = migration.assets(tag) or {}
        if not verify_asset(existing[tag].get(sha), sha, size, tag, directory):
            raise RuntimeError(f"Missing Fold8 channel payload: {name}")
    tag = "fork-fold8-experimental-v3"
    current = migration.assets(tag) or {}
    asset = current.get(manifest.name)
    digest = f"sha256:{migration.digest(manifest)}"
    if asset is None and publish:
        migration.ensure_release(tag, target)
        migration.gh("release", "upload", tag, str(manifest))
        asset = (migration.assets(tag) or {}).get(manifest.name)
    if not asset or asset.get("state") != "uploaded" or asset.get("size") != manifest.stat().st_size or asset.get("digest") != digest:
        raise RuntimeError("Fold8 channel v2 manifest is missing or differs from reviewed snapshot")
    print("Verified Fold8 experimental channel v2 (old release preserved).", flush=True)

def run(publish=False, target=None):
    remote, sizes, pending, active = load()
    tags = {SOURCE.fullmatch(remote[h]).group(1) for h in active}
    existing = {tag: migration.assets(tag) or {} for tag in sorted(tags)}
    missing = {}
    with tempfile.TemporaryDirectory(prefix="oneos-all-patches-") as tmp:
        directory = Path(tmp)
        for sha in sorted(active):
            tag = SOURCE.fullmatch(remote[sha]).group(1)
            if verify_asset(existing[tag].get(sha), sha, sizes[sha], tag, directory):
                continue
            if not publish or tag != TAG or sha not in pending:
                raise RuntimeError(f"Missing fork asset: {tag}/{sha} {active[sha]}")
            missing[sha] = pending[sha]
        print(f"{len(active)} active hashes; {len(missing)} to publish", flush=True)
        if missing:
            # No release changes until every missing payload passes size and SHA-256.
            with ThreadPoolExecutor(max_workers=4) as pool:
                files = list(pool.map(lambda item: migration.fetch(item[0], item[1], directory), missing.items()))
            migration.ensure_release(TAG, target)
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda path: migration.gh("release", "upload", TAG, str(path)), files))
            existing[TAG] = migration.assets(TAG) or {}
        for sha in sorted(active):
            tag = SOURCE.fullmatch(remote[sha]).group(1)
            if not verify_asset(existing[tag].get(sha), sha, sizes[sha], tag, directory):
                raise RuntimeError(f"Fork asset still missing after publication: {tag}/{sha}")
        verify_channel(existing, directory, publish, target)
    print(f"Verified all {len(active)} active pinned payloads in {len(tags)} fork releases.", flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--publish", action="store_true")
    modes.add_argument("--verify", action="store_true")
    modes.add_argument("--check-catalog", action="store_true")
    parser.add_argument("--target")
    args = parser.parse_args()
    if args.check_catalog:
        _, _, pending, active = load()
        print(f"Catalog: {len(active)} active hashes; {len(pending)} planned copies")
        return
    if args.publish:
        if not args.target or not re.fullmatch(r"[0-9a-f]{40}", args.target):
            parser.error("--publish needs a full commit SHA")
        if os.environ.get("GITHUB_ACTIONS") == "true" and (
            os.environ.get("GITHUB_REPOSITORY") != REPO or
            os.environ.get("GITHUB_REF") != "refs/heads/debug" or
            os.environ.get("GITHUB_EVENT_NAME") not in ("push", "workflow_dispatch") or
            os.environ.get("GITHUB_SHA") != args.target
        ):
            raise RuntimeError("Publication is restricted to the fork's debug integration")
    run(args.publish, args.target)

if __name__ == "__main__":
    main()
