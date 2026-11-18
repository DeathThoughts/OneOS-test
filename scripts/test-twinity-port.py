#!/usr/bin/env python3
"""Hermetic unit tests for Twinity ColorOS 17 ELF patching, super.img filtering, and repository wiring."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import twinity_elf_sites  # noqa: E402


def load_script_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


build_mod = load_script_module("build_twinity_port", "build-twinity-port.py")
wire_mod = load_script_module("wire_twinity_port", "wire-twinity-port.py")


class LpUnpackAndOdmAiTest(unittest.TestCase):
    def test_lpunpack_extracts_guest_only_and_excludes_vendor(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            fake_lpunpack = work / "fake_lpunpack.sh"
            fake_lpunpack.write_text(
                "#!/bin/sh\n"
                'out="$2"\n'
                'printf "guest-sys" > "$out/system_a.img"\n'
                ': > "$out/system_b.img"\n'
                'printf "guest-stock" > "$out/my_stock_a.img"\n'
                'printf "poco-vendor" > "$out/vendor_a.img"\n'
                'printf "poco-odm" > "$out/odm_a.img"\n'
                'printf "poco-dlkm" > "$out/vendor_dlkm_a.img"\n'
                'printf "poco-miext" > "$out/mi_ext_a.img"\n',
                encoding="utf-8",
            )
            fake_lpunpack.chmod(0o755)
            super_img = work / "super.img"
            super_img.write_bytes(b"super")
            guest_dir = work / "guest"
            odm_inspect = work / "odm_inspect.img"

            summary = build_mod.lpunpack_super(
                super_img, guest_dir, odm_inspect_out=odm_inspect, lpunpack_bin=fake_lpunpack
            )
            self.assertEqual({"system.img", "my_stock.img"}, {p.name for p in guest_dir.iterdir()})
            self.assertTrue(odm_inspect.is_file())
            self.assertIn("vendor", summary["excluded_hardware"])
            self.assertIn("mi_ext", summary["excluded_hardware"])
            self.assertIn("vendor_dlkm", summary["excluded_hardware"])

    def test_lpunpack_falls_back_to_pinned_mystic_adapter(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            super_img = work / "super.img"
            super_img.write_bytes(b"test")
            guest = work / "guest"
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                raw = Path(command[-1])
                (raw / "system_a.img").write_bytes(b"system")
                (raw / "vendor_a.img").write_bytes(b"vendor")
                return subprocess.CompletedProcess(command, 0)

            with patch.object(build_mod.shutil, "which", return_value=None), \
                 patch.dict(build_mod.os.environ, {"MYSTIC_PYTHON": "/tmp/mystic-venv/bin/python"}), \
                 patch.object(build_mod.subprocess, "run", side_effect=fake_run):
                result = build_mod.lpunpack_super(super_img, guest)
            self.assertEqual("/tmp/mystic-venv/bin/python", calls[0][0])
            self.assertEqual(
                str(SCRIPT_DIR / "unpack-twinity-super.py"),
                calls[0][1],
            )
            self.assertEqual(b"system", (guest / "system.img").read_bytes())
            self.assertFalse((guest / "vendor.img").exists())
            self.assertIn("vendor", result["excluded_hardware"])

    def test_validate_odm_ai_tar_enforces_guest_prepare_rules(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good.tar"
            with tarfile.open(good, "w") as tf:
                raw = b"\x7fELF\x02" + b"\x00" * 64
                ti = tarfile.TarInfo("lib64/libaiboost.so")
                ti.size = len(raw)
                tf.addfile(ti, io.BytesIO(raw))
            self.assertEqual(["lib64/libaiboost.so"], build_mod.validate_odm_ai_tar(good))

            bad_sym = Path(td) / "bad_sym.tar"
            with tarfile.open(bad_sym, "w") as tf:
                ti = tarfile.TarInfo("lib64/libaiboost.so")
                ti.type = tarfile.SYMTYPE
                ti.linkname = "/data/system/locksettings.db"
                tf.addfile(ti)
            with self.assertRaises(ValueError):
                build_mod.validate_odm_ai_tar(bad_sym)

            bad_path = Path(td) / "bad_path.tar"
            with tarfile.open(bad_path, "w") as tf:
                ti = tarfile.TarInfo("etc/init.rc")
                ti.size = 4
                tf.addfile(ti, io.BytesIO(b"nope"))
            with self.assertRaises(ValueError):
                build_mod.validate_odm_ai_tar(bad_path)


class TwinityElfSitesTest(unittest.TestCase):
    @staticmethod
    def _build_minimal_elf64(rodata_vaddr: int, rodata: bytes, text_vaddr: int, text: bytes, dynsym_names: list[tuple[str, int]] | None = None) -> bytes:
        # Build a valid little-endian AArch64 ELF64 with PT_LOAD segments and optional .dynsym/.dynstr + .rela.plt
        e_ehsize = 64
        e_phentsize = 56
        e_shentsize = 64
        phnum = 2
        ro_off = 0x1000
        tx_off = 0x2000
        buf = bytearray(0x4000)

        struct.pack_into(
            "<4sBBBBB7xHHIQQQIHHHHHH",
            buf, 0,
            b"\x7fELF", 2, 1, 1, 0, 0,
            3, 183, 1, text_vaddr, e_ehsize, 0, 0,
            e_ehsize, e_phentsize, phnum, e_shentsize, 0, 0,
        )
        # Program header 0: rodata (PF_R = 4)
        struct.pack_into("<IIQQQQQQ", buf, e_ehsize, 1, 4, ro_off, rodata_vaddr, rodata_vaddr, len(rodata), len(rodata), 0x1000)
        # Program header 1: text (PF_R | PF_X = 5)
        struct.pack_into("<IIQQQQQQ", buf, e_ehsize + e_phentsize, 1, 5, tx_off, text_vaddr, text_vaddr, len(text), len(text), 0x1000)
        buf[ro_off:ro_off + len(rodata)] = rodata
        buf[tx_off:tx_off + len(text)] = text

        if dynsym_names:
            dynstr = bytearray(b"\x00")
            dynsym = bytearray(24)  # STN_UNDEF
            rela = bytearray()
            plt_vaddr = text_vaddr + len(text) - 0x40
            for idx, (sym_name, call_stub_vaddr) in enumerate(dynsym_names, start=1):
                st_name = len(dynstr)
                dynstr.extend(sym_name.encode("ascii") + b"\x00")
                dynsym.extend(struct.pack("<IBBHQQ", st_name, 0x12, 0, 0, call_stub_vaddr, 16))
                rela.extend(struct.pack("<QQq", plt_vaddr + idx * 8, (idx << 32) | 1026, 0))

            ds_off = 0x3000
            st_off = ds_off + len(dynsym)
            rl_off = (st_off + len(dynstr) + 7) & ~7
            sh_off = (rl_off + len(rela) + 7) & ~7
            buf.extend(b"\x00" * max(0, sh_off + 5 * e_shentsize - len(buf)))
            buf[ds_off:ds_off + len(dynsym)] = dynsym
            buf[st_off:st_off + len(dynstr)] = dynstr
            buf[rl_off:rl_off + len(rela)] = rela

            shstr = b"\x00.dynsym\x00.dynstr\x00.rela.plt\x00.shstrtab\x00"
            shstr_off = sh_off + 5 * e_shentsize
            buf.extend(b"\x00" * max(0, shstr_off + len(shstr) - len(buf)))
            buf[shstr_off:shstr_off + len(shstr)] = shstr

            # Section 0: null, 1: .dynsym (SHT_DYNSYM=11, link=2), 2: .dynstr (SHT_STRTAB=3), 3: .rela.plt (SHT_RELA=4, link=1), 4: .shstrtab
            struct.pack_into("<IIQQQQIIQQ", buf, sh_off + 1 * 64, 1, 11, 2, 0, ds_off, len(dynsym), 2, 1, 8, 24)
            struct.pack_into("<IIQQQQIIQQ", buf, sh_off + 2 * 64, 9, 3, 2, 0, st_off, len(dynstr), 0, 0, 1, 0)
            struct.pack_into("<IIQQQQIIQQ", buf, sh_off + 3 * 64, 17, 4, 2, 0, rl_off, len(rela), 1, 0, 8, 24)
            struct.pack_into("<IIQQQQIIQQ", buf, sh_off + 4 * 64, 27, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0)
            struct.pack_into("<Q", buf, 40, sh_off)
            struct.pack_into("<HH", buf, 60, 5, 4)
        return bytes(buf)

    def test_vold_name_and_try_start_sites_resolved_and_patched(self):
        rodata = b"prefix\x00vold\x00suffix"
        # ADRP x1, 0x1000 -> 0x90000001 (from PC page 0x2000? page delta -1 -> immlo=3, immhi=0x7ffff -> 0xf0ffffe1);
        # simpler: put rodata at 0x10000, text at 0x20000: delta = -0x10000 (-16 pages)
        # Or put rodata at 0x10000 and text at 0x11000: delta = -1 page -> 0xf0ffffe1; even simpler: rodata at 0x20000, text at 0x10000 (+16 pages: immlo=0, immhi=4 -> 0x90000081)
        adrp_x1 = 0x90000081  # PC=0x10000 -> page 0x20000
        add_x1 = 0x91000021 | (7 << 10)  # add x1, x1, #7 ("vold\0")
        ldr_x9 = 0xF9402509   # ldr x9, [x8, #0x48]
        blr_x9 = 0xD63F0120   # blr x9
        mov_w19 = 0x2A0003F3  # mov w19, w0
        tbz_w19 = 0x360006B3  # tbz w19, #0, +0xd4 (imm14 = 0x35 = 53 instrs * 4 = 0xd4)
        insns = struct.pack("<6I", adrp_x1, add_x1, ldr_x9, blr_x9, mov_w19, tbz_w19) + b"\x1f\x20\x03\xd5" * 60
        elf = self._build_minimal_elf64(0x20000, rodata, 0x10000, insns)
        name_off, try_off = twinity_elf_sites.resolve_vold_sites(elf)
        self.assertEqual(0x1000 + 6, name_off)
        self.assertEqual(0x2000 + 20, try_off)

    def test_hash_pinned_coloros_vold_sites_require_exact_bytes(self):
        # The real binary SHA-256 and offsets are independently recorded in
        # make-overlays.sh. This tests the guarded lookup, not synthetic hash
        # equivalence to the complete ColorOS ELF.
        digest = "3df4d13bb9636cb9833a6ee2027d189c6acfd25b0404058ca130afb164a55896"
        elf = bytearray(0x78b68)
        elf[0x104e6:0x104ec] = b"\x00vold\x00"
        elf[0x78b64:0x78b68] = bytes.fromhex("b3060036")
        self.assertEqual((0x104e6, 0x78b64),
                         twinity_elf_sites._known_vold_sites(bytes(elf), digest))
        self.assertIsNone(twinity_elf_sites._known_vold_sites(bytes(elf), "0" * 64))
        elf[0x78b64] ^= 1
        with self.assertRaisesRegex(ValueError, "changed expected patch bytes"):
            twinity_elf_sites._known_vold_sites(bytes(elf), digest)

    def test_unknown_vold_produces_bounded_diagnostics_without_patch(self):
        rodata = b"prefix\x00vold\x00suffix"
        adrp = 0x90000081
        add = 0x91000021 | (7 << 10)
        # This unknown build retains a name reference but lacks the validated
        # branch; do not turn a nearby TBZ into a guessed patch location.
        code = struct.pack("<4I", adrp, add, 0x2A0003F3, 0x360000B3)
        elf = self._build_minimal_elf64(0x20000, rodata, 0x10000, code)
        with self.assertRaisesRegex(ValueError, "try_start"):
            twinity_elf_sites.resolve_vold_sites(elf)
        report = twinity_elf_sites.diagnose_vold_sites(elf)
        self.assertEqual(1, report["service_reference_count"])
        self.assertEqual(0, report["expected_tbz_exact_count"])
        self.assertGreaterEqual(report["tbz_w19_count"], 1)
        self.assertIn("DO NOT PATCH", report["warning"])

    def test_surfaceflinger_cbz_site_resolved(self):
        # Stub1 at 0x10100 (AIBinder_getExtension), Stub2 at 0x10110 (fromBinder)
        # At 0x10000: bl 0x10100 (+0x100 -> imm26=0x40 -> 0x94000040)
        # At 0x10004: ldr x8, [sp, #8] (0xf94007e8)
        # At 0x10008: cbz x8, +0x4c (imm19 = 19 -> 0xb4000268)
        # At 0x1000c: bl 0x10110 (+0x104 -> imm26=0x41 -> 0x94000041)
        code = bytearray(0x200)
        struct.pack_into("<4I", code, 0, 0x94000040, 0xF94007E8, 0xB4000268, 0x94000041)
        elf = self._build_minimal_elf64(
            0x20000, b"rodata\x00", 0x10000, bytes(code),
            dynsym_names=[
                ("AIBinder_getExtension", 0x10100),
                ("_ZN4aidl6vendor3qti8hardware7display8composer20IQtiComposer3Client10fromBinderERKNS_7android2os11SpAIBinderE", 0x10110),
            ],
        )
        cbz_off = twinity_elf_sites.resolve_surfaceflinger_cbz_site(elf)
        self.assertEqual(0x2008, cbz_off)

    def test_update_make_overlays_text_is_idempotent(self):
        sample = (
            'BUILDS = [\n'
            '    ("Existing", {\n'
            '        "vold": "aa",\n'
            '    }, [("vold", 0x10, NAME)]),\n'
            ']\n'
            'SITES = {"9896c0cba131": 0x470bc8,\n'
            '         "e5a8726a6687": 0x452a50,\n'
            '}\n'
        )
        once = twinity_elf_sites.update_make_overlays_text(
            sample,
            build_label="Twinity Test",
            vold_sha="1" * 64,
            inc_sha="2" * 64,
            crash_apex_name="crashrecovery.apex",
            crash_sha="3" * 64,
            vold_name_off=0x104E2,
            vold_try_off=0x7AB64,
            inc_name_off=0x6536B,
            sf_sha12="abcdef012345",
            sf_cbz_off=0x475D24,
        )
        twice = twinity_elf_sites.update_make_overlays_text(
            once,
            build_label="Twinity Test",
            vold_sha="1" * 64,
            inc_sha="2" * 64,
            crash_apex_name="crashrecovery.apex",
            crash_sha="3" * 64,
            vold_name_off=0x104E2,
            vold_try_off=0x7AB64,
            inc_name_off=0x6536B,
            sf_sha12="abcdef012345",
            sf_cbz_off=0x475D24,
        )
        self.assertEqual(once, twice)
        self.assertIn("0x104e2", once)
        self.assertIn('"abcdef012345": 0x475d24', once)


class WireRepositoryAndRestartHomeTest(unittest.TestCase):
    def test_wire_repository_is_idempotent_and_restart_home_passes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            files = {
                "app-android/app/src/full/kotlin/com/fahadali/oneos/session/PrepareJob.kt": (
                    "import com.fahadali.oneos.core.model.SessionEnv\n"
                    "class PrepareJob {\n"
                    "    fun run() {\n"
                    "                val gz = GZIPInputStream(counting, 1 shl 16)\n"
                    "                val buf = ByteArray(1 shl 18)\n"
                    "                val head = ByteArray(2)\n"
                    "                while (true) {\n"
                    '                    if (cancelled) throw IOException("stopped")\n'
                    "                    val n = gz.read(buf)\n"
                    "                    if (n < 0) break\n"
                    "                    // ext4 superblock magic 0xEF53, little endian at byte 1080.\n"
                    "                    for (k in 0 until n) {\n"
                    "                        val at = rawBytes + k\n"
                    "                        if (at == Images.EXT4_MAGIC_OFFSET) head[0] = buf[k]\n"
                    "                        if (at == Images.EXT4_MAGIC_OFFSET + 1) {\n"
                    "                            head[1] = buf[k]\n"
                    "                            val magic = (head[0].toInt() and 0xff) or ((head[1].toInt() and 0xff) shl 8)\n"
                    '                            if (magic != Images.EXT4_MAGIC) throw IOException("not an ext4 image")\n'
                    "                        }\n"
                    "                        if (at > Images.EXT4_MAGIC_OFFSET + 1) break\n"
                    "                    }\n"
                    "                    rawMd.update(buf, 0, n)\n"
                    "                    rawBytes += n\n"
                    '                    if (rawBytes > image.rawBytes) throw IOException("the image unpacks to more than ${image.rawBytes} bytes")\n'
                    "                    out.write(buf, 0, n)\n"
                    "                }\n"
                    "                // Whatever follows the gzip stream counts for the file's hash too.\n"
                    "                do {\n"
                    "                    val k = counting.read(buf, 0, buf.size)\n"
                    "                } while (k >= 0)\n"
                    "                out.flush()\n"
                    "                progress(gzBytes)\n"
                    "    }\n}\n"
                ),
                "app-android/core/model/src/main/kotlin/com/fahadali/oneos/core/model/Images.kt": (
                    "data class ImageRef(\n"
                    "    val family: SystemFamily,\n"
                    ") {\n"
                    '    val mirrorUrl: String get() = "${Images.MIRROR_FILES_URL}$gzName/download"\n'
                    "}\n"
                    "object ImageIdentifier {\n"
                    "    fun id() {\n"
                    "            } else if (raw != null) {\n"
                    '                val pick = if (profile == null || profile.choice(raw.id) != null) "pick ${raw.gzName}" else wantText\n'
                    "                ImageIdentity(\n"
                    "                    ImageState.UNCOMPRESSED, raw,\n"
                    '                    block("Pick the .img.gz file", "$name is the unpacked ${raw.shortName} image. OneOS unpacks the image itself: $pick.", R_UNCOMPRESSED),\n'
                    "                )\n"
                    "    }\n}\n"
                    "object Images {\n"
                    '    const val ID_FIND_X9U_IN_1105 = "coloros17-findx9u-in-1105"\n'
                    "    val DATA_UPGRADES: Map<String, String> = emptyMap()\n"
                    "    val ALL: List<ImageRef> = listOf(FIND_N6, FIND_X9U_IN_1105, PIXEL_YOGI)\n"
                    "}\n"
                ),
                "app-android/core/model/src/main/kotlin/com/fahadali/oneos/core/model/PackageSets.kt": (
                    "object PackageSets {\n"
                    '    const val ID_X9U_IN_1105_SULTRA = "x9u-in-1105-sultra"\n'
                    "    val ALL: List<PackageSet> = listOf(N6_GLOBAL_F8, N6_F8U, X9U_IN_1105_SULTRA, PIXEL_F8U)\n"
                    "}\n"
                ),
                "app-android/core/model/src/main/kotlin/com/fahadali/oneos/core/model/Profiles.kt": (
                    "object Profiles {\n"
                    "    val S_8550 = PhoneProfile(\n"
                    "        choices = listOf(X9U_IN_1105_CHOICE, PIXEL9_CHOICE, INFINITYX_CHOICE, LINEAGE_CHOICE, HYPEROS_CHOICE, OXYGEN16_CHOICE, NOTHING_CHOICE, ORIGINOS_CHOICE),\n"
                    "    )\n"
                    "    val S_PLUS_8550 = PhoneProfile(\n"
                    "        choices = listOf(X9U_IN_1105_CHOICE, PIXEL9_CHOICE, INFINITYX_CHOICE, LINEAGE_CHOICE, HYPEROS_CHOICE, OXYGEN16_CHOICE, NOTHING_CHOICE, ORIGINOS_CHOICE),\n"
                    "    )\n"
                    '    val S_8550_JP = S_8550.copy(id = ID_S_8550_JP, label = "Galaxy S23 (au)", modelPrefix = "SCG19", codename = "SCG19")\n'
                    "    fun byId(id: String, profiles: List<PhoneProfile> = BUILT_IN): PhoneProfile? = profiles.firstOrNull { it.id == id }\n"
                    "}\n"
                ),
                "app-android/app/src/full/kotlin/com/fahadali/oneos/session/StageCheck.kt": (
                    "import com.fahadali.oneos.BuildConfig\n"
                    "import com.fahadali.oneos.core.model.GateInput\n"
                    "class StageCheck {\n"
                    "    fun check() {\n"
                    "        val report = SwitchGates.evaluate(phase, input)\n"
                    "    }\n}\n"
                ),
                "app-android/app/src/full/res/values/strings_advanced.xml": (
                    "<resources>\n"
                    '    <string name="image_pick_summary">Opens your files. OneOS reads only the file you pick.</string>\n'
                    "</resources>\n"
                ),
                "app-android/app/src/full/res/values-pt-rBR/strings_advanced.xml": "<resources>\n</resources>\n",
                "app-android/app/src/full/kotlin/com/fahadali/oneos/ui/ImageActivity.kt": (
                    'val x = Intent().putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/gzip", "application/x-gzip", "application/octet-stream", "*/*"))\n'
                    '            Cards.row(systems, GLYPHS[i % GLYPHS.size], Images.systemName(image, p), notes.joinToString(" · "))\n'
                ),
                "app-android/app/src/full/kotlin/com/fahadali/oneos/ui/SystemsActivity.kt": (
                    '    /** "I have the file" of [imageId] (null: whichever system it is). */\n'
                    "    fun choose(imageId: String?) {\n"
                    "        pickFor = imageId\n"
                    '        val i = Intent().putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/gzip", "application/x-gzip", "application/octet-stream", "*/*"))\n'
                    "    }\n"
                    "    fun list() {\n"
                    "        val color = image(com.fahadali.oneos.core.model.SystemFamily.COLOROS, Images.FIND_N6)\n"
                    "            CatalogEntry(color.name, color, R.drawable.os_coloros, available.any { it.id == color.id }),\n"
                    "                        image.downloadUrl?.let { drive -> }\n"
                    "    }\n"
                ),
                "app-android/app/src/full/assets/root/oneos_root.sh": (
                    'SET_COMMON="services_patched.jar"\n'
                    'IMAGES_GMS="coloros17-findn6 coloros17-findx9u-in-1105 nothingos5-metroid oxygenos16-cph2745 lineage24-infiniti"\n'
                    'profile_set() {\n'
                    '    case "$1" in\n'
                    '        "s-8550 hyperos-lhasa") echo hyper-f8u ;;\n'
                    '    esac\n}\n'
                    'image_raw() {\n'
                    '    case "$1" in\n'
                    '        coloros17-findx9u-in-1105) echo "123 abc" ;;\n'
                    '    esac\n}\n'
                    'set_files() {\n'
                    '    case "$1" in\n'
                    '        x9u-in-1105-sultra) echo "$SET_COMMON" ;;\n'
                    '    esac\n}\n'
                    'set_image() {\n'
                    '    case "$1" in\n'
                    '        x9u-in-1105-sultra) echo coloros17-findx9u-in-1105 ;;\n'
                    '    esac\n}\n'
                    'image_family() {\n'
                    '    case "$1" in\n'
                    '        coloros17-findn6|coloros17-findx9u-in-1105) echo coloros ;;\n'
                    '    esac\n}\n'
                ),
                "switcher/assets/oneos_switch.sh": (
                    'UI_ACTIVATOR="\n'
                    "record_restart_home() {\n"
                    "    old_impl\n"
                    "}\n"
                    "GEN=1\n"
                    "            echo '[UI Activator] WindowManager is ready! Dismissing bootanim and unlocking...'\n"
                    "            echo '[UI Activator] Home activated!'\n"
                    "            (record_restart_home) </dev/null &\n"
                    '"\n'
                ),
                "app-android/app/build.gradle.kts": (
                    'val packageSets = mapOf(\n    "x9u-in-1105-sultra" to Pair(1, 2),\n)\nversionCode = 160\nversionName = "0.5.19"\n'
                ),
                "app-android/guest/build.gradle.kts": 'versionCode = 160\nversionName = "0.5.19"\n',
                "app-android/packages/sizes.txt": "0541e053377589e8bcc036996c0fb8d8f094207bcd9370922141f2c0b44e2f09 8807959\n",
                "app-android/packages/remote.txt": "0541e053377589e8bcc036996c0fb8d8f094207bcd9370922141f2c0b44e2f09 gh:sets-1\n",
                "app-android/packages/series.txt": "s25 hyper-f8u x9u-in-1105-sultra\n",
                "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/ApkSeriesTest.kt": (
                    "class ApkSeriesTest {\n    @Test\n    fun theListBuildsUseIsTheModels() {\n    }\n}\n"
                ),
                "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/ImagesTest.kt": (
                    "class ImagesTest {\n"
                    "    @Test\n"
                    "    fun builtInTable() {\n"
                    "        assertEquals(\n"
                    "            listOf(n6, xin, pixel, Images.PIXEL_KODIAK, Images.PIXEL_KOMODO, Images.INFINITYX, Images.NOTHING_METROID, Images.NOTHING_ASTEROIDS, Images.OXYGEN16, Images.ORIGINOS7, Images.LINEAGE24),\n"
                    "            Images.ALL.filter { it.googleServices }\n"
                    "        )\n"
                    "        assertEquals(listOf(pixel, Images.PIXEL_KODIAK, Images.PIXEL_KOMODO, Images.INFINITYX, Images.NOTHING_ASTEROIDS, Images.ORIGINOS7), Images.ALL.filter { it.presentsGms })\n"
                    "        assertEquals(listOf(n6, xin, Images.NOTHING_METROID, Images.OXYGEN16, Images.LINEAGE24), Images.ALL.filter { it.gmsHideable })\n"
                    "        assertEquals(listOf(n6, xin, pixel, Images.PIXEL_KODIAK, Images.LINEAGE24), Images.ALL)\n"
                    "        assertEquals(listOf(SystemFamily.COLOROS, SystemFamily.COLOROS, SystemFamily.PIXEL), Images.ALL.map { it.family })\n"
                    "        val takes = mapOf(\n"
                    "            Profiles.S_8550 to listOf(xin, Images.PIXEL_KOMODO, ifx, lineage, hyper, Images.OXYGEN16, nothing, origin),\n"
                    "        )\n"
                    "    }\n}\n"
                ),
                "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/PackageSetsTest.kt": (
                    "class PackageSetsTest {\n"
                    "    @Test fun fold8StableInheritsEverySharedPayloadAndGuard() {\n"
                    "        assertEquals(listOf(PackageSets.N6_GLOBAL_F8, n6, PackageSets.X9U_IN_1105_SULTRA, pixel), PackageSets.ALL)\n"
                    "    }\n}\n"
                ),
                "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/ProfilesTest.kt": (
                    "class ProfilesTest {\n"
                    "    @Test\n"
                    "    fun theFourBundlesResolveToTheirProfiles() {\n"
                    "            assertEquals(\n"
                    "                listOf(xin, ImageChoice(Images.ID_PIXEL_KOMODO, PackageSets.ID_PIXEL9_SULTRA, tested = false), ifx, lineage, hyper,\n"
                    "                    ImageChoice(Images.ID_OXYGEN16, PackageSets.ID_OXYGEN16, tested = false), nothing, origin), // 0.5.19: Nothing OS (Metroid), owner\n"
                    "                p.choices,\n"
                    "            )\n"
                    "            assertEquals(Profiles.S_ULTRA_8550.choices, p.choices)\n"
                    "        for (p in offerOrigin) assertEquals(p.id, Images.ID_ORIGINOS7, p.choices.last().imageId)\n"
                    "        assertEquals(\n"
                    "            listOf(Profiles.S_8550.choices, Profiles.S_8550.notes),\n"
                    "            listOf(Profiles.S_8550_JP.choices, Profiles.S_8550_JP.notes),\n"
                    "        )\n"
                    "    }\n}\n"
                ),
                "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/SessionEnvTest.kt": (
                    "class SessionEnvTest {\n"
                    "    fun test() {\n"
                    "        assertEquals(listOf(Images.FIND_N6, Images.FIND_X9U_IN_1105, Images.NOTHING_METROID, Images.OXYGEN16, Images.LINEAGE24), Images.ALL.filter { it.gmsHideable })\n"
                    "    }\n}\n"
                ),
                "app-android/core/root/src/test/kotlin/com/fahadali/oneos/core/root/StageScriptTest.kt": (
                    "class StageScriptTest {\n"
                    "    @Test\n"
                    "    fun verboseBootChoiceIsWrittenOnlyAfterLaunchChecks() {}\n"
                    "}\n"
                ),
            }
            for rel, content in files.items():
                fp = repo / rel
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content, encoding="utf-8")

            meta = repo / "out/twinity-metadata"
            meta.mkdir(parents=True)
            sha_a = "a" * 64
            sha_g = "b" * 64
            (meta / "twinity-4c-s23.sha256").write_text(
                f"{sha_a}  services_patched.jar\n{sha_g}  guards.txt\n0541e053377589e8bcc036996c0fb8d8f094207bcd9370922141f2c0b44e2f09  OneOSGuest.apk\n",
                encoding="utf-8",
            )
            (meta / "twinity-4c-s23.guards.txt").write_text(f"{sha_a}  services_patched.jar\n", encoding="utf-8")
            manifest = {
                "release_tag": "twinity-4c-v1",
                "remote_source": "gh:DeathThoughts/OneOS-test:twinity-4c-v1",
                "guest_image": {
                    "sdk": 37,
                    "build_id": "CP2A.260605.016",
                    "security_patch": "2026-09-01",
                    "gz_name": "OneOS_ColorOS17_OnePlus13T_CN_Twinity4c.img.gz",
                    "gz_bytes": 4_500_000_000,
                    "gz_sha256": "c" * 64,
                    "raw_bytes": 9_126_805_504,
                    "raw_sha256": "d" * 64,
                    "google_services": True,
                    "presents_gms": False,
                },
                "guarded_files": ["services_patched.jar"],
                "payloads": {
                    "services_patched.jar": {"size": 123456, "sha": sha_a},
                    "guards.txt": {"size": 78, "sha": sha_g},
                    "OneOSGuest.apk": {"size": 8807959, "sha": "0541e053377589e8bcc036996c0fb8d8f094207bcd9370922141f2c0b44e2f09"},
                },
            }
            manifest_path = meta / "twinity-4c-manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            wire_mod.wire_repository(repo, manifest_path)
            snap1 = {rel: (repo / rel).read_text(encoding="utf-8") for rel in files}
            wire_mod.wire_repository(repo, manifest_path)
            snap2 = {rel: (repo / rel).read_text(encoding="utf-8") for rel in files}
            self.assertEqual(snap1, snap2)

            rsh_after = (repo / "app-android/app/src/full/assets/root/oneos_root.sh").read_text(encoding="utf-8")
            self.assertIn("SET_TWINITY=", rsh_after)
            self.assertIn("coloros17-findx9u-in-1105 coloros17-twinity-4c nothingos5-metroid", rsh_after)
            self.assertIn(
                "Images.COLOROS_TWINITY_4C",
                (repo / "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/SessionEnvTest.kt").read_text(encoding="utf-8"),
            )
            subprocess.run([sys.executable, str(repo / "scripts/test-restart-home.py")], cwd=repo, check=True)



class RealImageResolverGuardTest(unittest.TestCase):
    def test_rejects_non_ext4_before_touching_overlays(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            img = root / "system.img"
            img.write_bytes(b"not a real filesystem" + bytes(2048))
            overlays = root / "make-overlays.sh"
            original = "BUILDS = [\\n]\\nSITES = {}\\n"
            overlays.write_text(original)
            with self.assertRaisesRegex(ValueError, "raw ext4"):
                twinity_elf_sites.resolve_and_update_for_image(
                    system_img=img, make_overlays_path=overlays,
                    build_label="Twinity invalid", dexdump_path="unused",
                    debugfs_path="unused", extract_erofs_path="unused")
            self.assertEqual(original, overlays.read_text())

    def test_missing_native_files_never_changes_overlays(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            img = root / "system.img"
            raw = bytearray(2048)
            raw[1080:1082] = bytes.fromhex("53ef")
            img.write_bytes(raw)
            overlays = root / "make-overlays.sh"
            original = "BUILDS = [\\n]\\nSITES = {}\\n"
            overlays.write_text(original)
            with patch.object(subprocess, "run") as fake:
                fake.return_value = subprocess.CompletedProcess([], 0, "", "")
                with self.assertRaisesRegex(ValueError, "Missing native"):
                    twinity_elf_sites.resolve_and_update_for_image(
                        system_img=img, make_overlays_path=overlays,
                        build_label="Twinity incomplete", dexdump_path="unused",
                        debugfs_path="unused", extract_erofs_path="unused")
            self.assertEqual(original, overlays.read_text())



    def test_resolves_mock_image_and_registers_sites_idempotently(self):
        from unittest.mock import patch
        build = TwinityElfSitesTest._build_minimal_elf64
        rodata = b"prefix\x00vold\x00suffix"
        adrp = 0x90000081
        add = 0x91000021 | (7 << 10)
        vold_code = struct.pack("<6I", adrp, add, 0xF9402509, 0xD63F0120,
                                0x2A0003F3, 0x360006B3) + b"\x1f\x20\x03\xd5" * 60
        vold = build(0x20000, rodata, 0x10000, vold_code)
        incremental = build(0x20000, rodata, 0x10000,
                            struct.pack("<2I", adrp, add) + b"\x1f\x20\x03\xd5" * 4)
        sf_code = bytearray(0x200)
        struct.pack_into("<4I", sf_code, 0, 0x94000040, 0xF94007E8,
                         0xB4000268, 0x94000041)
        sf = build(0x20000, b"rodata\x00", 0x10000, bytes(sf_code), dynsym_names=[
            ("AIBinder_getExtension", 0x10100),
            ("_ZN4aidl6vendor3qti8hardware7display8composer20IQtiComposer3Client10fromBinderERKNS_7android2os11SpAIBinderE", 0x10110),
        ])
        fixtures = {
            "/system/bin/vold": vold,
            "/system/lib64/service.incremental.so": incremental,
            "/system/bin/surfaceflinger": sf,
            "/system/apex/com.android.crashrecovery.apex": b"synthetic-apex",
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            image = root / "system.img"
            header = bytearray(2048)
            header[1080:1082] = bytes.fromhex("53ef")
            image.write_bytes(header)
            overlay = root / "make-overlays.sh"
            overlay.write_text("BUILDS = [\n]\nSITES = {}\n", encoding="utf-8")

            def fake_debugfs(command, **kwargs):
                _, source, dest = command[2].split(" ", 2)
                payload = fixtures.get(source)
                if payload is not None:
                    Path(dest).write_bytes(payload)
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(subprocess, "run", side_effect=fake_debugfs):
                args = dict(system_img=image, make_overlays_path=overlay,
                            build_label="Synthetic Twinity", dexdump_path="unused",
                            debugfs_path="fake-debugfs", extract_erofs_path="unused")
                first = twinity_elf_sites.resolve_and_update_for_image(**args)
                initial = overlay.read_text(encoding="utf-8")
                second = twinity_elf_sites.resolve_and_update_for_image(**args)
            self.assertEqual(0x1006, first["vold_name_offset"])
            self.assertEqual(0x2014, first["vold_try_start_offset"])
            self.assertEqual(0x2008, first["surfaceflinger_cbz_offset"])
            self.assertTrue(first["overlay_table_updated"])
            self.assertFalse(second["overlay_table_updated"])
            self.assertEqual(initial, overlay.read_text(encoding="utf-8"))
            self.assertIn("Synthetic Twinity", initial)


if __name__ == "__main__":
    unittest.main()
