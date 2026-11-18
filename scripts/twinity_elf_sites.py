#!/usr/bin/env python3
"""Conservative AArch64 ELF patch-site discovery for experimental OneOS guests.

Discover sites from executable references, not guessed offsets. Fail closed if
a build differs from the validated instruction shape.
"""
from __future__ import annotations

import re
import struct


def _segments(data: bytes):
    if data[:4] != b"\x7fELF" or data[4:6] != b"\x02\x01":
        raise ValueError("Expected little-endian ELF64")
    if struct.unpack_from("<H", data, 18)[0] != 183:
        raise ValueError("Expected AArch64 ELF")
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 54)
    if phentsize < 56 or phoff + phentsize * phnum > len(data):
        raise ValueError("Invalid ELF program headers")
    out = []
    for i in range(phnum):
        base = phoff + i * phentsize
        typ, flags, off, va, _, fs, _, _ = struct.unpack_from("<IIQQQQQQ", data, base)
        if typ == 1 and fs and off + fs <= len(data):
            out.append((off, off + fs, va, flags))
    if not out:
        raise ValueError("No loadable ELF segments")
    return out


def _file_for_va(segments, address):
    for start, end, va, _ in segments:
        if va <= address < va + end - start:
            return start + address - va
    return None


def _sign_extend(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def _adrp_target(insn, pc):
    imm = ((insn >> 29) & 3) | (((insn >> 5) & 0x7ffff) << 2)
    return (pc & ~0xfff) + (_sign_extend(imm, 21) << 12)


def _name_references(data: bytes, segments, name: bytes):
    """Find ADRP+ADD addressing the exact NUL-terminated string."""
    names = []
    start = 0
    while True:
        pos = data.find(b"\x00" + name + b"\x00", start)
        if pos < 0:
            break
        names.append(pos)
        start = pos + 1
    refs = []
    for a, b, va, flags in segments:
        if not flags & 1:
            continue
        for off in range(a, b - 7, 4):
            adrp, add = struct.unpack_from("<II", data, off)
            if adrp & 0x9f000000 != 0x90000000:
                continue
            reg = adrp & 31
            if add & 0xffc00000 != 0x91000000:
                continue
            if (add & 31) != reg or ((add >> 5) & 31) != reg:
                continue
            pc = va + off - a
            target = _adrp_target(adrp, pc) + ((add >> 10) & 0xfff)
            target_off = _file_for_va(segments, target)
            if target_off is not None and target_off - 1 in names:
                refs.append((off, target_off - 1))
    return refs


def resolve_vold_sites(data: bytes) -> tuple[int, int]:
    """Find the service name and guarded try_start branch in vold.

    The try_start instruction must match the previously studied TBZ->B
    transformation, otherwise we refuse to patch.
    """
    segs = _segments(data)
    candidates = []
    for ref_off, name_off in _name_references(data, segs, b"vold"):
        # In known vold builds the guarded call is nearby; do not patch an
        # unrelated TBZ in the binary merely because its bytes match.
        for off in range(ref_off + 8, min(ref_off + 100, len(data) - 3), 4):
            if data[off:off + 4] == bytes.fromhex("b3060036"):
                candidates.append((name_off, off))
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) != 1:
        raise ValueError(f"vold: expected one referenced service/try_start pair, got {len(candidates)}")
    return candidates[0]


def resolve_surfaceflinger_cbz_site(data: bytes) -> int:
    """Locate the guarded Qualcomm composer extension call shape."""
    _segments(data)
    if b"IQtiComposer3Client" not in data or b"AIBinder_getExtension" not in data:
        raise ValueError("surfaceflinger: expected Qualcomm composer symbols")
    candidates = []
    for a, b, _, flags in _segments(data):
        if not flags & 1:
            continue
        for off in range(a + 8, b - 7, 4):
            before = struct.unpack_from("<I", data, off - 8)[0]
            load = struct.unpack_from("<I", data, off - 4)[0]
            cbz = struct.unpack_from("<I", data, off)[0]
            after = struct.unpack_from("<I", data, off + 4)[0]
            if (before & 0xfc000000 == 0x94000000 and
                load == 0xf94007e8 and cbz == 0xb4000268 and
                after & 0xfc000000 == 0x94000000):
                candidates.append(off)
    if len(candidates) != 1:
        raise ValueError(f"surfaceflinger: expected one composer guard, found {len(candidates)}")
    return candidates[0]


def update_make_overlays_text(
    source: str, *, build_label: str, vold_sha: str, inc_sha: str,
    crash_apex_name: str, crash_sha: str, vold_name_off: int,
    vold_try_off: int, inc_name_off: int, sf_sha12: str, sf_cbz_off: int,
) -> str:
    """Register one hash-pinned build without accumulating duplicate entries."""
    for digest in (vold_sha, inc_sha, crash_sha):
        if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise ValueError("Invalid SHA256 digest")
    if not re.fullmatch(r"[a-fA-F0-9]{12}", sf_sha12):
        raise ValueError("Invalid surfaceflinger SHA1 prefix")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", crash_apex_name):
        raise ValueError("Invalid APEX filename")
    if not all(isinstance(x, int) and x >= 0 for x in
               (vold_name_off, vold_try_off, inc_name_off, sf_cbz_off)):
        raise ValueError("Invalid patch offset")
    if '"' in build_label or "\n" in build_label:
        raise ValueError("Invalid build label")
    name_patch = "NAME"
    entry = (
        f'    ("{build_label}", {{\n'
        f'        "vold": "{vold_sha.lower()}",\n'
        f'        "service.incremental.so": "{inc_sha.lower()}",\n'
        f'        "{crash_apex_name}": "{crash_sha.lower()}",\n'
        f'    }}, [("vold", {vold_name_off:#x}, {name_patch}), '
        f'("vold", {vold_try_off:#x}, TRY_START), '
        f'("service.incremental.so", {inc_name_off - 1:#x}, {name_patch})]),\n'
    )
    if vold_name_off < 1 or inc_name_off < 1:
        raise ValueError("Service string must have leading NUL")
    if f'"{build_label}"' not in source:
        needle = "BUILDS = [\n"
        if source.count(needle) != 1:
            raise ValueError("Cannot uniquely find BUILDS table")
        source = source.replace(needle, needle + entry, 1)
    site = f'"{sf_sha12.lower()}": {sf_cbz_off:#x}'
    if site not in source:
        # Do not quietly overwrite an existing hash with a different offset.
        if f'"{sf_sha12.lower()}":' in source:
            raise ValueError("Conflicting SurfaceFlinger site already registered")
        match = re.search(r"(?m)^(\\s*)SITES = \\{", source)
        if not match:
            raise ValueError("Cannot find SurfaceFlinger SITES table")
        loc = match.end()
        source = source[:loc] + site + ", " + source[loc:]
    return source
