#!/usr/bin/env python3
"""Build and package the HyperOS 4 Madrid guest from the pinned Xiaomi release."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import fcntl
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
TAG = "xiaomi-18-pro-max-cn"
RELEASE = f"https://github.com/DeathThoughts/OneOS-test/releases/tag/{TAG}"
ZIP_NAME = "OneOS_HyperOS4_Madrid.zip"
BUILD = "OS4.0.30.0.XEOCNXM"
PROPERTIES = {"ro.mi.os.version.incremental": BUILD, "ro.product.mod_device": "madrid"}
PARTS = (
    (2_097_152_000, "52fa316f7c37770d71c2402c2c5b6abd8696d12483670256c176326cbdf774f1"),
    (2_097_152_000, "974a6e22007c84c8aa3ca570ce7bc48c1bd95b56af94f241c71b26bf5ee06e79"),
    (2_097_152_000, "bc0ad727820b80ea0d6ced68e57d19b9206ace65b3a6b3fb20dd4763329223ab"),
    (2_097_152_000, "476445457f3865a6683c53d50f297672a6e06eb53d452b2377214755b27a960b"),
    (2_097_152_000, "a7156f0005cb9fee15dbf3f26e487c6b31df6f998586e2e535a17883fe0bd911"),
    (2_097_152_000, "5eba7751bcd5ac71c5523eadc33a4cfc9e80db183967ec9aae7fac6db481c674"),
    (2_097_152_000, "97cffb27fd26af83699db3ec440ed819c610de0e51c08b47741d799550b6f691"),
    (1_407_464_956, "7111860defbb36d5c4db0020c3b87de994581dc9db61b240161d02bef3534b7e"),
)
PARTITIONS = {
    "system": (977_072_128, 0xC0CBD2B2),
    "system_ext": (815_341_568, 0x8E1E6383),
    "product": (5_665_886_208, 0x05D5E607),
    "mi_ext": (173_858_816, 0xE1AA988C),
}

# Reuse the split-stream reader and the single-image ZIP format from Twinity.
_image_tools = runpy.run_path(str(ROOT / "scripts/build-twinity-image.py"))
BuildError = _image_tools["BuildError"]
SplitReader = _image_tools["SplitReader"]
verify_stream = _image_tools["verify_stream"]
mystic_checkout = _image_tools["mystic_checkout"]
image_details = _image_tools["image_details"]
MYSTIC_GSI_REV = _image_tools["MYSTIC_GSI_REV"]
_package_tools = runpy.run_path(str(ROOT / "scripts/package-twinity-image.py"))
PackageError = _package_tools["PackageError"]
file_pin = _package_tools["file_pin"]
write_zip = _package_tools["write_zip"]
validate_zip = _package_tools["validate_zip"]


def source_parts():
    for index, (size, sha256) in enumerate(PARTS, start=1):
        yield f"{TAG}.zip.{index:03d}", size, sha256


def download_parts(directory):
    url = RELEASE.replace("/releases/tag/", "/releases/download/")
    directory.mkdir()

    def download(part):
        name, size, _ = part
        print(f"Downloading {name} ({size:,} bytes)", flush=True)
        subprocess.run([
            "curl", "--fail", "--silent", "--show-error", "--location", "--proto", "=https",
            "--proto-redir", "=https", "--connect-timeout", "30", "--max-time", "900",
            "--retry", "3", "--retry-all-errors", "--retry-max-time", "1200",
            "--max-filesize", str(size), "--output", str(directory / name), f"{url}/{name}",
        ], check=True)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(download, source_parts()))


def extract_partitions(source_dir, partition_dir):
    partition_dir.mkdir()
    with ExitStack() as stack:
        streams = []
        for name, size, sha256 in source_parts():
            source = stack.enter_context((source_dir / name).open("rb"))
            if os.fstat(source.fileno()).st_size != size:
                raise BuildError(f"{name}: expected {size:,} bytes")
            print(f"Verifying {name}", flush=True)
            verify_stream(source, size, sha256, name)
            streams.append((source, size))
        joined = stack.enter_context(io.BufferedReader(SplitReader(streams)))
        archive = stack.enter_context(zipfile.ZipFile(joined))
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise BuildError("duplicate names in the source ZIP")
        details = {}
        for partition, (size, crc) in PARTITIONS.items():
            name = f"{partition}.img"
            entry = archive.getinfo(name)
            if (entry.compress_type, entry.file_size, entry.compress_size, entry.CRC) != (
                zipfile.ZIP_STORED, size, size, crc
            ):
                raise BuildError(f"{name}: source ZIP metadata differs from the pin")
            print(f"Extracting {name} ({size:,} bytes)", flush=True)
            # Reading through EOF makes ZipExtFile check the entry CRC.
            with archive.open(entry) as source, (partition_dir / name).open("xb") as output:
                sha256 = verify_stream(source, size, None, name, output)
            details[partition] = {"size": size, "crc32": f"{crc:08x}", "sha256": sha256}
    return details


def check_props(text, expected, label):
    actual = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            actual[key] = value
    for key, value in expected.items():
        if actual.get(key) != value:
            raise BuildError(f"{label}: expected {key}={value}, found {actual.get(key)!r}")


def check_stock_build(mystic, partitions, work):
    extractor = mystic / "tools/bin/Linux/x86_64/extract.erofs"
    with tempfile.TemporaryDirectory(prefix="stock-props-", dir=work) as temporary:
        subprocess.run([
            str(extractor), "-i", str(partitions / "mi_ext.img"), "-X", "/etc/build.prop",
            "-o", temporary, "-s",
        ], check=True)
        prop = Path(temporary) / "mi_ext/etc/build.prop"
        check_props(prop.read_text(), PROPERTIES, "stock mi_ext")


def check_guest_build(path):
    props = subprocess.check_output(
        ["debugfs", "-R", "cat /system/build.prop", str(path)], text=True,
    )
    check_props(props, {**PROPERTIES, "ro.build.version.sdk": "37"}, "guest system")


def convert(mystic, partitions, name):
    command = [
        str(mystic / ".venv/bin/python"), "-u", str(ROOT / "scripts/mysticgsi-from-images.py"),
        "--drop", "--type=hyperos", name, str(partitions), ",".join(PARTITIONS),
    ]
    last_line = ""
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True, errors="replace") as process:
        for line in process.stdout:
            print(line, end="", flush=True)
            if line.strip():
                last_line = line.strip()
        if process.wait():
            raise BuildError("MysticGSI conversion failed")
    result = (mystic / last_line).resolve()
    output = (mystic / "out" / name).resolve()
    if result.parent != output or result.suffix != ".img" or not result.is_file():
        raise BuildError(f"unexpected MysticGSI output path: {last_line!r}")
    if any(partitions.iterdir()):
        raise BuildError("MysticGSI did not unpack and drop every selected partition")
    return result


def require_space(work, required, step):
    free = shutil.disk_usage(work).free
    if free < required:
        raise BuildError(f"{step} needs {required:,} free bytes in {work}, only {free:,} available")


def build(source, work):
    mystic = mystic_checkout()
    if work.stat().st_dev != mystic.stat().st_dev:
        raise BuildError("--work-dir and MysticGSI must share a filesystem for partition hard links")
    required = sum(size for size, _ in PARTITIONS.values()) + (1 << 30)
    if source is None:
        required += sum(size for size, _ in PARTS)
    require_space(work, required, "source preparation")

    with tempfile.TemporaryDirectory(prefix="madrid-", dir=work) as temporary:
        staging = Path(temporary)
        partitions = staging / "partitions"
        if source is None:
            with tempfile.TemporaryDirectory(prefix="release-", dir=staging) as download:
                source = Path(download) / "parts"
                download_parts(source)
                details = extract_partitions(source, partitions)
            print("Removed downloaded source parts after verified extraction", flush=True)
        else:
            details = extract_partitions(source, partitions)
        check_stock_build(mystic, partitions, staging)

        # A build-specific name avoids reusing the pinned helper's output or unpack trees.
        name = f"oneos-{staging.name}"
        image = staging / "system.img"
        try:
            result = convert(mystic, partitions, name)
            os.link(result, image)
        finally:
            for directory in ("tmp", "out"):
                path = mystic / directory / name
                if path.exists():
                    shutil.rmtree(path)
        check_guest_build(image)

        raw = image_details(image)
        size = raw["size"]
        # Reserve space for a worst-case deflate stream and its ZIP headers.
        require_space(work, size + size // 1000 + 1024, "ZIP packaging")
        metadata = {"image": {"name": image.name, **raw}}
        package = staging / ZIP_NAME
        with image.open("rb") as stream:
            write_zip(stream, package)
        validate_zip(package, raw)
        metadata.update({
            "source_release": RELEASE,
            "source_parts": [{"name": name, "size": size, "sha256": sha}
                             for name, size, sha in source_parts()],
            "partitions": details,
            "mysticgsi_commit": MYSTIC_GSI_REV,
            "device": "madrid",
            "android_sdk": 37,
            "hyperos_build": BUILD,
            "zip": {"name": ZIP_NAME, **file_pin(package)},
        })
        (staging / "inventory.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (staging / "partitions").rmdir()
        staging.rename(work / "result")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, help="use local release parts and retain them")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "out/madrid-image")
    args = parser.parse_args()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    with (work / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (work / "result").exists():
            raise BuildError("result already exists, use a fresh --work-dir")
        build(args.source_dir, work)
    print(work / "result" / ZIP_NAME)


if __name__ == "__main__":
    try:
        main()
    except (BuildError, PackageError, OSError, KeyError, ValueError, subprocess.SubprocessError,
            zipfile.BadZipFile) as error:
        sys.exit(f"Madrid image: {error}")
