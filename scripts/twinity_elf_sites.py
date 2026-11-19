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


def _cstring(data: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\x00", offset)
    if end < 0:
        end = len(data)
    return data[offset:end].decode("ascii", errors="replace")


def _sections(data: bytes) -> list[dict[str, object]]:
    """Parse the section table, including the .dynsym/.dynstr/.rela.plt names."""
    if data[:4] != b"\x7fELF" or data[4:6] != b"\x02\x01":
        return []
    shoff = struct.unpack_from("<Q", data, 40)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 58)
    if shentsize < 64 or shnum == 0 or shoff + shentsize * shnum > len(data):
        return []
    sections = []
    for i in range(shnum):
        base = shoff + i * shentsize
        name, typ, flags, addr, off, size, link, info, align, entsize = \
            struct.unpack_from("<IIQQQQIIQQ", data, base)
        sections.append({
            "name": name, "type": typ, "flags": flags, "addr": addr,
            "off": off, "size": size, "link": link, "info": info,
            "align": align, "entsize": entsize,
        })
    if shstrndx >= len(sections):
        return sections
    strings = sections[shstrndx]
    if strings["off"] + strings["size"] > len(data):
        return sections
    for section in sections:
        section["sname"] = _cstring(data, int(strings["off"]) + int(section["name"]))
    return sections


def _jump_slot_relocations(data: bytes, sections=None) -> list[tuple[int, str]]:
    """Return (GOT offset, imported symbol name) for every JUMP_SLOT entry."""
    if sections is None:
        sections = _sections(data)
    by_name = {s.get("sname"): s for s in sections}
    dynsym = by_name.get(".dynsym")
    dynstr = by_name.get(".dynstr")
    if not dynsym or not dynstr:
        return []
    sym_off = int(dynsym["off"])
    sym_size = int(dynsym["entsize"]) or 24
    str_off = int(dynstr["off"])
    str_size = int(dynstr["size"])
    relocations = []
    for section in sections:
        if section.get("sname") not in (".rela.plt", ".rela.dyn"):
            continue
        if int(section["type"]) != 4:  # SHT_RELA
            continue
        rela_off = int(section["off"])
        rela_size = int(section["size"])
        rela_ent = int(section["entsize"]) or 24
        if rela_off + rela_size > len(data):
            continue
        for off in range(rela_off, rela_off + rela_size, rela_ent):
            if off + 24 > len(data):
                break
            r_offset, r_info, _ = struct.unpack_from("<QQq", data, off)
            if (r_info & 0xffffffff) != 1026:  # R_AARCH64_JUMP_SLOT
                continue
            sym_index = r_info >> 32
            sym_entry = sym_off + sym_index * sym_size
            if sym_entry + 24 > len(data):
                continue
            st_name = struct.unpack_from("<I", data, sym_entry)[0]
            if st_name >= str_size:
                continue
            relocations.append((r_offset, _cstring(data, str_off + st_name)))
    return relocations


def _plt_stub_got(data: bytes, offset: int, pc: int) -> int | None:
    """Decode the aarch64 PLT stub (adrp/ldr/add/br) and return its GOT slot."""
    if offset < 0 or offset + 16 > len(data):
        return None
    adrp, ldr, add, br = struct.unpack_from("<4I", data, offset)
    if ((adrp & 0x9f00001f) == 0x90000010 and
            (ldr & 0xffc003ff) == 0xf9400211 and
            (add & 0xffc003ff) == 0x91000210 and
            br == 0xd61f0220):
        return _adrp_target(adrp, pc) + ((ldr >> 10) & 0xfff) * 8
    return None


def _plt_stubs(data: bytes, segments, sections) -> dict[int, list[int]]:
    """Map each GOT slot to the PLT stub addresses that load it."""
    ranges = []
    for section in sections:
        name = str(section.get("sname", ""))
        if name.startswith(".plt") and int(section["size"]):
            start = int(section["off"])
            end = start + int(section["size"])
            if end <= len(data):
                ranges.append((start, end, int(section["addr"])))
    if not ranges:
        ranges = [(a, b, va) for a, b, va, flags in segments if flags & 1]
    stubs: dict[int, list[int]] = {}
    for start, end, va in ranges:
        for off in range(start, end - 15, 4):
            got = _plt_stub_got(data, off, va + off - start)
            if got is not None:
                stubs.setdefault(got, []).append(va + off - start)
    return stubs


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


