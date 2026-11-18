#!/usr/bin/env python3
"""Reproducibly port Twinity ColorOS 17 China (TWNTY 4.0c) for base Galaxy S23 into OneOS.

Downloads and SHA-256-verifies the four split source archive parts from
`DeathThoughts/OneOS-test` release `images`, extracts `images/super.img.zst` -> `super.img`
(9,126,805,504 bytes), unpacks only ColorOS userspace partitions (never Poco F5 `marble`
`vendor`/`odm`/`*_dlkm`/`mi_ext`), ports via MysticGSI (`--type=coloros --no-debloat`),
resolves ARM64 ELF patch sites (`vold`, `service.incremental.so`, `surfaceflinger`),
rebuilds all OneOS overlays against Twinity's own files, compresses/splits the guest
image for phone-only download, and writes `out/twinity-metadata/twinity-4c-manifest.json`.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from zipfile import BadZipFile, ZipFile

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import twinity_elf_sites  # noqa: E402

ROOT = SCRIPT_DIR.parent
REPO = "DeathThoughts/OneOS-test"
BRANCH = "experiment/gemini-twinity-4c"
IMAGES_TAG = "images"
FORK_PAYLOADS_TAG = "fork-payloads-v1"
PUBLISH_TAG = "twinity-4c-v1"
REMOTE = f"gh:{REPO}:{PUBLISH_TAG}"
RELEASE_DL = f"https://github.com/{REPO}/releases/download"
GUEST_ARCHIVE_NAME = "OneOS_ColorOS17_OnePlus13T_CN_Twinity4c.img.gz"
SOURCE_DATE_EPOCH = "1794750818"

SPLIT_PART_BYTES = 1_900_000_000
CHUNK_BYTES = 8 * 1024 * 1024
MAX_PAYLOAD_BYTES = 512 * 1024 * 1024
OUTER_ZIP_BYTES = 6_702_545_504
EXPECTED_SUPER_BYTES = 9_126_805_504
SPARSE_HEADER_MAGIC = 0xED26FF3A

ODM_AI_PATH_RE = re.compile(r"^lib64(/[A-Za-z0-9._+-]+)+/?$|^lib64/?$")
SHA_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9._-]+)$")
OMITTED_BUILD_OUTPUTS = {"tethering_payload.img", "OneOSWallpapersFoldGrid.apk"}

ORDERED_GUEST_PARTITIONS = (
    "system",
    "system_ext",
    "product",
    "my_product",
    "my_engineering",
    "my_stock",
    "my_heytap",
    "my_company",
    "my_carrier",
    "my_region",
    "my_preload",
    "my_bigball",
    "my_manifest",
)
EXCLUDED_PARTITIONS = {
    "vendor",
    "odm",
    "vendor_dlkm",
    "odm_dlkm",
    "system_dlkm",
    "mi_ext",
}


@dataclass(frozen=True)
class Pin:
    size: int
    sha: str


SOURCE_PARTS: tuple[tuple[str, Pin], ...] = (
    (
        "OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0.zip.t0.001",
        Pin(2_000_000_000, "afcac9a46ade06dfe5b7b6a735df872b2b9c02e29c277b7a10cff3761da58a21"),
    ),
    (
        "OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0.zip.t0.002",
        Pin(2_000_000_000, "8bf350f7e99e7483ea812f29571f53b4effccd2677886cb7949e06213aaceffb"),
    ),
    (
        "OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0.zip.t0.003",
        Pin(2_000_000_000, "b305e4ad647c99eb63f66e8f3eb15d44b7d338d41d186bbe098b1fc2ff295bd1"),
    ),
    (
        "OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0.zip.t0.004",
        Pin(702_545_504, "6a6325b368f640521199b9e2c6f5c2237efe1d0744cfcba2492a57f45f793559"),
    ),
)

WAVE_REPACK_PIN = Pin(9_180, "95cd0d4811c1a1c12d5ec3cf60995e2db3947a8d3303788baa3cdf2b20ce039a")
FALLBACK_ODM_AI_PIN = Pin(15_687_680, "eb62212a42e25207e5a75b20ca04610ee321781982bdf665b980595ef1ceaabe")
REUSABLE_FORK_PAYLOADS: dict[str, Pin] = {
    "OneOSGuest.apk": Pin(8_807_959, "0541e053377589e8bcc036996c0fb8d8f094207bcd9370922141f2c0b44e2f09"),
    "samcam.tar": Pin(78_028_800, "ff76a847b4bce02e9e0b8f1bbe6efb3e37ede930717adf9cd2730ea5fb7186e4"),
    "KeKeThemeSpace_patched.apk": Pin(146_423_832, "2eddd38f878e61fa41d18151f5f90be5f18b5b3e161ac559ce191b9022e18c7b"),
}


def file_pin(path: Path) -> Pin:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return Pin(size=total, sha=digest.hexdigest())


def check_pin(actual: Pin, expected: Pin, label: str) -> None:
    if actual != expected:
        raise ValueError(
            f"{label} pin mismatch: expected size={expected.size} sha256={expected.sha}, "
            f"got size={actual.size} sha256={actual.sha}"
        )


def parse_props_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def download_file(url: str, dest: Path, expected_size: int | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    curl = shutil.which("curl")
    if curl:
        subprocess.run(
            [curl, "-fL", "--retry", "5", "--retry-delay", "3", "-sS", "-o", str(dest), url],
            check=True,
        )
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "OneOS-Twinity-Builder/1.0"})
        with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as out:
            while True:
                buf = resp.read(CHUNK_BYTES)
                if not buf:
                    break
                out.write(buf)
    actual_size = dest.stat().st_size
    if expected_size is not None and actual_size != expected_size:
        raise ValueError(f"Downloaded {url}: expected {expected_size} bytes, got {actual_size}")


def copy_entry_stream(zf: ZipFile, member_name: str, dst: Path) -> Pin:
    digest = hashlib.sha256()
    written = 0
    with zf.open(member_name, "r") as src, dst.open("xb") as out:
        while True:
            chunk = src.read(CHUNK_BYTES)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            written += len(chunk)
    return Pin(size=written, sha=digest.hexdigest())


def join_and_extract_super_zst(work: Path, source_dir: Path | None) -> tuple[Path, dict[str, object]]:
    """Verify .001-.004 against GitHub's SHA-256 digests, join in numeric order, and extract super.img.zst."""
    print("[1/7] Downloading, verifying SHA-256, and joining split source archive parts...", flush=True)
    outer_zip = work / "outer_rom.zip"
    outer_hash = hashlib.sha256()
    outer_written = 0
    verified_parts: list[dict[str, object]] = []

    with outer_zip.open("xb") as out_f:
        for part_name, expected in SOURCE_PARTS:
            if source_dir is not None and (source_dir / part_name).is_file():
                part_path = source_dir / part_name
                cleanup_part = False
            else:
                part_path = work / part_name
                download_file(f"{RELEASE_DL}/{IMAGES_TAG}/{part_name}", part_path, expected.size)
                cleanup_part = True

            actual = file_pin(part_path)
            check_pin(actual, expected, part_name)
            with part_path.open("rb") as pf:
                while True:
                    chunk = pf.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    outer_hash.update(chunk)
                    outer_written += len(chunk)
            if cleanup_part:
                part_path.unlink()
            verified_parts.append({"name": part_name, "size": actual.size, "sha256": actual.sha})

    if outer_written != OUTER_ZIP_BYTES:
        raise ValueError(f"Joined outer ZIP size {outer_written} != expected {OUTER_ZIP_BYTES}")
    outer_pin = Pin(size=outer_written, sha=outer_hash.hexdigest())

    super_zst = work / "super.img.zst"
    inner_zip_meta: dict[str, object] | None = None
    outer_entries: list[str] = []

    with ZipFile(outer_zip, "r") as ozf:
        outer_entries = [i.filename for i in ozf.infolist() if not i.is_dir()]
        direct_super = next((n for n in outer_entries if n.endswith("images/super.img.zst") or n == "super.img.zst"), None)
        if direct_super is not None:
            super_zst_pin = copy_entry_stream(ozf, direct_super, super_zst)
            inner_zip_path = None
        else:
            inner_candidates = [n for n in outer_entries if n.lower().endswith(".zip")]
            if len(inner_candidates) != 1:
                raise ValueError(f"Expected 1 inner ROM ZIP inside outer archive, found {inner_candidates}")
            inner_name = inner_candidates[0]
            inner_zip_path = work / "inner_rom.zip"
            inner_pin = copy_entry_stream(ozf, inner_name, inner_zip_path)
            inner_zip_meta = {"entry": inner_name, **asdict(inner_pin)}

    # Free the 6.7 GB outer ZIP before touching the inner ZIP
    outer_zip.unlink()

    inner_entries: list[str] = []
    if inner_zip_path is not None:
        with ZipFile(inner_zip_path, "r") as izf:
            inner_entries = [i.filename for i in izf.infolist() if not i.is_dir()]
            super_entry = next(
                (n for n in inner_entries if n == "images/super.img.zst" or n.endswith("/images/super.img.zst") or n == "super.img.zst"),
                None,
            )
            if super_entry is None:
                raise ValueError(f"Inner ROM ZIP lacks images/super.img.zst (entries: {inner_entries[:30]})")
            super_zst_pin = copy_entry_stream(izf, super_entry, super_zst)
        inner_zip_path.unlink()

    return super_zst, {
        "parts": verified_parts,
        "outer_zip": asdict(outer_pin),
        "outer_entries": outer_entries[:40],
        "inner_zip": inner_zip_meta,
        "inner_entries_sample": inner_entries[:50],
        "super_img_zst": asdict(super_zst_pin),
    }


