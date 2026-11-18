#!/usr/bin/env python3
"""Validate Twinity4c overlays and publish their content-addressed payloads."""
import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "app-android/packages"
REPO = "DeathThoughts/OneOS-test"
TAG = "fork-payloads-v1"
REMOTE = f"gh:{REPO}:{TAG}"
RELEASE = f"https://github.com/{REPO}/releases/download"
MAX_PAYLOAD = 256 * 1024 * 1024
CHUNK = 1024 * 1024
SHA_LINE = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9_][A-Za-z0-9._+-]*)")


@dataclass(frozen=True)
class Pin:
    size: int
    sha: str


LEGACY_ASSETS = {
    "OneOSGuest.apk": Pin(7471127, "bd03c424877de86c2864313ba3371d3725b3d5531559373aadc84d592f9cae0c"),
    "samcam_bar.tar": Pin(78028800, "e858ae5759ebb5ca259c3c895e5d4aa787b006ead6221c1f1fede22721c0b80f"),
}


def file_pin(path):
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        return Pin(size, hashlib.file_digest(stream, "sha256").hexdigest())


def check_pin(actual, expected, name):
    if actual != expected:
        raise ValueError(f"{name}: expected {expected.size} bytes and SHA-256 {expected.sha}, "
                         f"got {actual.size} bytes and SHA-256 {actual.sha}")


def copy_pinned(src, dst, expected, name):
    digest = hashlib.sha256()
    size = 0
    while chunk := src.read(CHUNK):
        size += len(chunk)
        if size > expected.size:
            raise ValueError(f"{name} exceeds its {expected.size}-byte limit")
        digest.update(chunk)
        dst.write(chunk)
    check_pin(Pin(size, digest.hexdigest()), expected, name)
    print(f"Verified {name} ({size} bytes)", flush=True)


def download(url, output, size):
    # Fetch retries use the stable release URL so curl gets a fresh redirect.
    subprocess.run([
        "curl", "--fail", "--location", "--silent", "--show-error", "--proto", "=https",
        "--proto-redir", "=https", "--connect-timeout", "30", "--max-time", "900",
        "--retry", "3", "--retry-all-errors", "--retry-max-time", "1200",
        "--max-filesize", str(size), "--output", output, url,
    ], check=True)


BRANCH = "test/coloros-twinity-4c"
SET_NAME = "twinity-s23"
IMAGE_SIZE = 10_238_480_384
SHA256 = re.compile(r"[0-9a-f]{64}")
GIT_SHA = re.compile(r"[0-9a-f]{40}")
GUARDED = frozenset({
    "services_patched.jar", "oplus-services_patched.jar", "vold_patched",
    "service.incremental_patched.so", "framework_patched.jar", "telephony-common_patched.jar",
    "libcamera_client_patched.so", "oplus-framework_patched.jar",
})
GUARD_WORDS = frozenset({
    "storage", "installd", "power", "sysrq", "weaver", "biometric", "biowrites",
    "gatekeeper", "pdb", "apexd", "appid15000", "nousers", "nativeservices",
    "wipesecrets", "ksuappid", "secims",
})
GENERATED = GUARDED | {
    "ims-ext-common_patched.jar", "oplus-telephony-common_patched.jar",
    "surfaceflinger_patched", "guards.txt",
} | {
    f"{name}_payload.img" for name in (
        "art", "i18n", "runtime", "sdkext", "crashrecovery", "extservices", "permission",
        "wifi", "appsearch", "mediaprovider", "adservices", "rkpd", "healthfitness",
        "ondevicepersonalization", "configinfrastructure",
    )
}
BUILD_ONLY = {
    "build-info.txt", "tethering_payload.img", "OneOSWallpapersFoldGrid.apk",
    "OneOSTabletQs.apk", "OneOSFoldCutout.apk",
}


def validate_files(directory, expected):
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise ValueError(f"Expected a directory: {directory}")
    entries = {path.name: path for path in directory.iterdir()}
    missing = expected - entries.keys()
    unexpected = entries.keys() - expected
    if missing or unexpected:
        raise ValueError(f"Payload names differ: missing {sorted(missing)}, unexpected {sorted(unexpected)}")
    for path in entries.values():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_PAYLOAD:
            raise ValueError(f"Expected a regular 1–{MAX_PAYLOAD}-byte file: {path}")
    return entries


def validate_guards(path, pins):
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0] if lines else ""
    missing_words = GUARD_WORDS - set(re.findall(r"\b\w+\b", header))
    if not header.startswith("#") or missing_words:
        raise ValueError(f"Invalid guards.txt header; missing guard names: {sorted(missing_words)}")

    covered = set()
    for line in lines[1:]:
        if not line.strip() or line.startswith("#"):
            continue
        match = SHA_LINE.fullmatch(line)
        if not match:
            raise ValueError(f"Invalid guards.txt line: {line!r}")
        sha, name = match.groups()
        if name not in GUARDED or name in covered or pins[name].sha != sha:
            raise ValueError(f"Invalid guards.txt pin for {name}")
        covered.add(name)
    if covered != GUARDED:
        raise ValueError(f"Missing guards.txt pins: {sorted(GUARDED - covered)}")