# Pre-existing, independently pinned OneOS ColorOS 17 build. Its try_start
# TBZ is separated from MOV w19, w0 by other instructions. The resolver's
# adjacency heuristic is therefore insufficient; use the exact reviewed
# binary identity and byte-verified patch offsets, never a guessed location.
KNOWN_VOLD_SHA256_SITES = {
    "3df4d13bb9636cb9833a6ee2027d189c6acfd25b0404058ca130afb164a55896": (0x104e6, 0x78b64),
}


def _known_vold_sites(data: bytes, sha256_hex: str) -> tuple[int, int] | None:
    sites = KNOWN_VOLD_SHA256_SITES.get(sha256_hex)
    if sites is None:
        return None
    name_off, try_off = sites
    if (data[name_off:name_off + 6] != b"\x00vold\x00" or
            data[try_off:try_off + 4] != bytes.fromhex("b3060036")):
        raise ValueError("Hash-pinned ColorOS vold changed expected patch bytes")
    return sites


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
        import hashlib
        known = _known_vold_sites(data, hashlib.sha256(data).hexdigest())
        if known is not None:
            if known[0] != names[0]:
                raise ValueError("Known vold service reference does not match pinned location")
            return known
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


def _is_bl(word: int) -> bool:
    return word & 0xfc000000 == 0x94000000


def _is_ldr_64(word: int) -> bool:
    """LDR (immediate, unsigned offset, 64-bit), any Rt/base/offset."""
    return word & 0xffc00000 == 0xf9400000


def _sets_x8_from_result(word: int) -> bool:
    # Kept for the reviewed three-instruction shape and its unit fixtures.
    return ((_is_ldr_64(word) and (word & 31) == 8) or
            word == 0xaa0003e8)


def _is_cbz_cbnz(word: int) -> bool:
    return (word & 0x7e000000) == 0x34000000


def _is_cbnz(word: int) -> bool:
    return _is_cbz_cbnz(word) and bool(word & 0x01000000)


def _is_unconditional_b(word: int) -> bool:
    # B is 0x14000000; BL is 0x94000000 and must not match here.
    return (word & 0xfc000000) == 0x14000000


def _is_conditional_branch(word: int) -> bool:
    """CBZ/CBNZ (32/64-bit) or TBZ/TBNZ."""
    return (word & 0x7e000000) in (0x34000000, 0x36000000)


def _branch_target(word: int, pc: int) -> int:
    if word & 0x7e000000 == 0x34000000:
        return pc + (_sign_extend((word >> 5) & 0x7ffff, 19) << 2)
    if _is_unconditional_b(word):
        return pc + (_sign_extend(word & 0x03ffffff, 26) << 2)
    return pc + (_sign_extend((word >> 5) & 0x3fff, 14) << 2)


def _bl_target(word: int, pc: int) -> int:
    return pc + (_sign_extend(word & 0x03ffffff, 26) << 2)


