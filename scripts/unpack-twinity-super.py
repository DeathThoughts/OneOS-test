#!/usr/bin/env python3
"""Use the pinned MysticGSI LP extractor when no standalone lpunpack exists.

Extract only allowlisted userspace partitions plus odm for the narrowly scoped
read-only AI library inspection. Never unpack Poco vendor or dlkm images.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MYSTIC = ROOT / "mysticgsi"
PARTITIONS = {
    "system", "system_ext", "product", "my_product", "my_engineering",
    "my_stock", "my_heytap", "my_company", "my_carrier", "my_region",
    "my_preload", "my_bigball", "my_manifest", "odm",
}


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: unpack-twinity-super.py super.img output-dir", file=sys.stderr)
        return 2
    src, dest = (Path(arg).resolve() for arg in sys.argv[1:])
    if not src.is_file():
        raise FileNotFoundError(src)
    if not (MYSTIC / "tools/extractor/formats/super.py").is_file():
        raise RuntimeError("Pinned MysticGSI source checkout is required for LP extraction")
    sys.path.insert(0, str(MYSTIC))
    from tools.extractor.formats.super import unpack_super  # noqa: E402

    found = unpack_super(str(src), str(dest), target_partitions=PARTITIONS, logger=print)
    if not any(Path(p).stem in {"system", "system_a"} for p in found):
        raise RuntimeError("super.img did not provide a nonempty system partition")
    print(f"Extracted {len(found)} allowlisted logical partition images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