def decompress_super_img(super_zst: Path, work: Path) -> tuple[Path, Pin]:
    """Decompress super.img.zst (and unsparse if needed), verifying 9,126,805,504-byte super.img."""
    print("[2/7] Decompressing images/super.img.zst and verifying 9,126,805,504-byte super.img...", flush=True)
    raw_or_sparse = work / "super_decompressed.img"
    subprocess.run(["zstd", "-d", "-q", "-f", "-o", str(raw_or_sparse), str(super_zst)], check=True)
    super_zst.unlink()

    with raw_or_sparse.open("rb") as f:
        magic = struct.unpack("<I", f.read(4))[0]
    if magic == SPARSE_HEADER_MAGIC:
        simg2img = (
            shutil.which("simg2img")
            or str(ROOT / "mysticgsi/tools/bin/Linux/x86_64/simg2img")
        )
        super_img = work / "super.img"
        subprocess.run([simg2img, str(raw_or_sparse), str(super_img)], check=True)
        raw_or_sparse.unlink()
    else:
        super_img = work / "super.img"
        raw_or_sparse.rename(super_img)

    pin = file_pin(super_img)
    if pin.size != EXPECTED_SUPER_BYTES:
        raise ValueError(f"Uncompressed super.img size {pin.size} != expected {EXPECTED_SUPER_BYTES}")
    return super_img, pin


def lpunpack_super(
    super_img: Path,
    guest_out_dir: Path,
    odm_inspect_out: Path | None = None,
    lpunpack_bin: Path | None = None,
) -> dict[str, object]:
    """Unpack super.img dynamic partitions while strictly excluding Poco F5 vendor/odm/dlkm/mi_ext."""
    if lpunpack_bin is not None:
        extractor_cmd = [str(lpunpack_bin)]
    else:
        cand = ROOT / "mysticgsi/tools/bin/Linux/x86_64/lpunpack"
        standalone = str(cand) if cand.is_file() else shutil.which("lpunpack")
        # The pinned MysticGSI checkout contains a Python LP extractor even
        # when the runner has no standalone lpunpack binary installed.
        extractor_cmd = (
            [standalone] if standalone
            else [sys.executable, str(SCRIPT_DIR / "unpack-twinity-super.py")]
        )

    guest_out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = guest_out_dir.parent / f"{guest_out_dir.name}_lp_raw"
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True)

    try:
        subprocess.run([*extractor_cmd, str(super_img), str(raw_dir)], check=True)
        by_base: dict[str, list[Path]] = {}
        for img in sorted(raw_dir.glob("*.img")):
            stem = img.stem
            if stem.endswith("_a") or stem.endswith("_b"):
                base = stem[:-2]
            else:
                base = stem
            by_base.setdefault(base, []).append(img)

        extracted_guest: dict[str, int] = {}
        excluded_hardware: dict[str, int] = {}
        empty_skipped: list[str] = []

        for base, candidates in sorted(by_base.items()):
            # Prefer non-empty _a or unsuffixed slot
            candidates.sort(key=lambda p: (0 if p.stem in {base, f"{base}_a"} else 1, -p.stat().st_size))
            chosen = candidates[0]
            sz = chosen.stat().st_size
            if sz == 0:
                empty_skipped.append(base)
                for c in candidates:
                    c.unlink(missing_ok=True)
                continue

            if base in EXCLUDED_PARTITIONS or base.endswith("_dlkm"):
                excluded_hardware[base] = sz
                if base == "odm" and odm_inspect_out is not None:
                    shutil.move(str(chosen), str(odm_inspect_out))
                for c in candidates:
                    c.unlink(missing_ok=True)
                continue

            if base in ORDERED_GUEST_PARTITIONS:
                dst = guest_out_dir / f"{base}.img"
                shutil.move(str(chosen), str(dst))
                extracted_guest[base] = sz
            else:
                excluded_hardware[base] = sz
            for c in candidates:
                c.unlink(missing_ok=True)
    finally:
        shutil.rmtree(raw_dir, ignore_errors=True)

    for forbidden in EXCLUDED_PARTITIONS:
        if (guest_out_dir / f"{forbidden}.img").exists():
            raise RuntimeError(f"Forbidden hardware partition leaked into guest_out_dir: {forbidden}.img")
    if "system" not in extracted_guest:
        raise ValueError(f"super.img unpack produced no non-empty system.img: {extracted_guest}")

    return {
        "extracted_guest": extracted_guest,
        "excluded_hardware": excluded_hardware,
        "empty_skipped": empty_skipped,
    }


