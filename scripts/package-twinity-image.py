#!/usr/bin/env python3
"""Package the verified Twinity4c system image and optionally publish it from CI."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REPO = "DeathThoughts/OneOS-test"
BRANCH = "test/coloros-twinity-4c"
ZIP_NAME = "OneOS_ColorOS17_Twinity4c.zip"
IMAGE_NAME = "system.img"
INVENTORY = "inventory.json"
NOTES = "release-notes.md"
PART_LIMIT = 1_900_000_000
CHUNK = 4 * 1024 * 1024


class PackageError(Exception):
    pass


def fingerprint(source):
    digest = hashlib.sha256()
    size = 0
    while data := source.read(CHUNK):
        digest.update(data)
        size += len(data)
    return {"size": size, "sha256": digest.hexdigest()}


def file_pin(path):
    with path.open("rb") as source:
        return fingerprint(source)


def image_pin(source, metadata):
    image = metadata.get("image") if isinstance(metadata, dict) else None
    if not isinstance(image, dict):
        raise PackageError("missing image pin in builder metadata")
    size, digest = image.get("size"), image.get("sha256")
    if type(size) is not int or size < 1082 or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise PackageError("invalid image size or SHA-256 in builder metadata")
    source.seek(1080)
    if source.read(2) != b"\x53\xef":
        raise PackageError("system.img is missing ext4 superblock magic")
    source.seek(0)
    actual = fingerprint(source)
    if actual["size"] != size:
        raise PackageError("system.img size differs from builder metadata")
    if actual["sha256"] != digest:
        raise PackageError("system.img SHA-256 differs from builder metadata")
    return actual


def write_zip(source, path):
    # A fixed entry timestamp keeps the ZIP stable when the image bytes match.
    entry = zipfile.ZipInfo(IMAGE_NAME)
    entry.compress_type = zipfile.ZIP_DEFLATED
    source.seek(0)
    with zipfile.ZipFile(path, "x", allowZip64=True) as archive:
        with archive.open(entry, "w", force_zip64=True) as member:
            shutil.copyfileobj(source, member, CHUNK)


def validate_zip(path, expected):
    with zipfile.ZipFile(path, allowZip64=True) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].orig_filename != IMAGE_NAME:
            raise PackageError("ZIP must contain exactly one entry named system.img")
        entry = entries[0]
        if entry.compress_type != zipfile.ZIP_DEFLATED or entry.file_size != expected["size"]:
            raise PackageError("ZIP entry has an unexpected method or raw size")
        with archive.open(entry) as member:
            if fingerprint(member) != expected:
                raise PackageError("ZIP entry SHA-256 differs from the verified system.img")


def split_zip(path, out, limit=PART_LIMIT):
    parts = []
    joined = hashlib.sha256()
    with path.open("rb") as source:
        data = source.read(min(CHUNK, limit))
        while data:
            index = len(parts) + 1
            if index > 999:
                raise PackageError("ZIP requires more than 999 numbered parts")
            name = f"{path.name}.{index:03d}"
            digest = hashlib.sha256()
            size = 0
            with (out / name).open("xb") as destination:
                while data:
                    destination.write(data)
                    size += len(data)
                    digest.update(data)
                    joined.update(data)
                    if size == limit:
                        break
                    data = source.read(min(CHUNK, limit - size))
            parts.append({"name": name, "size": size, "sha256": digest.hexdigest()})
            data = source.read(min(CHUNK, limit))
    return {"name": path.name, "size": sum(part["size"] for part in parts), "sha256": joined.hexdigest()}, parts


def release_notes():
    return f"""# OneOS ColorOS 17 Twinity4c

Download every numbered `{ZIP_NAME}` part listed in `inventory.json` to the
same directory. Join the parts in numeric order to recreate the ZIP.

```sh
cat {ZIP_NAME}.[0-9][0-9][0-9] > {ZIP_NAME}
sha256sum {ZIP_NAME}
```

Compare the result with the joined ZIP SHA-256 in `inventory.json`. In OneOS,
import the reconstructed ZIP, which contains a single `system.img` entry.