def _aibinder_extension_guards(data: bytes, segments, sections) -> tuple[list[dict], list[int]]:
    """Find null checks around an imported AIBinder_getExtension call.

    The reviewed binaries used "bl AIBinder_getExtension; ldr x8; cbz x8; bl".
    Newer compilers may pick another destination register and move the status
    (w0) aside before the null check, so identify the call by its real PLT
    target and follow the loaded out-extension register instead of guessing x8.
    """
    relocations = _jump_slot_relocations(data, sections)
    extension_gots = {off for off, name in relocations if name == "AIBinder_getExtension"}
    if not extension_gots:
        return [], []
    stubs_by_got = _plt_stubs(data, segments, sections)
    extension_stubs = set()
    for got in extension_gots:
        extension_stubs.update(stubs_by_got.get(got, ()))
    if not extension_stubs:
        return [], []
    guards = []
    for a, b, va, flags in segments:
        if not flags & 1:
            continue
        for off in range(a, b - 4, 4):
            word = struct.unpack_from("<I", data, off)[0]
            pc = va + off - a
            if not _is_bl(word) or _bl_target(word, pc) not in extension_stubs:
                continue
            register = None
            load_off = None
            for step in range(1, 6):
                p = off + step * 4
                if p + 4 > b:
                    break
                candidate = struct.unpack_from("<I", data, p)[0]
                if _is_ldr_64(candidate):
                    register = candidate & 31
                    load_off = p
                    break
                if candidate == 0xaa0003e8:
                    register = 8
                    load_off = p
                    break
                if _is_bl(candidate) or _is_cbz_cbnz(candidate) or candidate == 0xd65f03c0:
                    break
            if register is None:
                continue
            for step in range(1, 6):
                p = load_off + step * 4
                if p + 4 > b:
                    break
                candidate = struct.unpack_from("<I", data, p)[0]
                if _is_cbz_cbnz(candidate) and (candidate & 31) == register:
                    branch_pc = va + p - a
                    target = _branch_target(candidate, branch_pc)
                    delta = target - branch_pc
                    if not 0 < delta <= 0x100:
                        break
                    target_off = a + (target - va)
                    if target_off > b:
                        break
                    if not _is_cbnz(candidate):
                        # cbz: skip the extension block when the out pointer is null.
                        if any(_is_bl(struct.unpack_from("<I", data, q)[0])
                               for q in range(p + 4, target_off, 4)):
                            guards.append({
                                "offset": p, "kind": "cbz", "call_offset": off,
                                "register": register,
                            })
                        break
                    # cbnz: enter only the extension block; the fall-through must
                    # jump over it, so replacing cbnz with nop skips the block.
                    if p + 8 > b:
                        break
                    skip = struct.unpack_from("<I", data, p + 4)[0]
                    if not _is_unconditional_b(skip):
                        break
                    skip_target = _branch_target(skip, branch_pc + 4)
                    skip_off = a + (skip_target - va)
                    if not (target < skip_target and skip_off <= b):
                        break
                    if any(_is_bl(struct.unpack_from("<I", data, q)[0])
                           for q in range(target_off, skip_off, 4)):
                        guards.append({
                            "offset": p, "kind": "cbnz", "call_offset": off,
                            "register": register,
                        })
                    break
                if _is_bl(candidate) or candidate == 0xd65f03c0:
                    break
    unique = {}
    for guard in guards:
        unique.setdefault(guard["offset"], guard)
    return sorted(unique.values(), key=lambda guard: guard["offset"]), sorted(extension_stubs)


def resolve_surfaceflinger_cbz_sites(data: bytes) -> list[tuple[int, str]]:
    """Locate every guarded Qualcomm composer extension call.

    Returns (file offset of the null check, "cbz" or "cbnz"). The caller
    patches every site; SurfaceFlinger may instantiate the client more than
    once for different displays.
    """
    segments = _segments(data)
    if b"IQtiComposer3Client" not in data or b"AIBinder_getExtension" not in data:
        raise ValueError("surfaceflinger: expected Qualcomm composer symbols")
    sections = _sections(data)
    guards, extension_stubs = _aibinder_extension_guards(data, segments, sections)
    if extension_stubs:
        if not guards:
            raise ValueError(
                "surfaceflinger: AIBinder_getExtension has no guarded composer call "
                f"(PLT stubs: {[hex(x) for x in extension_stubs]})"
            )
        return [(int(g["offset"]), str(g["kind"])) for g in guards]
    # Fallback for an ELF without usable .rela.plt/.plt data: the exact shape
    # from the reviewed builds (cbz x8 immediately before a call, after ldr x8).
    candidates = []
    for a, b, va, flags in segments:
        if not flags & 1:
            continue
        for off in range(a + 8, b - 7, 4):
            before = struct.unpack_from("<I", data, off - 8)[0]
            load = struct.unpack_from("<I", data, off - 4)[0]
            cbz = struct.unpack_from("<I", data, off)[0]
            after = struct.unpack_from("<I", data, off + 4)[0]
            pc = va + off - a
            if (_is_bl(before) and _sets_x8_from_result(load) and
                    cbz & 0xff00001f == 0xb4000008 and _is_bl(after) and
                    0 < _branch_target(cbz, pc) - pc <= 0x100):
                candidates.append(off)
    if not candidates:
        raise ValueError("surfaceflinger: expected at least one composer guard, found 0")
    return [(off, "cbz") for off in candidates]


