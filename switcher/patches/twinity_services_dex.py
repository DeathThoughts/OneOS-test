"""Make room for the guest guards in Twinity ColorOS 17's services.jar.

Twinity ships 65,535 method references in classes.dex. Move the four INetd
binder classes together into a fresh dex in the same services.jar and class
loader, preserving their descriptors and smali bytes.

Usage: twinity_services_dex.py <apktool decoded services.jar directory>
Prints the smali directories for the original dex and the fresh dex.
"""

import argparse
import os
from pathlib import Path
import re
import sys


SOURCE_DEX = "smali"
PACKAGE = Path("android/net")
FAMILY = (
    "INetd",
    "INetd$Default",
    "INetd$Stub",
    "INetd$Stub$Proxy",
)
DEX_DIR = re.compile(r"smali_classes([2-9]|[1-9][0-9]+)")
FAMILY_FILE = re.compile(r"INetd(?:\$[^/]*)?\.smali")
FAMILY_DESCRIPTOR = re.compile(rb"Landroid/net/INetd(?:\$[^/;]*)?;")
CLASS = re.compile(rb"^[ \t]*\.class(?=[ \t#\r\n]|$)[ \t]*([^#\r\n]*)")


def class_descriptors(lines):
    for line in lines:
        match = CLASS.match(line)
        if match:
            tokens = match[1].split()
            yield tokens[-1] if tokens else b""


def raise_walk_error(error):
    raise error


def move_family(decoded):
    if decoded.is_symlink():
        raise ValueError(f"decoded directory is a symlink: {decoded}")
    dex_dirs = {}
    for path in decoded.iterdir():
        numbered = DEX_DIR.fullmatch(path.name)
        if path.name != "smali" and numbered is None:
            continue
        if path.is_symlink():
            raise ValueError(f"dex directory is a symlink: {path}")
        if path.is_dir():
            dex_dirs[path] = int(numbered[1]) if numbered else 1

    source = decoded / SOURCE_DEX / PACKAGE
    expected = {source / f"{name}.smali": f"Landroid/net/{name};".encode() for name in FAMILY}
    found = set()
    for dex_dir in dex_dirs:
        for parent, dirs, files in os.walk(dex_dir, onerror=raise_walk_error):
            directory = Path(parent)
            for name in dirs:
                path = directory / name
                if path.is_symlink() or FAMILY_FILE.fullmatch(name):
                    raise ValueError(f"unexpected directory in decoded dex: {path}")
            for name in files:
                path = directory / name
                if path.is_symlink():
                    raise ValueError(f"unexpected symlink in decoded dex: {path}")
                if not name.endswith(".smali"):
                    continue
                if not path.is_file():
                    raise ValueError(f"smali source must be a regular non-symlink file: {path}")
                if path in expected:
                    descriptors = list(class_descriptors(path.read_bytes().splitlines()))
                    if descriptors != [expected[path]]:
                        raise ValueError(f"expected exactly one .class for {expected[path].decode()} in {path}")
                    found.add(path)
                else:
                    with path.open("rb") as smali:
                        if FAMILY_FILE.fullmatch(name) or any(
                            FAMILY_DESCRIPTOR.fullmatch(descriptor) for descriptor in class_descriptors(smali)
                        ):
                            raise ValueError(f"unexpected INetd family source: {path}")

    missing = expected.keys() - found
    if missing:
        names = ", ".join(sorted(path.name for path in missing))
        raise ValueError(f"missing {SOURCE_DEX} sources: {names}")
    destination = decoded / f"smali_classes{max(dex_dirs.values()) + 1}"
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"destination already exists: {destination}")

    destination.mkdir()
    target = destination / PACKAGE
    target.mkdir(parents=True)
    for path in expected:
        path.rename(target / path.name)
    return SOURCE_DEX, destination.name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decoded", type=Path)
    args = parser.parse_args()
    try:
        print(*move_family(args.decoded))
    except (OSError, ValueError) as error:
        sys.exit(f"twinity_services_dex: {error}")


if __name__ == "__main__":
    main()