def validate_image_metadata(provenance):
    image = provenance.get("image") if isinstance(provenance, dict) else None
    if not isinstance(image, dict):
        raise ValueError("Missing image pin in builder metadata")
    path, size, digest = image.get("path"), image.get("size"), image.get("sha256")
    if (not isinstance(path, str) or not Path(path).is_absolute() or type(size) is not int
            or size != IMAGE_SIZE or not isinstance(digest, str) or not SHA256.fullmatch(digest)):
        raise ValueError("Invalid image path, size, or SHA-256 in builder metadata")

    builder = runpy.run_path(str(ROOT / "scripts/build-twinity-image.py"))
    expected = builder["metadata"](Path(path), {"size": size, "sha256": digest})
    for key, value in expected.items():
        if provenance.get(key) != value:
            raise ValueError(f"Image provenance differs from pinned builder metadata: {key}")
    if provenance.keys() != expected.keys():
        raise ValueError("Unexpected keys in builder metadata")


def index_rows(path):
    return dict(line.split(None, 1) for line in path.read_text().splitlines()
                if line.strip() and not line.startswith("#"))


def write_metadata(metadata, pins, target, build_info, provenance, guards):
    sizes = index_rows(PACKAGES / "sizes.txt")
    remotes = index_rows(PACKAGES / "remote.txt")
    sources = json.loads((ROOT / "scripts/patch-migration/sources.json").read_text())
    if sources["schema"] != 1:
        raise ValueError("Unsupported migration sources schema")

    unique = {}
    for pin in pins.values():
        if pin.sha in unique and unique[pin.sha] != pin.size:
            raise ValueError(f"Conflicting payload sizes for {pin.sha}")
        unique[pin.sha] = pin.size

    missing_sizes = []
    missing_remotes = []
    source_updates = {}
    for sha, size in sorted(unique.items()):
        if sha in sizes and int(sizes[sha]) != size:
            raise ValueError(f"Existing size differs for {sha}")
        if sha in remotes and remotes[sha] != REMOTE:
            raise ValueError(f"Existing remote differs for {sha}")

        current = sources["payloads"].get(sha)
        if current and current["bytes"] != size:
            raise ValueError(f"Existing migration source size differs for {sha}")
        if sha not in sizes:
            missing_sizes.append(f"{sha} {size}\n")
        if sha not in remotes:
            missing_remotes.append(f"{sha} {REMOTE}\n")
        if current is None:
            source_updates[sha] = {"bytes": size, "sources": [REMOTE]}
        elif REMOTE not in current["sources"]:
            source_updates[sha] = {"bytes": size, "sources": [REMOTE, *current["sources"]]}

    manifest = [
        f"# OneOS package set {SET_NAME}: overlays built from the pinned Twinity4c source.\n",
        *(f"{pin.sha}  {name}\n" for name, pin in sorted(pins.items())),
    ]
    inventory = {
        "target": target,
        "overlay_build": build_info,
        "release": REMOTE,
        "image": provenance["image"],
        "source": {key: value for key, value in provenance.items() if key != "image"},
        "source_part_urls": {part["name"]: f'{RELEASE}/images/{part["name"]}'
                             for part in provenance["source_parts"]},
        "payloads": {
            name: {**asdict(pin), "url": f"{RELEASE}/{TAG}/{pin.sha}",
                   "origin": "existing release" if name in LEGACY_ASSETS else "overlay build"}
            for name, pin in sorted(pins.items())
        },
    }

    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / f"{SET_NAME}.sha256").write_text("".join(manifest), encoding="utf-8")
    (metadata / f"{SET_NAME}.guards.txt").write_bytes(guards)
    (metadata / "sizes.additions.txt").write_text("".join(missing_sizes), encoding="utf-8")
    (metadata / "remote.additions.txt").write_text("".join(missing_remotes), encoding="utf-8")
    (metadata / "sources.additions.json").write_text(
        json.dumps({"schema": 1, "payloads": source_updates}, indent=2) + "\n", encoding="utf-8")
    (metadata / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")


def release_assets():
    api = f"repos/{REPO}/releases"
    release = json.loads(subprocess.check_output(["gh", "api", f"{api}/tags/{TAG}"], timeout=60))
    if release.get("tag_name") != TAG or release.get("draft") is not False or type(release.get("id")) is not int:
        raise RuntimeError(f"Expected the published {REPO} release {TAG}")
    pages = json.loads(subprocess.check_output([
        "gh", "api", "--paginate", "--slurp", f'{api}/{release["id"]}/assets?per_page=100',
    ], timeout=60))
    return {asset["name"]: asset for page in pages for asset in page}


def verify_asset(asset, expected, work):
    if asset.get("state") != "uploaded" or asset.get("size") != expected.size:
        raise RuntimeError(f"Release asset has an unexpected state or size: {expected.sha}")
    digest = asset.get("digest")
    if digest:
        if digest != f"sha256:{expected.sha}":
            raise RuntimeError(f"Release asset has a different digest: {expected.sha}")
    else:
        with tempfile.TemporaryDirectory(prefix="check-asset-", dir=work) as tmp:
            verified = Path(tmp) / expected.sha
            download(f"{RELEASE}/{TAG}/{expected.sha}", verified, expected.size)
            check_pin(file_pin(verified), expected, expected.sha)


def publish(overlays, pins, work):
    existing = release_assets()
    missing = {}
    seen = set()
    for name, pin in pins.items():
        if pin.sha in seen:
            continue
        seen.add(pin.sha)
        if pin.sha in existing:
            verify_asset(existing[pin.sha], pin, work)
        else:
            missing[pin.sha] = (name, pin)

    stage = work / "publish"
    stage.mkdir()
    for sha, (name, pin) in missing.items():
        with (overlays / name).open("rb") as src, (stage / sha).open("xb") as dst:
            copy_pinned(src, dst, pin, name)

    for sha, (_, pin) in missing.items():
        result = subprocess.run([
            "gh", "release", "upload", TAG, stage / sha, "--repo", REPO,
        ], timeout=300)
        if result.returncode:
            # Another uploader may have added the same hash. Check its bytes.
            current = release_assets()
            if sha not in current:
                result.check_returncode()
            verify_asset(current[sha], pin, work)

    if missing:
        current = release_assets()
        for sha, (_, pin) in missing.items():
            if sha not in current:
                raise RuntimeError(f"Release is missing the uploaded asset: {sha}")
            verify_asset(current[sha], pin, work)
    print(f"Verified {len(seen)} hash-named assets on {REPO} release {TAG}", flush=True)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def require_publish_context(environment, target):
    required = {"GITHUB_REPOSITORY": REPO, "GITHUB_REF": f"refs/heads/{BRANCH}",
                "GITHUB_ACTIONS": "true", "CI": "true", "GITHUB_SHA": target}
    if not GIT_SHA.fullmatch(target) or any(
        environment.get(name) != value for name, value in required.items()
    ):
        raise ValueError(f"Publishing requires the {REPO} GitHub Actions build on {BRANCH}")
    if environment.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}:
        raise ValueError("Publishing requires a push or manual workflow run")
    if not environment.get("GH_TOKEN", "").strip() or git("rev-parse", "HEAD") != target:
        raise ValueError("Publishing requires GH_TOKEN and the checked-out workflow commit")
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Publishing requires a clean checkout")


