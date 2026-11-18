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
    """Find a unique service-name reference and validated try_start branch.

    The existing ColorOS builds keep the service registration string and
    try_start branch in different functions, sometimes megabytes apart.
    Never rely on their proximity or on fixed offsets.
    """
    segs = _segments(data)
    references = _name_references(data, segs, b"vold")
    names = sorted({name for _, name in references})
    if len(names) != 1:
        raise ValueError(f"vold: expected one referenced NUL-prefixed service name, found {len(names)}")

    candidates = []
    for a, b, _, flags in segs:
        if not flags & 1:
            continue
        for off in range(a + 4, b - 3, 4):
            if (data[off:off + 4] == bytes.fromhex("b3060036") and
                data[off - 4:off] == bytes.fromhex("f303002a")):
                candidates.append(off)
    if len(candidates) != 1:
        raise ValueError(f"vold: expected one validated try_start TBZ, found {len(candidates)}")
    return names[0], candidates[0]



def diagnose_vold_sites(data: bytes) -> dict[str, object]:
    """Bounded, nonpatching diagnostics for an unfamiliar AArch64 vold ELF.

    Return offsets and instruction words only; never publish copyrighted
    executable images. Nothing in this report authorizes a binary patch.
    """
    import hashlib

    segments = _segments(data)
    refs = _name_references(data, segments, b"vold")
    exact = []
    tbz_w19 = []
    mov_w19 = []
    for a, b, _, flags in segments:
        if not flags & 1:
            continue
        for off in range(a, b - 3, 4):
            inst = struct.unpack_from("<I", data, off)[0]
            if inst == 0x360006b3:
                exact.append(off)
            if (inst & 0x7f00001f) == 0x36000013:
                tbz_w19.append(off)
            if inst == 0x2a0003f3:
                mov_w19.append(off)
    mov_set = set(mov_w19)
    candidates = []
    for off in tbz_w19:
        near_mov = [p for p in (off - 4, off - 8, off - 12, off - 16)
                    if p in mov_set]
        if near_mov:
            candidates.append({
                "offset": f"0x{off:x}",
                "instruction": data[off:off + 4].hex(),
                "preceding_mov_offsets": [f"0x{x:x}" for x in near_mov],
                "window": data[max(0, off - 16):off + 8].hex(),
            })
    return {
        "warning": "DIAGNOSTIC ONLY. DO NOT PATCH WITHOUT INSTRUCTION FLOW ANALYSIS.",
        "vold_sha256": hashlib.sha256(data).hexdigest(),
        "file_size": len(data),
        "service_string_references": [{
            "adrp_offset": f"0x{ref:x}", "string_offset": f"0x{name:x}",
        } for ref, name in refs[:32]],
        "service_reference_count": len(refs),
        "expected_tbz_exact_count": len(exact),
        "expected_tbz_offsets": [f"0x{x:x}" for x in exact[:32]],
        "tbz_w19_count": len(tbz_w19),
        "mov_w19_count": len(mov_w19),
        "nearby_mov_tbz_candidate_count": len(candidates),
        "nearby_mov_tbz_candidates": candidates[:64],
        "truncated": len(candidates) > 64 or len(refs) > 32 or len(exact) > 32,
    }


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
        f'("service.incremental.so", {inc_name_off:#x}, {name_patch})]),\n'
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
        match = re.search(r"(?m)^(\s*)SITES = \{", source)
        if not match:
            raise ValueError("Cannot find SurfaceFlinger SITES table")
        loc = match.end()
        source = source[:loc] + site + ", " + source[loc:]
    return source