This is a OneOS guest image. It contains no Poco firmware or kernel and has
not been tested on a phone.
"""


def package(image, metadata, out, target):
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".package.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix=".package-", dir=out) as temporary:
            staging = Path(temporary)
            archive = staging / ZIP_NAME
            with image.open("rb") as source:
                raw = image_pin(source, metadata)
                write_zip(source, archive)
            validate_zip(archive, raw)
            joined, parts = split_zip(archive, staging)
            archive.unlink()
            inventory = {
                "schema": 1,
                "target": target,
                "image": {"name": IMAGE_NAME, **raw},
                "source": {key: value for key, value in metadata.items() if key != "image"},
                "zip": joined,
                "parts": parts,
            }
            (staging / INVENTORY).write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
            (staging / NOTES).write_text(release_notes(), encoding="utf-8")
            assets = {INVENTORY: file_pin(staging / INVENTORY), NOTES: file_pin(staging / NOTES)}
            assets.update({part["name"]: {key: part[key] for key in ("size", "sha256")} for part in parts})
            for existing in out.iterdir():
                if existing.name not in assets:
                    if existing.name == ZIP_NAME or existing.name.startswith(f"{ZIP_NAME}."):
                        raise PackageError(f"unexpected existing output: {existing}")
                    continue
                if existing.is_symlink() or not existing.is_file() or file_pin(existing) != assets[existing.name]:
                    raise PackageError(f"existing output differs: {existing}")
            for name in assets:
                destination = out / name
                if not destination.exists():
                    os.link(staging / name, destination)
    return assets


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def require_publish_context(environment, target):
    required = {"GITHUB_ACTIONS": "true", "CI": "true", "GITHUB_REPOSITORY": REPO,
                "GITHUB_REF": f"refs/heads/{BRANCH}", "GITHUB_SHA": target}
    if any(environment.get(key) != value for key, value in required.items()):
        raise PackageError(f"publishing requires GitHub Actions on {REPO} at {BRANCH}")
    if environment.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}:
        raise PackageError("publishing requires a push or manual workflow run")
    if not environment.get("GH_TOKEN", "").strip() or git("rev-parse", "HEAD") != target:
        raise PackageError("publishing requires GH_TOKEN and the checked-out workflow commit")
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise PackageError("publishing requires a clean checkout")


def gh(*args, timeout=60, check=True, **kwargs):
    return subprocess.run(["gh", *args], timeout=timeout, check=check, **kwargs)


def release(tag, out, target):
    api = f"repos/{REPO}/releases/tags/{tag}"
    result = gh("api", api, check=False, capture_output=True)
    if result.returncode:
        if b"HTTP 404" not in result.stderr:
            result.check_returncode()
        creation = gh("release", "create", tag, "--repo", REPO, "--target", target, "--prerelease",
                      "--title", "OneOS ColorOS 17 Twinity4c", "--notes-file", str(out / NOTES), check=False)
        result = gh("api", api, check=False, capture_output=True)
        if result.returncode:
            creation.check_returncode()
            result.check_returncode()
    details = json.loads(result.stdout)
    if (details.get("tag_name") != tag or details.get("draft") is not False
            or details.get("prerelease") is not True or type(details.get("id")) is not int):
        raise PackageError(f"expected a published prerelease for {tag}")
    ref = json.loads(gh("api", f"repos/{REPO}/git/ref/tags/{tag}", capture_output=True).stdout)
    commit = ref.get("object", {})
    if commit.get("type") != "commit" or commit.get("sha") != target:
        raise PackageError(f"release tag {tag} does not point to {target}")
    return details["id"]


def release_assets(release_id):
    path = f"repos/{REPO}/releases/{release_id}/assets?per_page=100"
    rows = gh("api", "--paginate", "--jq", ".[] | @json", path, capture_output=True).stdout.splitlines()
    assets = (json.loads(row) for row in rows)
    return {asset["name"]: asset for asset in assets}


def verify_asset(asset, expected, out):
    name = asset["name"]
    if asset.get("state") != "uploaded" or asset.get("size") != expected["size"]:
        raise PackageError(f"release asset has an unexpected state or size: {name}")
    digest = asset.get("digest")
    if digest:
        if digest != f"sha256:{expected['sha256']}":
            raise PackageError(f"release asset has a different SHA-256: {name}")
        return
    if type(asset.get("id")) is not int:
        raise PackageError(f"release asset has no download ID: {name}")
    with tempfile.TemporaryFile(dir=out) as fetched:
        gh("api", f"repos/{REPO}/releases/assets/{asset['id']}", "-H", "Accept: application/octet-stream",
           timeout=900, stdout=fetched)
        fetched.seek(0)
        if fingerprint(fetched) != expected:
            raise PackageError(f"downloaded release asset differs: {name}")


def verify_existing(existing, assets, out):
    unexpected = existing.keys() - assets.keys()
    if unexpected:
        raise PackageError(f"unexpected release assets: {', '.join(sorted(unexpected))}")
    for name, asset in existing.items():
        verify_asset(asset, assets[name], out)


def publish(out, target, assets):
    tag = f"twinity4c-{target[:12]}"
    release_id = release(tag, out, target)
    existing = release_assets(release_id)
    verify_existing(existing, assets, out)
    # The inventory goes up first so any retry must match the full image pin.
    for name in assets:
        if name in existing:
            continue
        result = gh("release", "upload", tag, str(out / name), "--repo", REPO, timeout=900, check=False)
        if result.returncode:
            current = release_assets(release_id)
            if name not in current:
                result.check_returncode()
            verify_asset(current[name], assets[name], out)
    current = release_assets(release_id)
    if current.keys() != assets.keys():
        raise PackageError(f"release {tag} is missing or has unexpected assets")
    verify_existing(current, assets, out)
    print(f"Verified {len(assets)} assets on https://github.com/{REPO}/releases/tag/{tag}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--image-metadata", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{40}", args.target):
        raise PackageError("expected a full 40-digit Git commit SHA")
    if args.publish:
        require_publish_context(os.environ, args.target)
    metadata = json.loads(args.image_metadata.read_text(encoding="utf-8"))
    out = args.out.resolve()
    assets = package(args.image, metadata, out, args.target)
    if args.publish:
        publish(out, args.target, assets)
    print(out / INVENTORY)


if __name__ == "__main__":
    try:
        main()
    except (PackageError, OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        sys.exit(f"twinity package: {error}")
