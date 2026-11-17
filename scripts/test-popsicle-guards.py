#!/usr/bin/env python3
"""Exercise the Popsicle guard changes with synthetic decoded smali."""
import contextlib
import hashlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest import mock

PATCHES = Path(__file__).resolve().parents[1] / "switcher" / "patches"


def load_patch(name):
    spec = importlib.util.spec_from_file_location(name, PATCHES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


miui = load_patch("miui_guards")
hal = load_patch("hyperos_hal_ext")

TRANSACT = "    invoke-interface {v0, v1, v2, v3, v4}, Landroid/os/IBinder;->transact(ILandroid/os/Parcel;Landroid/os/Parcel;I)Z\n"
WIFI_CLASS = "com/android/server/wifi/WifiOptimizationImpl"
WIFI_IFACE = "com/xiaomi/net/ip/IMiuiIpClientExtension"
WIFI_CAST = f"    invoke-static {{v2}}, L{WIFI_IFACE}$Stub;->asInterface(Landroid/os/IBinder;)L{WIFI_IFACE};\n"
WIFI_METHOD = f""".method private getMiuiExtension(Landroid/os/IBinder;)L{WIFI_IFACE};
    .locals 3

    :try_start
    invoke-interface {{p1}}, Landroid/os/IBinder;->getExtension()Landroid/os/IBinder;

    move-result-object v2

    .line 2176
    .local v2, "ext":Landroid/os/IBinder;
    if-nez v2, :cond_extension

    const/4 v0, 0x0

    return-object v0

    :cond_extension
{WIFI_CAST}
    move-result-object v0
    :try_end
    .catch Landroid/os/RemoteException; {{:try_start .. :try_end}} :error

    return-object v0

    :error
    move-exception v1

    const/4 v0, 0x0

    return-object v0
.end method"""


def method(header, code="    return-void\n"):
    return f"{header}\n    .locals 5\n\n{code}.end method\n"


class PopsicleGuardsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def add_class(self, cls, body):
        path = self.root / "smali" / f"{cls}.smali"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f".class public L{cls};\n.super Ljava/lang/Object;\n\n{body}")
        return path

    def snapshot(self):
        return {p.relative_to(self.root): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def run_cli(self, patch, *args):
        return subprocess.run([sys.executable, "-I", PATCHES / f"{patch}.py", *args],
                              capture_output=True, text=True)

    def service_fixture(self, count):
        classes = defaultdict(list)
        for cls, header, _ in miui.RETURN_AT_ENTRY:
            classes[cls].append(method(header))
        classes[miui.POWERCTL_CLASS].append(miui.POWERCTL + "\n")
        classes[miui.VOLD_CLASS].append(
            "    invoke-interface {v0, p1}, Landroid/os/IVold;->setDiscardIoAwareGran(I)V\n")
        classes[miui.BOOTANIM_CLASS].append(
            '    const-string v0, "service.bootanim.exit"\n'
            '    const-string v1, "0"\n'
            '    invoke-static {v0, v1}, Landroid/os/SystemProperties;->set(Ljava/lang/String;Ljava/lang/String;)V\n'
            '    const-string v0, "ctl.start"\n'
            '    const-string v1, "bootanim"\n'
            '    invoke-static {v0, v1}, Landroid/os/SystemProperties;->set(Ljava/lang/String;Ljava/lang/String;)V\n')
        for index in range(28):
            classes[miui.OEMNETD_CLASS].append(method(
                f".method public useNetd{index}()V",
                "    invoke-interface {v0}, Lcom/android/internal/net/IOemNetd;->isAlive()Z\n"
                "    return-void\n"))
        for cls, methods in classes.items():
            self.add_class(cls, "\n".join(methods))

        headers = [f".method public transaction{index:02d}()V" for index in range(count)]
        proxy = self.add_class(miui.OEMNETD_PROXY,
                               method(".method public isAlive()Z", TRANSACT + "    return v0\n")
                               + "\n".join(method(h, TRANSACT + "    return-void\n") for h in reversed(headers)))
        fingerprint = hashlib.sha256(("\n".join(headers) + "\n").encode("utf-8")).hexdigest()
        return proxy, fingerprint

    def test_default_service_blocks_54_methods_and_retains_is_alive(self):
        proxy, _ = self.service_fixture(54)
        done = self.run_cli("miui_guards", self.root)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("smali\n", done.stdout)
        self.assertEqual(54, proxy.read_text().count("    throw v0\n"))
        self.assertEqual(1, proxy.read_text().count(TRANSACT))

    def test_default_service_rejects_48_methods_without_writing(self):
        self.service_fixture(48)
        before = self.snapshot()
        done = self.run_cli("miui_guards", self.root)
        self.assertNotEqual(0, done.returncode)
        self.assertIn("48 methods to block, expected 54", done.stderr)
        self.assertEqual(before, self.snapshot())

    def test_popsicle_service_checks_sorted_headers_and_keeps_one_transaction(self):
        proxy, fingerprint = self.service_fixture(48)
        with mock.patch.object(miui, "POPSICLE_PROXY_HEADERS_SHA256", fingerprint), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            miui.main(self.root, variant="popsicle")
        self.assertEqual(48, proxy.read_text().count("    throw v0\n"))
        self.assertEqual(1, proxy.read_text().count(TRANSACT))

    def test_popsicle_service_rejects_a_changed_header_without_writing(self):
        proxy, fingerprint = self.service_fixture(48)
        proxy.write_text(proxy.read_text().replace("transaction06", "changedTransaction06"))
        before = self.snapshot()
        with mock.patch.object(miui, "POPSICLE_PROXY_HEADERS_SHA256", fingerprint), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(SystemExit, "fingerprint mismatch"):
            miui.main(self.root, variant="popsicle")
        self.assertEqual(before, self.snapshot())

    def test_proinstaller_still_works_and_rejects_variant(self):
        cls = miui.PROINSTALLER[0][0]
        path = self.add_class(cls, "\n".join(method(header) for _, header, _ in miui.PROINSTALLER))
        before = self.snapshot()
        done = self.run_cli("miui_guards", "--proinstaller", "--variant", "popsicle", self.root)
        self.assertNotEqual(0, done.returncode)
        self.assertIn("not allowed with argument --proinstaller", done.stderr)
        self.assertEqual(before, self.snapshot())

        done = self.run_cli("miui_guards", "--proinstaller", self.root)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(2, path.read_text().count("# oneos: returns at entry"))

    def adjacent_wifi_fixture(self, count=5):
        return self.add_class("AdjacentCasts", "\n".join(
            method(f".method public extension{index}()V",
                   "    invoke-interface {v0}, Landroid/os/IBinder;->getExtension()Landroid/os/IBinder;\n"
                   "\n    move-result-object v0\n"
                   "\n    invoke-static {v0}, Lvendor/IWifi$Stub;->asInterface(Landroid/os/IBinder;)Lvendor/IWifi;\n"
                   "    return-void\n") for index in range(count)))

    def run_wifi(self, count=6):
        fingerprint = hashlib.sha256(WIFI_METHOD.encode("utf-8")).hexdigest()
        with mock.patch.object(hal, "POPSICLE_WIFI_METHOD_SHA256", fingerprint), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            hal.main(["--expect", str(count), str(self.root), "HalExtWifi"])

    def test_original_wifi_guards_five_adjacent_casts(self):
        adjacent = self.adjacent_wifi_fixture()
        done = self.run_cli("hyperos_hal_ext", "--expect", "5", self.root, "HalExtWifi")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(5, adjacent.read_text().count("Lcom/oneos/guest/HalExtWifi;->ext0("))

    def test_popsicle_wifi_checks_v2_immediately_before_as_interface(self):
        adjacent = self.adjacent_wifi_fixture()
        wifi = self.add_class(WIFI_CLASS, WIFI_METHOD + "\n")
        original = wifi.read_text()
        self.run_wifi()
        call = ("\n    invoke-static {v2}, Lcom/oneos/guest/HalExtWifi;->ext1(Landroid/os/IBinder;)Landroid/os/IBinder;\n"
                "\n    move-result-object v2\n\n")
        self.assertEqual(original.replace(WIFI_CAST, call + WIFI_CAST), wifi.read_text())
        self.assertEqual(5, adjacent.read_text().count("Lcom/oneos/guest/HalExtWifi;->ext0("))
        helper = self.root / "smali/com/oneos/guest/HalExtWifi.smali"
        self.assertIn(f"sget-object v1, L{WIFI_IFACE};->DESCRIPTOR:Ljava/lang/String;", helper.read_text())

    def test_popsicle_wifi_rejects_changed_method_bytes_without_writing(self):
        self.adjacent_wifi_fixture()
        for replacement in (".line 2177\n", ".line 2176\r\n"):
            with self.subTest(replacement=replacement):
                self.add_class(WIFI_CLASS, WIFI_METHOD.replace(".line 2176\n", replacement) + "\n")
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "fingerprint mismatch"):
                    self.run_wifi()
                self.assertEqual(before, self.snapshot())

    def test_popsicle_wifi_rejects_unrecognized_call_without_writing(self):
        self.adjacent_wifi_fixture()
        self.add_class(WIFI_CLASS, WIFI_METHOD + "\n")
        self.add_class("zzz/Unrecognized", WIFI_METHOD + "\n")
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "Unrecognized.smali: 1 getExtension"):
            self.run_wifi()
        self.assertEqual(before, self.snapshot())

    def test_popsicle_wifi_exception_does_not_apply_to_five_cast_mode(self):
        self.adjacent_wifi_fixture(count=4)
        self.add_class(WIFI_CLASS, WIFI_METHOD + "\n")
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "WifiOptimizationImpl.smali: 1 getExtension"):
            self.run_wifi(count=5)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
