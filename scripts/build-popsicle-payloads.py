#!/usr/bin/env python3
"""Build the pinned Popsicle GSI's overlays and publish the validated payload set."""
import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile


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


IMAGE_PARTS = {
    "HyperOS.4.17.Pro.Max.zip.001": Pin(1999724809, "0545e55bc8265de7bfa9fb63e6a9339d58409f3a7251a9faef09a34ad6dce850"),
    "HyperOS.4.17.Pro.Max.zip.002": Pin(1999724538, "e3bba9083764efe1dd0472e23f1dff1a094d131b2114711d467617814129b904"),
}
WRAPPER = Pin(3999449347, "e27b664263777b8f25adcaf2f47ce164ded6422bef6a79fb432ce0e160872557")
SOURCE_NAME = "Hyperos_popsicle_17_17OS4_0_260909_233731301_QCPECN_S_AB_20260920.zip"
SOURCE = Pin(3999449075, "1603c41aceb505ae6c51349a51312e127433eb80cd5a297bbb49a6eb7e70caf9")
SYSTEM = Pin(7203971072, "a0d622cff955e907b89bf7691d6d12102587bf9333ad89e92aebee0fe57b12ba")

# app-android/packages/hyper-f8u.sha256 and sizes.txt. socket_exec is copied
# from the checkout by make-overlays.sh and must match the same published pin.
LEGACY_ASSETS = {
    "OneOSGuest.apk": Pin(7471127, "bd03c424877de86c2864313ba3371d3725b3d5531559373aadc84d592f9cae0c"),
    "samcam_bar.tar": Pin(78028800, "e858ae5759ebb5ca259c3c895e5d4aa787b006ead6221c1f1fede22721c0b80f"),
}
SOCKET_EXEC = Pin(889984, "5970952d6399034217bcc56bad6c00a0cb203ea184215ebf46ccd8075bd98482")


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


def extract_member(archive, output, name, encoding, crc, expected):
    with ZipFile(archive) as outer:
        members = outer.infolist()
        if len(members) != 1:
            raise ValueError(f"{archive.name} must contain exactly one ZIP member")
        item = members[0]
        if (item.filename != name or item.file_size != expected.size or item.CRC != crc
                or item.compress_type != encoding or item.flag_bits & 1):
            raise ValueError(f"{archive.name}: unexpected entry, size, CRC, or compression for {name}")
        # ZipExtFile validates the CRC at EOF. Use an explicit destination and
        # enforce the pinned size again while copying the decoded member.
        with outer.open(item) as src, output.open("xb") as dst:
            copy_pinned(src, dst, expected, name)


def prepare_image(work, source_dir):
    wrapper = work / "wrapper.zip"
    with wrapper.open("xb") as dst:
        for name, expected in IMAGE_PARTS.items():
            if source_dir is None:
                part = work / name
                download(f"{RELEASE}/images/{name}", part, expected.size)
            else:
                part = source_dir / name
            with part.open("rb") as src:
                copy_pinned(src, dst, expected, name)
            if source_dir is None:
                part.unlink()
    check_pin(file_pin(wrapper), WRAPPER, wrapper.name)
    source = work / "source.zip"
    extract_member(wrapper, source, SOURCE_NAME, ZIP_STORED, 0xFB39FB5D, SOURCE)
    wrapper.unlink()
    image = work / "system.img"
    extract_member(source, image, image.name, ZIP_DEFLATED, 0xD954E37D, SYSTEM)
    source.unlink()
    return image


def run_overlay_build(image, overlays, work, metadata):
    environment = os.environ.copy()
    environment["TMPDIR"] = str(work)
    if not environment.get("KEYSTORE"):
        keystore = work / "debug.keystore"
        subprocess.run([
            "keytool", "-genkeypair", "-keystore", keystore, "-storepass", "android", "-alias", "androiddebugkey",
            "-keypass", "android", "-dname", "CN=Android Debug,O=Android,C=US", "-keyalg", "RSA",
            "-keysize", "2048", "-validity", "10000",
        ], check=True)
        environment["KEYSTORE"] = str(keystore)
    with (metadata / "overlay-build.log").open("w", encoding="utf-8") as log:
        with subprocess.Popen([ROOT / "scripts/make-overlays.sh", image, overlays], cwd=ROOT, env=environment,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                              errors="replace") as process:
            for line in process.stdout:
                log.write(line)
                print(line, end="", flush=True)
            if process.wait():
                raise RuntimeError(f"make-overlays.sh failed (exit {process.returncode})")


def add_legacy_assets(overlays, work):
    check_pin(file_pin(overlays / "socket_exec"), SOCKET_EXEC, "socket_exec")
    for name, expected in LEGACY_ASSETS.items():
        downloaded = work / name
        download(f"{RELEASE}/{TAG}/{expected.sha}", downloaded, expected.size)
        with downloaded.open("rb") as src, (overlays / name).open("xb") as dst:
            copy_pinned(src, dst, expected, name)


BRANCH = "test/hyperos-popsicle"
MYSTICGSI_REF = "825b850dd8ddff11fdb8ed024034fc00e94b1451"