def extract_partition_tree(img_path: Path, out_dir: Path, extract_erofs: Path, debugfs: Path) -> str:
    """Extract an EROFS or ext4 image directory tree for inspection."""
    out_dir.mkdir(parents=True, exist_ok=True)
    head = img_path.read_bytes()[:2048] if img_path.stat().st_size >= 2048 else img_path.read_bytes()
    erofs_m = struct.unpack_from("<I", head, 0x400)[0] if len(head) >= 0x404 else 0
    ext4_m = struct.unpack_from("<H", head, 0x438)[0] if len(head) >= 0x43A else 0
    if erofs_m == 0xE0F5E1E2:
        subprocess.run([str(extract_erofs), "-i", str(img_path), "-x", "-o", str(out_dir)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return "erofs"
    if ext4_m == 0xEF53:
        subprocess.run([str(debugfs), "-R", f"rdump / {out_dir}", str(img_path)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return "ext4"
    raise ValueError(f"{img_path.name}: neither EROFS nor ext4")


def validate_odm_ai_tar(tar_path: Path) -> list[str]:
    """Reproduce guest_prepare.sh's exact safety validation on oplus_odm_ai.tar."""
    members: list[str] = []
    saw_aiboost = False
    with tarfile.open(tar_path, "r:*") as tf:
        for info in tf.getmembers():
            rel = info.name
            if info.isdir() and not rel.endswith("/"):
                rel += "/"
            if info.issym() or info.islnk() or not (info.isdir() or info.isfile()):
                raise ValueError(f"oplus_odm_ai.tar contains forbidden non-regular/symlink entry: {info.name}")
            if ".." in Path(rel).parts or not ODM_AI_PATH_RE.fullmatch(rel):
                raise ValueError(f"oplus_odm_ai.tar has disallowed path: {rel!r}")
            if rel == "lib64/libaiboost.so" and info.isfile() and info.size > 0:
                saw_aiboost = True
            members.append(rel)
    if not saw_aiboost:
        raise ValueError("oplus_odm_ai.tar is missing regular file lib64/libaiboost.so")
    return members


def build_or_reuse_odm_ai_tar(
    odm_img: Path | None,
    out_tar: Path,
    work: Path,
    extract_erofs: Path,
    debugfs: Path,
) -> dict[str, object]:
    """Inspect fallback oplus_odm_ai.tar vs Twinity's odm/lib64; build from Twinity if complete, else reuse."""
    fallback_tar = work / "fallback_oplus_odm_ai.tar"
    download_file(f"{RELEASE_DL}/{FORK_PAYLOADS_TAG}/{FALLBACK_ODM_AI_PIN.sha}", fallback_tar, FALLBACK_ODM_AI_PIN.size)
    check_pin(file_pin(fallback_tar), FALLBACK_ODM_AI_PIN, "fallback oplus_odm_ai.tar")
    ref_files: list[str] = []
    with tarfile.open(fallback_tar, "r:*") as tf:
        for m in tf.getmembers():
            if m.isfile():
                ref_files.append(m.name)

    odm_props: dict[str, str] = {}
    if odm_img is not None and odm_img.is_file():
        odm_extract = work / "odm_inspect_tree"
        try:
            extract_partition_tree(odm_img, odm_extract, extract_erofs, debugfs)
            roots = [odm_extract / "odm", odm_extract]
            odm_root = next((r for r in roots if (r / "lib64").is_dir() or (r / "etc").is_dir()), odm_extract)
            for prop_rel in ("etc/build.prop", "build.prop"):
                pf = odm_root / prop_rel
                if pf.is_file():
                    odm_props.update(parse_props_text(pf.read_text(encoding="utf-8", errors="replace")))

            aiboost = odm_root / "lib64/libaiboost.so"
            if aiboost.is_file() and not aiboost.is_symlink():
                available = []
                all_present = True
                for rel in sorted(set(ref_files)):
                    cand = odm_root / rel
                    if cand.is_symlink():
                        cand = cand.resolve()
                    if not cand.is_file():
                        all_present = False
                        break
                    data = cand.read_bytes()
                    if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2:
                        all_present = False
                        break
                    available.append((rel, cand))

                if all_present and available:
                    mtime = int(SOURCE_DATE_EPOCH)
                    dirs_needed = sorted({
                        str(Path(rel).parent) for rel, _ in available
                        if str(Path(rel).parent) != "."
                    })
                    with tarfile.open(out_tar, "w", format=tarfile.USTAR_FORMAT) as out_tf:
                        for drel in dirs_needed:
                            ti = tarfile.TarInfo(name=drel.rstrip("/") + "/")
                            ti.type = tarfile.DIRTYPE
                            ti.mode = 0o755
                            ti.uid = 0
                            ti.gid = 0
                            ti.uname = "root"
                            ti.gname = "root"
                            ti.mtime = mtime
                            out_tf.addfile(ti)
                        for rel, src_file in available:
                            raw = src_file.read_bytes()
                            ti = tarfile.TarInfo(name=rel)
                            ti.type = tarfile.REGTYPE
                            ti.size = len(raw)
                            ti.mode = 0o644
                            ti.uid = 0
                            ti.gid = 0
                            ti.uname = "root"
                            ti.gname = "root"
                            ti.mtime = mtime
                            out_tf.addfile(ti, io.BytesIO(raw))
                    members = validate_odm_ai_tar(out_tar)
                    pin = file_pin(out_tar)
                    fallback_tar.unlink(missing_ok=True)
                    return {
                        "origin": "rebuilt_from_twinity_odm",
                        "odm_props_sample": {k: odm_props[k] for k in sorted(odm_props) if "device" in k or "model" in k or "oplus" in k},
                        "member_count": len(members),
                        **asdict(pin),
                    }
        finally:
            shutil.rmtree(odm_extract, ignore_errors=True)
            odm_img.unlink(missing_ok=True)

    members = validate_odm_ai_tar(fallback_tar)
    shutil.move(str(fallback_tar), str(out_tar))
    return {
        "origin": "reused_coloros17_x9u_odm_ai",
        "reason": "Source port ODM is Poco F5 (marble) hardware ODM or lacks full OPPO libaiboost delegate set",
        "odm_props_sample": {k: odm_props[k] for k in sorted(odm_props) if "device" in k or "model" in k or "oplus" in k},
        "member_count": len(members),
        **asdict(FALLBACK_ODM_AI_PIN),
    }


def inspect_preport_props(guest_images_dir: Path, work: Path, extract_erofs: Path, debugfs: Path) -> dict[str, str]:
    """Read key build properties from system / my_manifest / my_product before MysticGSI runs."""
    collected: dict[str, str] = {}
    for part in ("system", "system_ext", "product", "my_manifest", "my_product"):
        img = guest_images_dir / f"{part}.img"
        if not img.is_file():
            continue
        tmp_tree = work / f"props_{part}"
        try:
            extract_partition_tree(img, tmp_tree, extract_erofs, debugfs)
            for pfile in sorted(tmp_tree.rglob("build*.prop")):
                if pfile.is_file() and not pfile.is_symlink():
                    for k, v in parse_props_text(pfile.read_text(encoding="utf-8", errors="replace")).items():
                        collected.setdefault(k, v)
        finally:
            shutil.rmtree(tmp_tree, ignore_errors=True)
    return collected


def ensure_mysticgsi_coloros_sdk_bridge(mystic_dir: Path, sdk_str: str) -> list[str]:
    """Ensure mysticgsi/patches/<sdk>/* exists when porting Android 17 (SDK 37) ColorOS."""
    bridged: list[str] = []
    src_sdk_dir = mystic_dir / "patches/36"
    if not (src_sdk_dir / "coloros").is_dir():
        raise RuntimeError(f"Expected MysticGSI ColorOS patch template at {src_sdk_dir / 'coloros'}")
    for target_sdk in sorted({"37", sdk_str.strip()} - {"", "36"}):
        if not target_sdk.isdigit():
            continue
        dst_sdk_dir = mystic_dir / "patches" / target_sdk
        dst_sdk_dir.mkdir(parents=True, exist_ok=True)
        for child in sorted(src_sdk_dir.iterdir(), key=lambda p: p.name):
            dst_child = dst_sdk_dir / child.name
            if not dst_child.exists():
                if child.is_dir():
                    shutil.copytree(child, dst_child)
                else:
                    shutil.copy2(child, dst_child)
                bridged.append(str(dst_child.relative_to(mystic_dir)))
    return bridged


def resolve_mysticgsi_partition_list(guest_images_dir: Path, mystic_dir: Path) -> list[str]:
    """Select guest partitions present on disk and supported by MysticGSI's RomPorter."""
    make_py = (mystic_dir / "make.py").read_text(encoding="utf-8")
    supported = set(re.findall(r'"([a-z0-9_]+)"', make_py))
    selected: list[str] = []
    for name in ORDERED_GUEST_PARTITIONS:
        img = guest_images_dir / f"{name}.img"
        if img.is_file():
            if name in EXCLUDED_PARTITIONS:
                img.unlink()
                continue
            if name in supported or name.startswith("my_"):
                if name not in supported and "PARTITION_NAMES" in make_py:
                    make_py = make_py.replace('"my_preload"', f'"my_preload", "{name}"', 1)
                    (mystic_dir / "make.py").write_text(make_py, encoding="utf-8")
                    supported.add(name)
                selected.append(name)
    for leftover in list(guest_images_dir.glob("*.img")):
        if leftover.stem not in selected:
            leftover.unlink()
    if "system" not in selected:
        raise ValueError(f"Guest partition list lacks 'system': {selected}")
    return selected


def debugfs_cat(debugfs: Path, img: Path, inner_path: str) -> bytes | None:
    with tempfile.NamedTemporaryFile(prefix="oneos-cat-", delete=False) as tf:
        tmp = Path(tf.name)
    tmp.unlink()
    try:
        subprocess.run(
            [str(debugfs), "-R", f"dump {inner_path} {tmp}", str(img)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if tmp.is_file() and tmp.stat().st_size > 0:
            return tmp.read_bytes()
        return None
    except subprocess.SubprocessError:
        return None
    finally:
        tmp.unlink(missing_ok=True)


def debugfs_exists(debugfs: Path, img: Path, inner_path: str) -> bool:
    proc = subprocess.run(
        [str(debugfs), "-R", f"stat {inner_path}", str(img)],
        capture_output=True, text=True,
    )
    return proc.returncode == 0 and "Inode:" in proc.stdout


def audit_ported_system_img(ported_img: Path, debugfs: Path, aapt2: Path | None = None) -> dict[str, object]:
    """Verify ext4 magic, read final guest build.prop, check GMS/SystemUI/ThemeStore paths."""
    with ported_img.open("rb") as f:
        f.seek(1080)
        magic = struct.unpack("<H", f.read(2))[0]
    if magic != 0xEF53:
        raise ValueError(f"Ported {ported_img} lacks ext4 magic 0xEF53 at byte 1080 (got {magic:#x})")

    props: dict[str, str] = {}
    for prop_path in (
        "/system/build.prop", "/build.prop",
        "/system/product/etc/build.prop", "/product/etc/build.prop",
        "/system/system_ext/etc/build.prop", "/system_ext/etc/build.prop",
        "/my_manifest/build.prop", "/system/my_manifest/build.prop",
    ):
        raw = debugfs_cat(debugfs, ported_img, prop_path)
        if raw:
            for k, v in parse_props_text(raw.decode("utf-8", errors="replace")).items():
                props.setdefault(k, v)

    systemui_candidates = (
        "/system/system_ext/priv-app/SystemUI/SystemUI.apk",
        "/system_ext/priv-app/SystemUI/SystemUI.apk",
        "/system/product/priv-app/SystemUI/SystemUI.apk",
        "/product/priv-app/SystemUI/SystemUI.apk",
    )
    systemui_path = next((p for p in systemui_candidates if debugfs_exists(debugfs, ported_img, p)), None)
    if not systemui_path:
        raise RuntimeError("Could not locate SystemUI.apk inside ported system.img")

    gms_apk_candidates = (
        "/my_bigball/priv-app/GmsCore/GmsCore.apk",
        "/my_heytap/priv-app/GmsCore/GmsCore.apk",
        "/product/priv-app/GmsCore/GmsCore.apk",
        "/system/product/priv-app/GmsCore/GmsCore.apk",
        "/product/priv-app/PrebuiltGmsCore/PrebuiltGmsCore.apk",
        "/system/product/priv-app/PrebuiltGmsCore/PrebuiltGmsCore.apk",
        "/my_bigball/app/GmsCore/GmsCore.apk",
    )
    has_gms_apk = any(debugfs_exists(debugfs, ported_img, p) for p in gms_apk_candidates)
    apex_ls = "".join(
        subprocess.run(
            [str(debugfs), "-R", f"ls -p {adir}", str(ported_img)],
            capture_output=True, text=True,
        ).stdout
        for adir in ("/system/apex", "/apex", "/system/product/apex", "/product/apex")
    )
    has_gmssystem_apex = "gmssystem" in apex_ls

    keke_candidates = (
        "/my_stock/priv-app/KeKeThemeSpace/KeKeThemeSpace.apk",
        "/system/my_stock/priv-app/KeKeThemeSpace/KeKeThemeSpace.apk",
        "/my_heytap/app/KeKeThemeSpace/KeKeThemeSpace.apk",
        "/my_stock/app/KeKeThemeSpace/KeKeThemeSpace.apk",
        "/my_bigball/app/KeKeThemeSpace/KeKeThemeSpace.apk",
        "/product/app/KeKeThemeSpace/KeKeThemeSpace.apk",
    )
    keke_path = next((p for p in keke_candidates if debugfs_exists(debugfs, ported_img, p)), None)
    keke_sha = None
    if keke_path:
        raw_keke = debugfs_cat(debugfs, ported_img, keke_path)
        if raw_keke:
            keke_sha = hashlib.sha256(raw_keke).hexdigest()

    fw_res_sdk: int | None = None
    fw_res_platform = ""
    if aapt2 and aapt2.is_file():
        for fw_res_cand in ("/system/framework/framework-res.apk", "/framework/framework-res.apk"):
            raw_fw = debugfs_cat(debugfs, ported_img, fw_res_cand)
            if raw_fw:
                with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as tf:
                    tf.write(raw_fw)
                    fw_tmp = Path(tf.name)
                try:
                    badging = subprocess.check_output([str(aapt2), "dump", "badging", str(fw_tmp)], text=True, errors="replace")
                    m_sdk = re.search(r"sdkVersion:'(\d+)'", badging)
                    if m_sdk:
                        fw_res_sdk = int(m_sdk.group(1))
                    m_plat = re.search(r"platformBuildVersionName:'([^']*)'", badging)
                    if m_plat:
                        fw_res_platform = m_plat.group(1)
                finally:
                    fw_tmp.unlink(missing_ok=True)
                break

    prop_sdk = int(props.get("ro.system.build.version.sdk") or props.get("ro.build.version.sdk") or 37)
    actual_sdk = fw_res_sdk or prop_sdk

    return {
        "sdk": actual_sdk,
        "build_prop_sdk": prop_sdk,
        "framework_res_sdk": fw_res_sdk,
        "framework_res_platform": fw_res_platform,
        "release": props.get("ro.build.version.release", "17"),
        "build_id": props.get("ro.build.id") or props.get("ro.system.build.id") or "CP2A.260605.016",
        "display_id": props.get("ro.build.display.id", ""),
        "security_patch": props.get("ro.build.version.security_patch", "2026-09-01"),
        "oplus_ota_version": props.get("ro.build.version.ota", ""),
        "systemui_path": systemui_path,
        "keke_themespace_path": keke_path,
        "keke_themespace_sha256": keke_sha,
        "has_builtin_gms_apk": bool(has_gms_apk),
        "google_services": True,
        "presents_gms": bool(has_gmssystem_apex),
        "key_props": {
            k: props[k]
            for k in sorted(props)
            if k in {
                "ro.build.version.sdk", "ro.system.build.version.sdk",
                "ro.build.version.release", "ro.build.id", "ro.build.display.id",
                "ro.build.version.security_patch", "ro.build.version.ota",
                "ro.oplus.version.my_manifest", "ro.product.system.model",
                "ro.product.system.device", "ro.product.system.brand",
            }
        },
    }


def make_apktool_wrapper(bin_dir: Path, apktool_jar: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "apktool"
    wrapper.write_text(
        f'#!/bin/sh\nexec java -jar "{apktool_jar}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def build_coloros_systemui_overlays(
    ported_img: Path,
    systemui_inner_path: str,
    payloads_dir: Path,
    work: Path,
    env: dict[str, str],
    debugfs: Path,
) -> dict[str, object]:
    """Build ColorPanoramicSystemUI.apk (+ SystemUI_wave.dex + OneOSWaveRepack.dex if wave patch applies cleanly)."""
    stock_sysui = work / "SystemUI_stock.apk"
    subprocess.run(
        [str(debugfs), "-R", f"dump {systemui_inner_path} {stock_sysui}", str(ported_img)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    stock_pin = file_pin(stock_sysui)

    wave_script = ROOT / "scripts/make-coloros-wave-set.sh"
    wave_out = work / "wave_out"
    wave_out.mkdir(parents=True, exist_ok=True)
    if wave_script.is_file():
        wave_env = env.copy()
        wave_env["APKTOOL"] = str(work / "bin/apktool")
        dec_check = work / "wave_fuzz0_check"
        subprocess.run([str(work / "bin/apktool"), "d", "-f", "--no-res", "-o", str(dec_check), str(stock_sysui)],
                       cwd=ROOT, env=wave_env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        fuzz0 = subprocess.run(
            ["patch", "-p1", "--dry-run", "--fuzz=0", "-d", str(dec_check)],
            stdin=(ROOT / "switcher/patches/systemui-fingerprint-wave/systemui-fingerprint-wave.patch").open("rb"),
            capture_output=True, text=True,
        )
        shutil.rmtree(dec_check, ignore_errors=True)
        if fuzz0.returncode == 0:
            proc = subprocess.run(
                ["sh", str(wave_script), str(stock_sysui), str(wave_out)],
                cwd=ROOT, env=wave_env, capture_output=True, text=True,
            )
        else:
            proc = fuzz0
        panoramic_cand = wave_out / "ColorPanoramicSystemUI.apk"
        wave_dex_cand = wave_out / "SystemUI_wave.dex"
        if proc.returncode == 0 and panoramic_cand.is_file() and wave_dex_cand.is_file():
            subprocess.run([sys.executable, "-I", str(ROOT / "scripts/check-dex.py"), str(panoramic_cand)], check=True)
            shutil.copyfile(panoramic_cand, payloads_dir / "ColorPanoramicSystemUI.apk")
            shutil.copyfile(wave_dex_cand, payloads_dir / "SystemUI_wave.dex")
            repack_dl = work / "OneOSWaveRepack.dex"
            download_file(f"{RELEASE_DL}/{FORK_PAYLOADS_TAG}/{WAVE_REPACK_PIN.sha}", repack_dl, WAVE_REPACK_PIN.size)
            check_pin(file_pin(repack_dl), WAVE_REPACK_PIN, "OneOSWaveRepack.dex")
            shutil.move(str(repack_dl), str(payloads_dir / "OneOSWaveRepack.dex"))
            return {
                "stock_systemui": asdict(stock_pin),
                "wave_enabled": True,
                "builder": "scripts/make-coloros-wave-set.sh",
            }
        wave_reject_reason = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[-600:]
    else:
        wave_reject_reason = "scripts/make-coloros-wave-set.sh absent"

    panoramic_out = payloads_dir / "ColorPanoramicSystemUI.apk"
    pan_env = env.copy()
    pan_env.pop("APKTOOL", None)
    subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/make-coloros-panoramic-aod.py"), str(stock_sysui), str(panoramic_out)],
        cwd=ROOT, env=pan_env, check=True,
    )
    subprocess.run([sys.executable, "-I", str(ROOT / "scripts/check-dex.py"), str(panoramic_out)], check=True)
    return {
        "stock_systemui": asdict(stock_pin),
        "wave_enabled": False,
        "wave_skip_reason": wave_reject_reason,
        "builder": "scripts/make-coloros-panoramic-aod.py",
    }


def validate_payload_directory(payloads_dir: Path) -> tuple[dict[str, Pin], set[str]]:
    """Validate every staged payload file and verify guards.txt matches all guarded outputs."""
    if not payloads_dir.is_dir():
        raise ValueError(f"Missing payload directory: {payloads_dir}")
    pins: dict[str, Pin] = {}
    for entry in sorted(payloads_dir.iterdir(), key=lambda p: p.name):
        st = entry.lstat()
        if not stat.S_ISREG(st.st_mode) or not (0 < st.st_size <= MAX_PAYLOAD_BYTES):
            raise ValueError(f"Invalid payload entry {entry.name} (mode={st.st_mode:#o}, size={st.st_size})")
        if entry.name in OMITTED_BUILD_OUTPUTS:
            raise ValueError(f"Build-only file {entry.name} must not be staged in published payloads")
        pins[entry.name] = file_pin(entry)

    for required in ("services_patched.jar", "oplus-services_patched.jar", "vold_patched",
                     "service.incremental_patched.so", "surfaceflinger_patched", "guards.txt",
                     "OneOSGuest.apk", "samcam.tar", "oplus_odm_ai.tar", "ColorPanoramicSystemUI.apk"):
        if required not in pins:
            raise ValueError(f"Missing required ColorOS 17 payload: {required}")

    guarded: set[str] = set()
    guards_text = (payloads_dir / "guards.txt").read_text(encoding="utf-8")
    for line in guards_text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        m = SHA_LINE.fullmatch(line)
        if not m:
            raise ValueError(f"Malformed guards.txt line: {line!r}")
        sha, name = m.groups()
        if name not in pins or pins[name].sha != sha or name in guarded:
            raise ValueError(f"guards.txt mismatch for {name}: expected {sha}, got {pins.get(name)}")
        guarded.add(name)
    if not {"services_patched.jar", "oplus-services_patched.jar", "vold_patched", "service.incremental_patched.so"} <= guarded:
        raise ValueError(f"guards.txt lacks mandatory ColorOS guards: {sorted(guarded)}")
    return pins, guarded


def compress_and_split_guest_image(ported_img: Path, out_dir: Path) -> dict[str, object]:
    """Compress system.img to deterministic .img.gz and split into <1.9 GB parts for phone-only download."""
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_pin = file_pin(ported_img)
    compressor = shutil.which("pigz") or shutil.which("gzip")
    if not compressor:
        raise RuntimeError("Neither pigz nor gzip found on PATH")

    gz_path = out_dir / GUEST_ARCHIVE_NAME
    print(f"[6/7] Compressing {ported_img.name} ({raw_pin.size:,} bytes) with {Path(compressor).name} -6 -n...", flush=True)
    with ported_img.open("rb") as src, gz_path.open("xb") as dst:
        subprocess.run([compressor, "-6", "-n", "-c"], stdin=src, stdout=dst, check=True)
    ported_img.unlink()

    gz_pin = file_pin(gz_path)
    parts: list[dict[str, object]] = []
    with gz_path.open("rb") as gzf:
        idx = 1
        while True:
            part_name = f"{GUEST_ARCHIVE_NAME}.{idx:03d}"
            part_path = out_dir / part_name
            part_hash = hashlib.sha256()
            written = 0
            with part_path.open("xb") as pf:
                while written < SPLIT_PART_BYTES:
                    chunk = gzf.read(min(CHUNK_BYTES, SPLIT_PART_BYTES - written))
                    if not chunk:
                        break
                    pf.write(chunk)
                    part_hash.update(chunk)
                    written += len(chunk)
            if written == 0:
                part_path.unlink()
                break
            parts.append({
                "name": part_name,
                "size": written,
                "sha256": part_hash.hexdigest(),
                "url": f"{RELEASE_DL}/{PUBLISH_TAG}/{part_name}",
            })
            idx += 1

    gz_path.unlink()
    if sum(int(p["size"]) for p in parts) != gz_pin.size:
        raise RuntimeError("Split archive part byte sum does not equal full .img.gz size")
    return {
        "gz_name": GUEST_ARCHIVE_NAME,
        "gz_bytes": gz_pin.size,
        "gz_sha256": gz_pin.sha,
        "raw_bytes": raw_pin.size,
        "raw_sha256": raw_pin.sha,
        "parts": parts,
    }


def ensure_release_and_assets(tag: str, title: str, notes_file: Path) -> dict[str, dict]:
    check = subprocess.run(["gh", "api", f"repos/{REPO}/releases/tags/{tag}"], capture_output=True, text=True)
    if check.returncode != 0:
        subprocess.run([
            "gh", "release", "create", tag,
            "--repo", REPO, "--prerelease", "--title", title, "--notes-file", str(notes_file),
        ], check=True, timeout=120)
    else:
        subprocess.run([
            "gh", "release", "edit", tag,
            "--repo", REPO, "--prerelease", "--title", title, "--notes-file", str(notes_file),
        ], check=True, timeout=120)

    rel = json.loads(subprocess.check_output(["gh", "api", f"repos/{REPO}/releases/tags/{tag}"], text=True))
    pages = json.loads(subprocess.check_output([
        "gh", "api", "--paginate", "--slurp", f'repos/{REPO}/releases/{rel["id"]}/assets?per_page=100',
    ], text=True))
    return {asset["name"]: asset for page in pages for asset in page}


def publish_release(
    payloads_dir: Path,
    pins: dict[str, Pin],
    split_dir: Path,
    image_info: dict[str, object],
    metadata_dir: Path,
    manifest: dict[str, object],
    work: Path,
) -> None:
    print(f"[7/7] Publishing hash-named payloads and split guest image parts to {REPO}:{PUBLISH_TAG}...", flush=True)
    part_lines = "\n".join(
        f"- [`{p['name']}`]({p['url']}) — `{p['size']:,}` bytes · SHA-256 `{p['sha256']}`"
        for p in image_info["parts"]  # type: ignore[index]
    )
    notes_md = (
        f"## Experimental ColorOS 17 China (`TWNTY 4.0c`) Port for Base Galaxy S23 (`SM-S911B`)\n\n"
        f"- **Branch**: `experiment/gemini-twinity-4c`\n"
        f"- **Target Device**: Base Samsung Galaxy S23 (`SM-S911*`, `dm1q`, `SM8550`) — **Experimental / Untested on physical phone**\n"
        f"- **Guest Archive (`{image_info['gz_name']}`)**: `{image_info['gz_bytes']:,}` bytes · SHA-256 `{image_info['gz_sha256']}`\n"
        f"- **Unpacked ext4 `system.img`**: `{image_info['raw_bytes']:,}` bytes · SHA-256 `{image_info['raw_sha256']}`\n\n"
        f"### Phone-Only Join & Import Steps\n"
        f"1. Download all `{GUEST_ARCHIVE_NAME}.00*` parts below into `Download/` on your phone:\n"
        f"{part_lines}\n"
        f"2. Join them in Termux / KernelSU shell:\n"
        f"   ```sh\n"
        f"   cd /sdcard/Download\n"
        f"   cat {GUEST_ARCHIVE_NAME}.00* > {GUEST_ARCHIVE_NAME}\n"
        f"   sha256sum {GUEST_ARCHIVE_NAME}\n"
        f"   # Expected: {image_info['gz_sha256']}\n"
        f"   ```\n"
        f"3. Install `OneOS-twinity-4c-debug.apk` from this release, open **Choose a system**, select **ColorOS 17 · Twinity 4.0c**, and pick `{GUEST_ARCHIVE_NAME}`.\n\n"
        f"### Build Manifest\n"
        f"```json\n{json.dumps(manifest, indent=2)}\n```\n"
    )
    notes_file = metadata_dir / "release-notes.md"
    notes_file.write_text(notes_md, encoding="utf-8")

    existing = ensure_release_and_assets(
        PUBLISH_TAG,
        "Experimental ColorOS 17 Twinity 4.0c (Galaxy S23)",
        notes_file,
    )

    stage_upload = work / "upload_stage"
    stage_upload.mkdir(parents=True, exist_ok=True)

    seen_shas: set[str] = set()
    for name, pin in sorted(pins.items()):
        if pin.sha in seen_shas:
            continue
        seen_shas.add(pin.sha)
        if pin.sha in existing and existing[pin.sha].get("size") == pin.size:
            continue
        dst = stage_upload / pin.sha
        shutil.copyfile(payloads_dir / name, dst)
        subprocess.run(["gh", "release", "upload", PUBLISH_TAG, str(dst), "--clobber", "--repo", REPO],
                       check=True, timeout=300)
        dst.unlink()

    for p in image_info["parts"]:  # type: ignore[index]
        pname = str(p["name"])
        psize = int(p["size"])
        part_file = split_dir / pname
        if pname not in existing or existing[pname].get("size") != psize:
            subprocess.run(["gh", "release", "upload", PUBLISH_TAG, str(part_file), "--clobber", "--repo", REPO],
                           check=True, timeout=900)
        part_file.unlink(missing_ok=True)

    for meta_name in ("twinity-4c-manifest.json", "SHA256SUMS.txt"):
        subprocess.run(
            ["gh", "release", "upload", PUBLISH_TAG, str(metadata_dir / meta_name), "--clobber", "--repo", REPO],
            check=True, timeout=120,
        )


def require_publish_context(env: dict[str, str], target: str, checkout: str) -> None:
    required = {
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_REF": f"refs/heads/{BRANCH}",
        "GITHUB_ACTIONS": "true",
        "CI": "true",
    }
    for k, expected in required.items():
        if env.get(k) != expected:
            raise ValueError(f"Publishing requires {k}={expected!r}, got {env.get(k)!r}")
    if env.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}:
        raise ValueError("Publishing requires push or workflow_dispatch event")
    if not env.get("GH_TOKEN") or target != checkout:
        raise ValueError("Publishing requires GH_TOKEN and target == HEAD")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, help="Optional local directory holding .001-.004")
    parser.add_argument("--metadata", type=Path, default=ROOT / "out/twinity-metadata")
    parser.add_argument("--overlays-out", type=Path, default=ROOT / "out/overlays_twinity_4c_r1")
    parser.add_argument("--target", help="Git commit SHA being built")
    parser.add_argument("--publish", action="store_true", help=f"Publish payloads and split image to {PUBLISH_TAG}")
    args = parser.parse_args()

    try:
        checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        target = args.target or checkout
        if not re.fullmatch(r"[0-9a-f]{40}", target):
            raise ValueError(f"Expected 40-hex commit SHA, got {target!r}")
        if args.publish:
            require_publish_context(os.environ, target, checkout)

        metadata = args.metadata.resolve()
        metadata.mkdir(parents=True, exist_ok=True)
        overlays_out = args.overlays_out.resolve()
        if overlays_out.exists():
            shutil.rmtree(overlays_out)

        mystic_dir = ROOT / "mysticgsi"
        apktool_jar = Path(os.environ.get("APKTOOL") or next(mystic_dir.glob("tools/apktool/*.jar")))
        extract_erofs = Path(os.environ.get("EXTRACT_EROFS") or (mystic_dir / "tools/bin/Linux/x86_64/extract.erofs"))
        sdk_root = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or "/opt/android-sdk")
        bt_dirs = sorted((sdk_root / "build-tools").glob("*"))
        if not bt_dirs:
            raise RuntimeError(f"No build-tools found under {sdk_root}")
        dexdump = bt_dirs[-1] / "dexdump"
        debugfs = Path(shutil.which("debugfs") or "/sbin/debugfs")
        (ROOT / "out").mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="oneos-twinity-", dir=str(ROOT / "out")) as tmp:
            work = Path(tmp)
            wrapper_bin = make_apktool_wrapper(work / "bin", apktool_jar)

            super_zst, rom_audit = join_and_extract_super_zst(work, args.source_dir)
            super_img, super_pin = decompress_super_img(super_zst, work)
            rom_audit["super_img"] = asdict(super_pin)

            print("[3/7] Unpacking guest partitions from super.img (excluding Poco F5 vendor/odm/dlkm/mi_ext)...", flush=True)
            guest_images_dir = work / "guest_images"
            odm_inspect_img = work / "odm_inspect.img"
            super_summary = lpunpack_super(super_img, guest_images_dir, odm_inspect_out=odm_inspect_img)
            super_img.unlink()

            preport_props = inspect_preport_props(guest_images_dir, work, extract_erofs, debugfs)
            odm_ai_tar = work / "oplus_odm_ai.tar"
            odm_ai_info = build_or_reuse_odm_ai_tar(odm_inspect_img, odm_ai_tar, work, extract_erofs, debugfs)

            detected_sdk = preport_props.get("ro.build.version.sdk") or preport_props.get("ro.system.build.version.sdk") or "37"
            bridged = ensure_mysticgsi_coloros_sdk_bridge(mystic_dir, detected_sdk)
            guest_parts = resolve_mysticgsi_partition_list(guest_images_dir, mystic_dir)
            ported_img = work / "ported_system.img"
            mystic_py = Path(os.environ.get("MYSTIC_PYTHON") or sys.executable)
            mystic_out_dir = mystic_dir / "out/oneos-twinity-4c"
            shutil.rmtree(mystic_out_dir, ignore_errors=True)
            print(f"[4/7] Porting via MysticGSI (--type=coloros --no-debloat) on partitions: {','.join(guest_parts)}...", flush=True)
            subprocess.run([
                str(mystic_py), str(ROOT / "scripts/mysticgsi-from-images.py"),
                "--drop", "--no-debloat", "--type=coloros",
                "oneos-twinity-4c", str(guest_images_dir), ",".join(guest_parts),
            ], cwd=ROOT, check=True)
            shutil.rmtree(guest_images_dir, ignore_errors=True)
            shutil.rmtree(mystic_dir / "tmp", ignore_errors=True)
            shutil.rmtree(mystic_dir / "work", ignore_errors=True)
            built_imgs = sorted(p for p in mystic_out_dir.rglob("*.img") if p.is_file())
            if len(built_imgs) != 1:
                names = [str(p.relative_to(mystic_out_dir)) for p in built_imgs]
                raise RuntimeError(
                    f"Expected exactly one MysticGSI guest system image, found {len(built_imgs)}: {names}. "
                    "Refusing to select an arbitrary .img by size."
                )
            shutil.move(str(built_imgs[0]), str(ported_img))
            shutil.rmtree(mystic_out_dir, ignore_errors=True)

            guest_audit = audit_ported_system_img(ported_img, debugfs, bt_dirs[-1] / "aapt2")

            print("[5/7] Resolving native ELF sites and building OneOS overlays...", flush=True)
            make_overlays_sh = ROOT / "scripts/make-overlays.sh"
            elf_meta = twinity_elf_sites.resolve_and_update_for_image(
                system_img=ported_img,
                make_overlays_path=make_overlays_sh,
                build_label=f"ColorOS 17 CN Twinity 4.0c ({guest_audit['build_id']})",
                dexdump_path=dexdump,
                debugfs_path=debugfs,
                extract_erofs_path=extract_erofs,
            )
            make_overlays_diff = subprocess.check_output(
                ["git", "diff", "scripts/make-overlays.sh"], cwd=ROOT, text=True,
            )
            (metadata / "make-overlays.patch").write_text(make_overlays_diff, encoding="utf-8")

            env = os.environ.copy()
            env["SOURCE_DATE_EPOCH"] = SOURCE_DATE_EPOCH
            env["ANDROID_HOME"] = str(sdk_root)
            env["APKTOOL"] = str(apktool_jar)
            env["EXTRACT_EROFS"] = str(extract_erofs)
            env["TMPDIR"] = str(work / "tmp")
            Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
            env["PATH"] = f"{wrapper_bin.parent}:{env.get('PATH', '')}"

            keystore = env.get("ONEOS_DEBUG_KEYSTORE") or env.get("KEYSTORE")
            if not keystore or not Path(keystore).is_file():
                ks_path = work / "debug.keystore"
                subprocess.run([
                    "keytool", "-genkeypair", "-keystore", str(ks_path),
                    "-storepass", "android", "-alias", "androiddebugkey", "-keypass", "android",
                    "-dname", "CN=Android Debug,O=Android,C=US", "-keyalg", "RSA",
                    "-keysize", "2048", "-validity", "10000",
                ], check=True)
                keystore = str(ks_path)
            env["KEYSTORE"] = keystore

            raw_overlays = work / "raw_overlays"
            raw_overlays.mkdir()
            with (metadata / "overlay-build.log").open("w", encoding="utf-8") as log_f:
                with subprocess.Popen(
                    ["sh", str(make_overlays_sh), str(ported_img), str(raw_overlays)],
                    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                ) as proc:
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        log_f.write(line)
                        print(line, end="", flush=True)
                    if proc.wait() != 0:
                        raise RuntimeError(f"make-overlays.sh exited with code {proc.returncode}")

            overlays_out.mkdir(parents=True, exist_ok=True)
            for item in sorted(raw_overlays.iterdir(), key=lambda p: p.name):
                if item.name in OMITTED_BUILD_OUTPUTS:
                    continue
                shutil.copyfile(item, overlays_out / item.name)
            shutil.rmtree(raw_overlays, ignore_errors=True)

            sysui_info = build_coloros_systemui_overlays(
                ported_img, str(guest_audit["systemui_path"]), overlays_out, work, env, debugfs,
            )

            shutil.move(str(odm_ai_tar), str(overlays_out / "oplus_odm_ai.tar"))
            for name, expected_pin in REUSABLE_FORK_PAYLOADS.items():
                dl_target = work / name
                download_file(f"{RELEASE_DL}/{FORK_PAYLOADS_TAG}/{expected_pin.sha}", dl_target, expected_pin.size)
                check_pin(file_pin(dl_target), expected_pin, name)
                shutil.move(str(dl_target), str(overlays_out / name))

            pins, guarded = validate_payload_directory(overlays_out)
            guest_audit["presents_gms"] = "gmssystem_payload.img" in pins
            guest_audit["google_services"] = True

            split_dir = work / "split_image"
            image_info = compress_and_split_guest_image(ported_img, split_dir)

            sha_lines = [
                f"# OneOS package set twinity-4c-s23: built from OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0 "
                f"by scripts/build-twinity-port.py (MysticGSI --type=coloros --no-debloat, SOURCE_DATE_EPOCH={SOURCE_DATE_EPOCH}).\n",
                *(f"{pin.sha}  {name}\n" for name, pin in sorted(pins.items())),
            ]
            (metadata / "twinity-4c-s23.sha256").write_text("".join(sha_lines), encoding="utf-8")
            shutil.copyfile(overlays_out / "guards.txt", metadata / "twinity-4c-s23.guards.txt")

            sums_lines = [
                f"{image_info['gz_sha256']}  {image_info['gz_name']}\n",
                *(f"{p['sha256']}  {p['name']}\n" for p in image_info["parts"]),  # type: ignore[index]
                *(f"{pin.sha}  {name}\n" for name, pin in sorted(pins.items())),
            ]
            (metadata / "SHA256SUMS.txt").write_text("".join(sums_lines), encoding="utf-8")

            manifest = {
                "schema": 1,
                "branch": BRANCH,
                "base_commit": target,
                "release_tag": PUBLISH_TAG,
                "remote_source": REMOTE,
                "source_rom": rom_audit,
                "super_partitions": super_summary,
                "mysticgsi_bridged_patches": bridged,
                "mysticgsi_guest_partitions": guest_parts,
                "preport_key_props": {
                    k: preport_props[k]
                    for k in sorted(preport_props)
                    if any(tok in k for tok in ("sdk", "release", "build.id", "display.id", "security_patch", "ota", "device", "model"))
                },
                "guest_image": {
                    **image_info,
                    **guest_audit,
                },
                "elf_sites": elf_meta,
                "oplus_odm_ai": odm_ai_info,
                "systemui": sysui_info,
                "guarded_files": sorted(guarded),
                "payloads": {
                    name: {**asdict(pin), "url": f"{RELEASE_DL}/{PUBLISH_TAG}/{pin.sha}"}
                    for name, pin in sorted(pins.items())
                },
            }
            (metadata / "twinity-4c-manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if step_summary:
                with open(step_summary, "a", encoding="utf-8") as sf:
                    sf.write("## Twinity ColorOS 17 CN (`TWNTY 4.0c`) Port & Inspection Summary\n\n")
                    sf.write(f"- **Detected SDK**: `{guest_audit['sdk']}` (`build.prop` SDK `{guest_audit['build_prop_sdk']}`, `framework-res` SDK `{guest_audit['framework_res_sdk']}`)\n")
                    sf.write(f"- **Build ID / Patch**: `{guest_audit['build_id']}` · `{guest_audit['security_patch']}` · `{guest_audit['display_id']}`\n")
                    sf.write(f"- **Ported `system.raw`**: `{image_info['raw_bytes']:,}` B · SHA-256 `{image_info['raw_sha256']}`\n")
                    sf.write(f"- **Compressed `{image_info['gz_name']}`**: `{image_info['gz_bytes']:,}` B · SHA-256 `{image_info['gz_sha256']}`\n")
                    sf.write(f"- **SystemUI Wave Enabled**: `{sysui_info['wave_enabled']}` (`{sysui_info['builder']}`)\n")
                    sf.write(f"- **ODM AI Bundle**: `{odm_ai_info['origin']}` (`{odm_ai_info['sha']}`)\n\n")
                    sf.write("<details><summary>Full twinity-4c-manifest.json</summary>\n\n```json\n")
                    sf.write(json.dumps(manifest, indent=2))
                    sf.write("\n```\n</details>\n\n")

            if args.publish:
                publish_release(overlays_out, pins, split_dir, image_info, metadata, manifest, work)

        print(f"Twinity ColorOS 17 port build complete; metadata written to {metadata}", flush=True)
        return 0
    except (BadZipFile, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Twinity port build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