def resolve_surfaceflinger_cbz_site(data: bytes) -> int:
    """Backward-compatible single-site lookup used by fixtures and callers."""
    sites = resolve_surfaceflinger_cbz_sites(data)
    if len(sites) != 1:
        raise ValueError(f"surfaceflinger: expected one composer guard, found {len(sites)}")
    return sites[0][0]


def diagnose_surfaceflinger_sites(data: bytes) -> dict[str, object]:
    """Bounded, nonpatching diagnostics for an unfamiliar SurfaceFlinger ELF.

    Return instruction words and offsets only; never publish copyrighted
    executable images. Nothing in this report authorizes a binary patch.
    """
    import hashlib

    segments = _segments(data)
    sections = _sections(data)
    relocations = _jump_slot_relocations(data, sections)
    extension_gots = {off for off, name in relocations if name == "AIBinder_getExtension"}
    stubs_by_got = _plt_stubs(data, segments, sections)
    extension_stubs = set()
    for got in extension_gots:
        extension_stubs.update(stubs_by_got.get(got, ()))
    extension_guards, _ = _aibinder_extension_guards(data, segments, sections)

    def word(off: int) -> int | None:
        if 0 <= off <= len(data) - 4:
            return struct.unpack_from("<I", data, off)[0]
        return None

    def window(off: int) -> list[str]:
        out = []
        for p in range(off - 16, off + 20, 4):
            w = word(p)
            out.append(f"0x{w:08x}" if w is not None else "--")
        return out

    guards = []
    calls = []
    composer = []
    for a, b, va, flags in segments:
        if not flags & 1:
            continue
        for off in range(a + 8, b - 7, 4):
            before = word(off - 8)
            load = word(off - 4)
            branch = word(off)
            after = word(off + 4)
            if branch is None or after is None:
                continue
            if (before is not None and load is not None and
                    _is_bl(before) and _sets_x8_from_result(load) and
                    branch & 0xff00001f == 0xb4000008):
                pc = va + off - a
                before_target = _bl_target(before, pc - 8)
                composer.append({
                    "branch_offset": f"0x{off:x}",
                    "branch_word": f"0x{branch:08x}",
                    "branch_target": f"0x{_branch_target(branch, pc):x}",
                    "branch_delta": f"0x{_branch_target(branch, pc) - pc:x}",
                    "forward": _branch_target(branch, pc) > pc,
                    "before_word": f"0x{before:08x}",
                    "before_target": f"0x{before_target:x}",
                    "before_is_aibinder_get_extension": before_target in extension_stubs,
                    "load_word": f"0x{load:08x}",
                    "after_word": f"0x{after:08x}",
                    "after_is_bl": _is_bl(after),
                    "window": window(off),
                })
            if _is_conditional_branch(branch) and _is_bl(after):
                pc = va + off - a
                guards.append({
                    "branch_offset": f"0x{off:x}",
                    "branch_word": f"0x{branch:08x}",
                    "branch_register": branch & 31,
                    "branch_target": f"0x{_branch_target(branch, pc):x}",
                    "before_word": f"0x{before:08x}" if before is not None else None,
                    "load_word": f"0x{load:08x}" if load is not None else None,
                    "window": window(off),
                })
            if _is_bl(branch) and _is_conditional_branch(after):
                pc = va + off - a
                calls.append({
                    "call_offset": f"0x{off:x}",
                    "call_target": f"0x{pc + (_sign_extend(branch & 0x03ffffff, 26) << 2):x}",
                    "branch_offset": f"0x{off + 4:x}",
                    "branch_word": f"0x{after:08x}",
                    "branch_target": f"0x{_branch_target(after, pc + 4):x}",
                    "window": window(off),
                })
    return {
        "warning": "DIAGNOSTIC ONLY. DO NOT PATCH WITHOUT INSTRUCTION FLOW ANALYSIS.",
        "surfaceflinger_sha256": hashlib.sha256(data).hexdigest(),
        "file_size": len(data),
        "qti_composer_symbol_present": b"IQtiComposer3Client" in data,
        "aibinder_get_extension_symbol_present": b"AIBinder_getExtension" in data,
        "aibinder_get_extension_gots": [f"0x{x:x}" for x in sorted(extension_gots)],
        "aibinder_get_extension_plt_stubs": [f"0x{x:x}" for x in sorted(extension_stubs)],
        "jump_slot_relocation_count": len(relocations),
        "aibinder_extension_guards": [{
            "branch_offset": f"0x{g['offset']:x}",
            "kind": g["kind"],
            "call_offset": f"0x{g['call_offset']:x}",
            "register": g["register"],
            "window": window(int(g["offset"])),
        } for g in extension_guards[:64]],
        "guard_windows": guards[:128],
        "call_windows": calls[:128],
        "composer_candidates": composer[:512],
        "truncated": (len(guards) > 128 or len(calls) > 128 or
                      len(composer) > 512 or len(extension_guards) > 64),
    }