def resolve_and_update_for_image(
    *, system_img, make_overlays_path, build_label, dexdump_path,
    debugfs_path, extract_erofs_path,
):
    """Inspect a merged ext4 guest and register native patch sites.

    An unexpected binary aborts before modifying make-overlays.sh. The ELF
    resolvers are intentionally conservative, and passing synthetic unit tests
    does NOT certify a new ROM's native binaries or host isolation.
    """
    import hashlib
    import os
    from pathlib import Path
    import subprocess
    import tempfile

    image = Path(system_img)
    overlays = Path(make_overlays_path)
    if not image.is_file() or not overlays.is_file():
        raise FileNotFoundError("system.img or make-overlays.sh missing")
    with image.open("rb") as src:
        src.seek(1080)
        if src.read(2) != b"\x53\xef":
            raise ValueError("Guest must be a raw ext4 system.img (not EROFS/super/sparse)")
    debugfs = str(debugfs_path)
    # The final guest is ext4. The dexdump and EROFS tools are used elsewhere
    # in the pipeline and are intentionally not needed to resolve ELF files.
    with tempfile.TemporaryDirectory(prefix="oneos-native-sites-") as tmp:
        root = Path(tmp)

        def dump(image_path: str, filename: str):
            target = root / filename
            if not re.fullmatch(r"[A-Za-z0-9._-]+", filename):
                raise ValueError("Invalid extraction filename")
            proc = subprocess.run(
                [debugfs, "-R", f"dump {image_path} {target}", str(image)],
                capture_output=True, text=True,
            )
            if not target.is_file() or target.stat().st_size == 0:
                return None
            if proc.returncode:
                raise RuntimeError(f"debugfs failed extracting {image_path}: {proc.stderr.strip()}")
            return target.read_bytes()

        vold = dump("/system/bin/vold", "vold")
        incremental = dump("/system/lib64/service.incremental.so", "service.incremental.so")
        sf = dump("/system/bin/surfaceflinger", "surfaceflinger")
        if vold is None or incremental is None or sf is None:
            raise ValueError("Missing native vold, service.incremental.so or surfaceflinger")
        apex_candidates = (
            ("crashrecovery-google.apex", "/system/apex/com.google.android.crashrecovery.apex"),
            ("crashrecovery-aosp.apex", "/system/apex/com.android.crashrecovery.apex"),
            ("crashrecovery-google-compressed.apex", "/system/apex/com.google.android.crashrecovery_compressed.apex"),
            ("crashrecovery-aosp.capex", "/system/apex/com.android.crashrecovery.capex"),
        )
        apexes = [(n, raw) for n, p in apex_candidates if (raw := dump(p, n)) is not None]
        if len(apexes) != 1:
            raise ValueError(f"Expected exactly one crashrecovery APEX, found {[x[0] for x in apexes]}")
        apex_name, apex = apexes[0]

        name_off, try_off = resolve_vold_sites(vold)
        inc_segs = _segments(incremental)
        inc_refs = _name_references(incremental, inc_segs, b"vold")
        inc_names = sorted({name for _, name in inc_refs})
        if len(inc_names) != 1:
            raise ValueError(f"incremental: expected one referenced vold service name, found {len(inc_names)}")
        inc_off = inc_names[0]
        sf_off = resolve_surfaceflinger_cbz_site(sf)

        if vold[name_off:name_off + 6] != b"\x00vold\x00":
            raise ValueError("vold string bytes changed")
        if incremental[inc_off:inc_off + 6] != b"\x00vold\x00":
            raise ValueError("incremental service string bytes changed")
        if vold[try_off:try_off + 4] != bytes.fromhex("b3060036"):
            raise ValueError("vold try_start patch bytes changed")
        if sf[sf_off:sf_off + 4] != bytes.fromhex("680200b4"):
            raise ValueError("surfaceflinger patch bytes changed")

        sha = lambda blob: hashlib.sha256(blob).hexdigest()
        sf_sha12 = hashlib.sha1(sf).hexdigest()[:12]
        original = overlays.read_text(encoding="utf-8")
        updated = update_make_overlays_text(
            original, build_label=build_label,
            vold_sha=sha(vold), inc_sha=sha(incremental),
            crash_apex_name=apex_name, crash_sha=sha(apex),
            vold_name_off=name_off, vold_try_off=try_off,
            inc_name_off=inc_off, sf_sha12=sf_sha12, sf_cbz_off=sf_off,
        )
        if updated != original:
            # Only write after all sites and expected bytes validate.
            fd, scratch = tempfile.mkstemp(prefix=".make-overlays-", dir=overlays.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    out.write(updated)
                os.chmod(scratch, overlays.stat().st_mode & 0o777)
                os.replace(scratch, overlays)
            finally:
                if os.path.exists(scratch):
                    os.unlink(scratch)
        return {
            "vold_sha256": sha(vold),
            "vold_name_offset": name_off,
            "vold_try_start_offset": try_off,
            "incremental_sha256": sha(incremental),
            "incremental_name_offset": inc_off,
            "surfaceflinger_sha1_prefix": sf_sha12,
            "surfaceflinger_cbz_offset": sf_off,
            "crashrecovery_apex": apex_name,
            "crashrecovery_sha256": sha(apex),
            "overlay_table_updated": updated != original,
        }
