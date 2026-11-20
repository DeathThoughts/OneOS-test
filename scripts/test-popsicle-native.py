#!/usr/bin/env python3
"""Exercise the Popsicle native patchers with synthetic inputs instead of ROM files."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SF_SCRIPT = (ROOT / "switcher/patches/hyperos_sf_group.py").read_text()
VOLD_SCRIPT = (ROOT / "scripts/make-overlays.sh").read_text().split("<<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]

SF_SHA = "54c8cd4021824244f717759c5bf20a9cee9f4b56296213d9a05e5f0c35605089"
VOLD_SHAS = {
    "vold": "f93d8d75894dacf39842fd349c7c271f18794dec0a3a450d56b39464b89273e4",
    "service.incremental.so": "2a8a3ff60d47b438acef3338d2f28ea13cc54cce1cad5d8bd55aa68f4adb7caa",
    "crashrecovery-aosp.capex": "c1babb552c0e161e7cbaa3b26c30e23a870f76f352a6e3c19a17f2e01a70131f",
}
SF_SITES = ((0x4A1900, "e9c24039", "e93240b9"), (0x4B3210, "08c14039", "083140b9"))
VOLD_SITES = {
    "vold": ((0xF45C, "00766f6c6400", "0067766c6400"), (0x6CE00, "b3060036", "35000014")),
    "service.incremental.so": ((0x64785, "00766f6c6400", "0067766c6400"),),
    "crashrecovery-aosp.capex": (),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fixture(sites, replacement=False):
    size = max((offset + len(bytes.fromhex(old)) for offset, old, _ in sites), default=0)
    data = bytearray(b"\xcd" * (size + 32))
    for offset, old, new in sites:
        value = bytes.fromhex(new if replacement else old)
        data[offset:offset + len(value)] = value
    return bytes(data)


def corrupt(data, offset):
    return data[:offset] + bytes((data[offset] ^ 1,)) + data[offset + 1:]


class PopsicleNative(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        self.sf_input = self.work / "libsurfaceflinger.so"
        self.sf_out = self.work / "libsurfaceflinger_patched.so"
        self.vold_out = self.work / "vold_patched"
        self.inc_out = self.work / "service.incremental_patched.so"

    def run_patch(self, script, args, pins=()):
        # Keep the real selection and byte checks. Substitute only the digests
        # in the subprocess's copy so synthetic inputs can match the build.
        for stock, test in pins:
            self.assertEqual(1, script.count(stock))
            script = script.replace(stock, sha(test))
        return subprocess.run([sys.executable, "-I", "-", *map(str, args)], input=script,
                              text=True, capture_output=True)

    def run_sf(self, data, pins=()):
        self.sf_input.write_bytes(data)
        result = self.run_patch(SF_SCRIPT, (self.sf_input, self.sf_out), pins)
        self.assertEqual(sha(data), sha(self.sf_input.read_bytes()))
        return result

    def run_vold(self, inputs, pins=(), madrid=False):
        for name, data in inputs.items():
            (self.work / name).write_bytes(data)
        result = self.run_patch(VOLD_SCRIPT, (self.work, self.vold_out, self.inc_out, int(madrid)), pins)
        for name, data in inputs.items():
            self.assertEqual(sha(data), sha((self.work / name).read_bytes()), name)
        return result

    def test_surfaceflinger_changes_only_the_two_group_loads(self):
        data = fixture(SF_SITES)
        result = self.run_sf(data, ((SF_SHA, data),))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(sha(fixture(SF_SITES, replacement=True)), sha(self.sf_out.read_bytes()))

    def test_surfaceflinger_refuses_unpinned_and_modified_files(self):
        data = fixture(SF_SITES)
        for submitted, pins in ((data, ()), (corrupt(data, 0), ((SF_SHA, data),)),
                                (data[:-1], ((SF_SHA, data),))):
            result = self.run_sf(submitted, pins)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("not the library this patch was made for", result.stderr)
            self.assertFalse(self.sf_out.exists())

    def test_surfaceflinger_checks_both_instruction_sites_after_hash_match(self):
        data = fixture(SF_SITES)
        for offset, _, _ in SF_SITES:
            for bad in (corrupt(data, offset), data[:offset + 3]):
                with self.subTest(offset=hex(offset), size=len(bad)):
                    result = self.run_sf(bad, ((SF_SHA, bad),))
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(f"unexpected bytes at {offset:#x}", result.stderr)
                    self.assertFalse(self.sf_out.exists())

    def test_vold_and_incremental_change_only_the_pinned_sites(self):
        inputs = {name: fixture(sites) for name, sites in VOLD_SITES.items()}
        pins = [(stock, inputs[name]) for name, stock in VOLD_SHAS.items()]
        result = self.run_vold(inputs, pins)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("HyperOS OS4.0 Popsicle OS4.0.0.39.XPBCNXM", result.stdout)
        self.assertEqual(sha(fixture(VOLD_SITES["vold"], replacement=True)), sha(self.vold_out.read_bytes()))
        self.assertEqual(sha(fixture(VOLD_SITES["service.incremental.so"], replacement=True)),
                         sha(self.inc_out.read_bytes()))

    def test_vold_requires_all_three_hashes(self):
        inputs = {name: fixture(sites) for name, sites in VOLD_SITES.items()}
        pins = [(stock, inputs[name]) for name, stock in VOLD_SHAS.items()]
        for name, data in inputs.items():
            for bad in (corrupt(data, 0), data[:-1]):
                with self.subTest(name=name, size=len(bad)):
                    result = self.run_vold({**inputs, name: bad}, pins)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertIn("not a build this was written against", result.stdout)
                    self.assertFalse(self.vold_out.exists())
                    self.assertFalse(self.inc_out.exists())

    def test_madrid_vold_requires_madrid_native_pins(self):
        inputs = {name: fixture(sites) for name, sites in VOLD_SITES.items()}
        pins = [(stock, inputs[name]) for name, stock in VOLD_SHAS.items()]
        result = self.run_vold(inputs, pins, madrid=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--madrid requires the pinned Madrid", result.stderr)
        self.assertFalse(self.vold_out.exists())
        self.assertFalse(self.inc_out.exists())

    def test_vold_checks_every_patch_site_before_writing_either_output(self):
        inputs = {name: fixture(sites) for name, sites in VOLD_SITES.items()}
        for name, sites in VOLD_SITES.items():
            for offset, old, _ in sites:
                for bad in (corrupt(inputs[name], offset), inputs[name][:offset + len(bytes.fromhex(old)) - 1]):
                    with self.subTest(name=name, offset=hex(offset), size=len(bad)):
                        submitted = {**inputs, name: bad}
                        pins = [(stock, submitted[key]) for key, stock in VOLD_SHAS.items()]
                        result = self.run_vold(submitted, pins)
                        self.assertNotEqual(0, result.returncode)
                        self.assertIn(f"{name}: unexpected bytes at {offset:#x}", result.stderr)
                        self.assertFalse(self.vold_out.exists())
                        self.assertFalse(self.inc_out.exists())


if __name__ == "__main__":
    unittest.main()