def update_make_overlays_text(
    source: str, *, build_label: str, vold_sha: str, inc_sha: str,
    crash_apex_name: str, crash_sha: str, vold_name_off: int,
    vold_try_off: int, inc_name_off: int, sf_sha12: str,
    sf_cbz_off: int | list[int] | tuple[int, ...],
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
               (vold_name_off, vold_try_off, inc_name_off)):
        raise ValueError("Invalid patch offset")
    offsets = (sf_cbz_off,) if isinstance(sf_cbz_off, int) else tuple(sf_cbz_off)
    if (not offsets or
            not all(isinstance(x, int) and x >= 0 for x in offsets) or
            len(set(offsets)) != len(offsets)):
        raise ValueError("Invalid SurfaceFlinger patch offsets")
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
    if len(offsets) == 1:
        site = f'"{sf_sha12.lower()}": {offsets[0]:#x}'
    else:
        site = (f'"{sf_sha12.lower()}": ('
                + ", ".join(f"{x:#x}" for x in offsets) + ")")
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
    debugfs_path, extract_erofs_path, diagnostics_dir=None,
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

        try:
            name_off, try_off = resolve_vold_sites(vold)
        except ValueError:
            if diagnostics_dir is not None:
                import json
                target_dir = Path(diagnostics_dir)
                target_dir.mkdir(parents=True, exist_ok=True)
                report = diagnose_vold_sites(vold)
                (target_dir / "twinity-vold-native-diagnostics.json").write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            raise
        inc_segs = _segments(incremental)
        inc_refs = _name_references(incremental, inc_segs, b"vold")
        inc_names = sorted({name for _, name in inc_refs})
        if len(inc_names) != 1:
            raise ValueError(f"incremental: expected one referenced vold service name, found {len(inc_names)}")
        inc_off = inc_names[0]
        try:
            sf_sites = resolve_surfaceflinger_cbz_sites(sf)
        except ValueError:
            if diagnostics_dir is not None:
                import json
                target_dir = Path(diagnostics_dir)
                target_dir.mkdir(parents=True, exist_ok=True)
                report = diagnose_surfaceflinger_sites(sf)
                (target_dir / "twinity-surfaceflinger-native-diagnostics.json").write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            raise

        if vold[name_off:name_off + 6] != b"\x00vold\x00":
            raise ValueError("vold string bytes changed")
        if incremental[inc_off:inc_off + 6] != b"\x00vold\x00":
            raise ValueError("incremental service string bytes changed")
        if vold[try_off:try_off + 4] != bytes.fromhex("b3060036"):
            raise ValueError("vold try_start patch bytes changed")
        for sf_off, _kind in sf_sites:
            sf_word = struct.unpack_from("<I", sf, sf_off)[0]
            if sf_word & 0x7e000000 != 0x34000000:
                raise ValueError("surfaceflinger patch bytes changed")
        sf_offsets = [off for off, _kind in sf_sites]

        sha = lambda blob: hashlib.sha256(blob).hexdigest()
        sf_sha12 = hashlib.sha1(sf).hexdigest()[:12]
        original = overlays.read_text(encoding="utf-8")
        updated = update_make_overlays_text(
            original, build_label=build_label,
            vold_sha=sha(vold), inc_sha=sha(incremental),
            crash_apex_name=apex_name, crash_sha=sha(apex),
            vold_name_off=name_off, vold_try_off=try_off,
            inc_name_off=inc_off, sf_sha12=sf_sha12, sf_cbz_off=sf_offsets,
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
            "surfaceflinger_cbz_offset": sf_offsets[0],
            "surfaceflinger_cbz_offsets": sf_offsets,
            "crashrecovery_apex": apex_name,
            "crashrecovery_sha256": sha(apex),
            "overlay_table_updated": updated != original,
        }