GUARDED = frozenset({
    "apprecovery.proinstaller_patched.jar", "framework_patched.jar",
    "libcamera_client_patched.so", "libsurfaceflinger_patched.so", "miui-framework_patched.jar",
    "miui-services_patched.jar", "miui-telephony-common_patched.jar", "miui-wifi-service_patched.jar",
    "service.incremental_patched.so", "services_patched.jar", "telephony-common_patched.jar", "vold_patched",
})
GENERATED = GUARDED | {
    "OneOSFoldCutout.apk", "OneOSPhoneInjection.apk", "guards.txt", "ims-ext-common_patched.jar",
    "libaudiohal_aidl_patched.so", "oneos-apex-requires.txt", "oneos-telephony.jar",
    "oneos-telephony.sha256", "socket_exec",
} | {
    f"{name}_payload.img" for name in (
        "adservices", "appsearch", "art", "bt", "configinfrastructure", "crashrecovery",
        "extservices", "gmssystem", "healthfitness", "i18n", "mediaprovider",
        "ondevicepersonalization", "permission", "rkpd", "runtime", "sdkext", "wifi",
    )
}
BUILD_ONLY = {"build-info.txt", "tethering_payload.img"}
PUBLISHED = GENERATED | LEGACY_ASSETS.keys()


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


def validate_payloads(overlays):
    files = validate_files(overlays, PUBLISHED)
    pins = {name: file_pin(files[name]) for name in sorted(files)}
    for name, expected in (LEGACY_ASSETS | {"socket_exec": SOCKET_EXEC}).items():
        check_pin(pins[name], expected, name)

    covered = set()
    for line in files["guards.txt"].read_text(encoding="utf-8").splitlines():
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
    return pins


def index_rows(path):
    return dict(line.split(None, 1) for line in path.read_text().splitlines()
                if line.strip() and not line.startswith("#"))


def write_metadata(metadata, pins, target, build_info):
    sizes = index_rows(PACKAGES / "sizes.txt")
    remotes = index_rows(PACKAGES / "remote.txt")
    sources = json.loads((ROOT / "scripts/patch-migration/sources.json").read_text())
    if sources["schema"] != 1:
        raise ValueError("Unsupported migration sources schema")

    unique = {pin.sha: pin.size for pin in pins.values()}
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
        "# OneOS package set hyper-popsicle: overlays built from the pinned Popsicle GSI "
        "by scripts/build-popsicle-payloads.py.\n",
        *(f"{pin.sha}  {name}\n" for name, pin in pins.items()),
    ]
    (metadata / "hyper-popsicle.sha256").write_text("".join(manifest), encoding="utf-8")
    (metadata / "sizes.additions.txt").write_text("".join(missing_sizes), encoding="utf-8")
    (metadata / "remote.additions.txt").write_text("".join(missing_remotes), encoding="utf-8")
    (metadata / "sources.additions.json").write_text(
        json.dumps({"schema": 1, "payloads": source_updates}, indent=2) + "\n", encoding="utf-8")
    inventory = {
        "target": target,
        "overlay_build": build_info,
        "release": REMOTE,
        "mysticgsi_tool_ref": MYSTICGSI_REF,
        "image": {
            "parts": {name: {"url": f"{RELEASE}/images/{name}", **asdict(pin)}
                      for name, pin in IMAGE_PARTS.items()},
            "wrapper_zip": asdict(WRAPPER),
            "source_zip": {"name": SOURCE_NAME, **asdict(SOURCE)},
            "system_img": asdict(SYSTEM),
        },
        "payloads": {
            name: {**asdict(pin), "url": f"{RELEASE}/{TAG}/{pin.sha}",
                   "origin": "existing release" if name in LEGACY_ASSETS else "overlay build"}
            for name, pin in pins.items()
        },
    }
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


def require_publish_context(environment, target, checkout):
    required = {"GITHUB_REPOSITORY": REPO, "GITHUB_REF": f"refs/heads/{BRANCH}",
                "GITHUB_ACTIONS": "true", "CI": "true", "GITHUB_SHA": target}
    if not target or not all(environment.get(name) == value for name, value in required.items()):
        raise ValueError(f"Publishing requires the {REPO} GitHub Actions build on {BRANCH}")
    if environment.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}:
        raise ValueError("Publishing requires a push or manual workflow run")
    if not environment.get("GH_TOKEN") or target != checkout:
        raise ValueError("Publishing requires GH_TOKEN and the checked-out workflow commit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--source-dir", type=Path, help="use local copies of the pinned split ZIP")
    mode.add_argument("--existing-overlays", type=Path, help="validate an existing local overlay build")
    parser.add_argument("--metadata", type=Path, default=ROOT / "out/popsicle-metadata")
    parser.add_argument("--target", help="commit being built (defaults to the current checkout)")
    parser.add_argument("--publish", action="store_true", help="upload to the existing fork payload release")
    args = parser.parse_args()

    try:
        checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        target = args.target or checkout
        if not re.fullmatch("[0-9a-f]{40}", target):
            raise ValueError("Expected a full Git commit SHA")
        if args.publish:
            if args.existing_overlays or args.source_dir:
                raise ValueError("Publishing requires a fresh build from the pinned release downloads")
            require_publish_context(os.environ, target, checkout)

        metadata = args.metadata.resolve()
        metadata.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="oneos-popsicle-") as tmp:
            work = Path(tmp)
            generated = args.existing_overlays or work / "overlays"
            if args.existing_overlays is None:
                image = prepare_image(work, args.source_dir)
                run_overlay_build(image, generated, work, metadata)
            files = validate_files(generated, GENERATED | BUILD_ONLY)
            build_info = files["build-info.txt"].read_text(encoding="utf-8")
            if args.publish and not {f"commit {target}", "dirty 0"} <= set(build_info.splitlines()):
                raise ValueError("Publishing requires overlays built from the clean workflow commit")
            overlays = work / "payloads"
            overlays.mkdir()
            for name in sorted(GENERATED):
                shutil.copyfile(files[name], overlays / name)
            add_legacy_assets(overlays, work)
            pins = validate_payloads(overlays)
            write_metadata(metadata, pins, target, build_info)
            if args.publish:
                publish(overlays, pins, work)
        print(f"Validated {len(pins)} payloads; metadata written to {metadata}", flush=True)
    except (BadZipFile, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Popsicle payload build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
