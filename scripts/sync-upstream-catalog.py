#!/usr/bin/env python3
"""Reconcile hash-keyed upstream package tables, not executable code.

Run from an in-progress merge of debug and upstream. The trusted workflow
copies this script out of its checkout before switching to the candidate.
Only remote.txt, sizes.txt and the fork's migration plan may be changed.
"""
import json
from pathlib import Path
import re
import subprocess
import sys

PACKAGES = Path("app-android/packages")
REMOTE = PACKAGES / "remote.txt"
SIZES = PACKAGES / "sizes.txt"
PLAN = Path("scripts/patch-migration/fork-payloads-v2.json")
FORK = "gh:DeathThoughts/OneOS-test:"
DEST = FORK + "fork-payloads-v2"
HASH = re.compile(r"[0-9a-f]{64}")
SOURCE = re.compile(r"(?:gh:(?:[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*:)?[a-z0-9][a-z0-9.-]{0,39}|sf|[A-Za-z0-9_-]{20,80})")
FORK_SOURCE = re.compile(re.escape(FORK) + r"[a-z0-9][a-z0-9.-]{0,39}")

def read_at(ref, path):
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], text=True)

def table(text):
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split(None, 1)
        if not HASH.fullmatch(key) or key in result:
            raise ValueError(f"Invalid or duplicate pin: {key}")
        result[key] = value.strip()
    return result

def active_hashes():
    active = set()
    for path in sorted(PACKAGES.glob("*.sha256")):
        # A set can reuse the same hash for multiple file names.
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, name = line.split(None, 1)
            if not HASH.fullmatch(key) or not name.strip():
                raise ValueError(f"Invalid pin in {path}: {line}")
            active.add(key)
    if not active:
        raise ValueError("No active package pins")
    return active

def reconcile(base, upstream):
    ours = table(read_at(base, REMOTE))
    theirs = table(read_at(upstream, REMOTE))
    sizes = table(read_at(base, SIZES))
    upstream_sizes = table(read_at(upstream, SIZES))
    plan = json.loads(read_at(base, PLAN))
    if plan["schema"] != 1 or not isinstance(plan["payloads"], dict):
        raise ValueError("Unsupported migration plan")
    for sha, size in upstream_sizes.items():
        if not size.isdecimal() or int(size) <= 0:
            raise ValueError(f"Invalid upstream size: {sha}")
        if sha in sizes and int(sizes[sha]) != int(size):
            raise ValueError(f"Same SHA has different size: {sha}")
        sizes.setdefault(sha, size)

    active = active_hashes()
    for sha in sorted(active):
        size = int(sizes.get(sha, "0"))
        if not 0 < size <= 2 * 1024**3:
            raise ValueError(f"No valid size for active pin {sha}")
        old = ours.get(sha, "")
        if FORK_SOURCE.fullmatch(old):
            continue
        sources = list(dict.fromkeys((theirs.get(sha, "") + "," + old).split(",")))
        sources = [s for s in sources if s and SOURCE.fullmatch(s)]
        if not sources:
            raise ValueError(f"No supported origin for {sha}")
        if sha in plan["payloads"] and plan["payloads"][sha]["bytes"] != size:
            raise ValueError(f"Migration plan size differs for {sha}")
        plan["payloads"][sha] = {"bytes": size, "sources": sources}
        ours[sha] = DEST
    for sha in active:
        if not FORK_SOURCE.fullmatch(ours[sha]):
            raise ValueError(f"Active pin is not fork-owned: {sha}")

    # Preserve historical fork references and their immutable pins; never
    # silently change the content of a SHA-named asset.
    REMOTE.write_text("# SHA-256 -> fork-owned pinned GitHub release asset.\n" +
                      "".join(f"{h} {v}\n" for h, v in sorted(ours.items())))
    SIZES.write_text("# SHA-256 -> exact payload size.\n" +
                     "".join(f"{h} {v}\n" for h, v in sorted(sizes.items())))
    PLAN.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    subprocess.run(["git", "add", "--", str(REMOTE), str(SIZES), str(PLAN)], check=True)
    print(f"Prepared {len(active)} active pins; {len(plan['payloads'])} recorded migration entries")

if __name__ == "__main__":
    if len(sys.argv) != 3 or not all(re.fullmatch(r"[0-9a-f]{40}", x) for x in sys.argv[1:]):
        raise SystemExit("Usage: sync-upstream-catalog.py BASE_SHA UPSTREAM_SHA")
    reconcile(*sys.argv[1:])
