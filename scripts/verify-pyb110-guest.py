#!/usr/bin/env python3
"""Check the OPPO partitions and boot animation inside the packaged guest."""

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

import runpy


SOURCE = Path(__file__).with_name("prepare-pyb110-source.py")


def inspect(image, command):
    return subprocess.run(
        ["debugfs", "-R", command, str(image)], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ).stdout


def verify(image):
    partitions = runpy.run_path(str(SOURCE))["PARTITIONS"]
    for partition in partitions:
        if partition.startswith("my_"):
            details = inspect(image, f"stat /{partition}")
            if "Type: directory" not in details:
                raise ValueError(f"packaged guest is missing /{partition}")
            print(f"Verified /{partition} in packaged guest")
    for directory in ("/my_product/media/bootanimation", "/my_stock/media/bootanimation"):
        print(inspect(image, f"ls -l {directory}").strip())
    animation = "/my_product/media/bootanimation/bootanimation.zip"
    with tempfile.TemporaryDirectory(prefix="pyb110-bootanim-") as temporary:
        archive = Path(temporary) / "bootanimation.zip"
        dump = inspect(image, f"dump {animation} {archive}")
        if not archive.is_file():
            raise ValueError(f"packaged guest is missing {animation}: {dump.strip()}")
        with zipfile.ZipFile(archive) as artwork:
            if not artwork.read("desc.txt").strip():
                raise ValueError(f"{animation} has no animation descriptor")
            broken_entry = artwork.testzip()
            if broken_entry is not None:
                raise ValueError(f"{animation} has a corrupt member: {broken_entry}")
        with archive.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        print(f"Verified {animation}: {archive.stat().st_size} bytes, SHA-256 {digest}")


if __name__ == "__main__":
    try:
        verify(Path(sys.argv[1]))
    except (OSError, ValueError, zipfile.BadZipFile, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f"PYB110 guest: {error}")