def require_build_info(build_info, target):
    for key, value in (("commit", target), ("dirty", "0")):
        entries = [row for row in build_info.splitlines() if row.split()[:1] == [key]]
        if entries != [f"{key} {value}"]:
            raise ValueError("Publishing requires overlays built from the clean workflow commit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing-overlays", type=Path, required=True)
    parser.add_argument("--image-metadata", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, default=ROOT / "out/twinity-payload-metadata")
    parser.add_argument("--target", required=True, help="full Git commit SHA used to build the overlays")
    parser.add_argument("--publish", action="store_true", help="upload to the existing fork payload release")
    args = parser.parse_args()

    try:
        if not GIT_SHA.fullmatch(args.target):
            raise ValueError("Expected a full Git commit SHA")
        if args.publish:
            require_publish_context(os.environ, args.target)

        provenance = json.loads(args.image_metadata.read_text(encoding="utf-8"))
        validate_image_metadata(provenance)
        files = validate_files(args.existing_overlays, GENERATED | BUILD_ONLY)
        build_info = files["build-info.txt"].read_text(encoding="utf-8")
        if args.publish:
            require_build_info(build_info, args.target)
        pins = {name: file_pin(files[name]) for name in sorted(GENERATED)}
        validate_guards(files["guards.txt"], pins)

        with tempfile.TemporaryDirectory(prefix="oneos-twinity-payloads-") as tmp:
            work = Path(tmp)
            overlays = work / "payloads"
            overlays.mkdir()
            for name, pin in pins.items():
                with files[name].open("rb") as source, (overlays / name).open("xb") as destination:
                    copy_pinned(source, destination, pin, name)
            validate_guards(overlays / "guards.txt", pins)

            for name, pin in LEGACY_ASSETS.items():
                fetched = work / name
                download(f"{RELEASE}/{TAG}/{pin.sha}", fetched, pin.size)
                with fetched.open("rb") as source, (overlays / name).open("xb") as destination:
                    copy_pinned(source, destination, pin, name)
                pins[name] = pin

            write_metadata(args.metadata, pins, args.target, build_info, provenance,
                           (overlays / "guards.txt").read_bytes())
            if args.publish:
                publish(overlays, pins, work)
        print(f"Validated {len(pins)} payloads; metadata written to {args.metadata}", flush=True)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Twinity payload validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
