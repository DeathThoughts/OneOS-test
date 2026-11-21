#!/usr/bin/env python3
"""Extract the pinned OnePlus 16 ColorOS 17 guest inputs without downloading the OTA ZIP."""

import argparse
import hashlib
import importlib
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MYSTIC = ROOT / "mysticgsi"
MYSTIC_REV = "825b850dd8ddff11fdb8ed024034fc00e94b1451"
MIRROR = "https://share.deepstorage.dev/1793925203281/6d23ff0b95a248ca94259c990615ac2c_fwatlas.zip"
MIRROR_SIZE = 10_187_610_434
MIRROR_PAYLOAD_OFFSET = 86 + 61
OTA_SIZE = 10_187_609_378
OTA_PAYLOAD_OFFSET = 61
METADATA_SIZE = 377_893
METADATA_SHA256 = "7b900a45c63b7e72f4db70e80c96337307456f39245c0f764507af08f7152a6c"
PARTITIONS = {
    "system": (1_048_899_584, "44f4a69e1d8a8241d9e1dc1306215f542fb0b04b8fba72b0245f9ccc47da8999"),
    "system_ext": (934_428_672, "8a6b8b5d08cb50ca5c4efec3f776685921ea75db56bd56a6fa13dc5711b23360"),
    "product": (168_960_000, "f5f3a7ad6caa4080d7002e6d5d6fb718a6994cceea63474a0c4d042fc7166f3d"),
    "odm": (3_473_047_552, "43f55a24cd07989419c44cdc3b037ce320f2eb99c923673935261456aa0020fb"),
}


def partition_matches(path, size, digest):
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as image:
        return hashlib.file_digest(image, "sha256").hexdigest() == digest


def extract(out, ota):
    revision = subprocess.check_output(
        ["git", "-C", MYSTIC, "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != MYSTIC_REV:
        raise ValueError(f"MysticGSI revision {revision} differs from {MYSTIC_REV}")
    sys.path.insert(0, str(MYSTIC))
    payload = importlib.import_module("tools.extractor.formats.payload")
    stream = runpy.run_path(str(ROOT / "scripts/payload-stream.py"))

    if ota:
        if ota.stat().st_size != OTA_SIZE:
            raise ValueError("unexpected size for the original PYB110 OTA ZIP")
        source, offset = ota.open("rb"), OTA_PAYLOAD_OFFSET
    else:
        source = stream["HttpFile"](MIRROR, MIRROR_SIZE)
        offset = MIRROR_PAYLOAD_OFFSET

    with source:
        source.seek(offset)
        metadata = source.read(METADATA_SIZE)
        if hashlib.sha256(metadata).hexdigest() != METADATA_SHA256:
            raise ValueError("payload metadata SHA-256 differs from the supplied PYB110 OTA")
        missing = {
            name for name, (size, digest) in PARTITIONS.items()
            if not partition_matches(out / f"{name}.img", size, digest)
        }
        if not missing:
            return

        # Use the same random-access extractor for a local ZIP or for the
        # stored member in the mirror's outer ZIP. Check all outputs before moving them.
        def open_input(path, mode="r", *args, **kwargs):
            return source if path == "pyb110-ota" else open(path, mode, *args, **kwargs)

        def is_input(path, start=0):
            source.seek(start)
            return path == "pyb110-ota" and source.read(4) == b"CrAU"

        payload.open = open_input
        payload.is_payload = is_input
        with tempfile.TemporaryDirectory(prefix=".pyb110-", dir=out) as temporary:
            staging = Path(temporary)
            payload.extract_payload("pyb110-ota", temporary, missing, logger=print, offset=offset)
            for name in sorted(missing):
                image = staging / f"{name}.img"
                if not partition_matches(image, *PARTITIONS[name]):
                    raise ValueError(f"{name} digest or size differs from the OTA manifest")
            for name in sorted(missing):
                image = staging / f"{name}.img"
                image.replace(out / image.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--ota", type=Path, help="original ZIP to use in place of the mirror")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    extract(args.out, args.ota)
    for name, (size, digest) in PARTITIONS.items():
        print(f"{name} {size} {digest}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"PYB110 source: {error}")
