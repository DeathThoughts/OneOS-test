#!/usr/bin/env python3
"""guest_props.sh names a fingerprint vendor for HyperOS, also over an empty host value.

HyperOS reports no fingerprint hardware unless persist.vendor.sys.fp.vendor names
one. A Galaxy S23 HyperOS session (11-15) found the name defined in One UI with
no value, left it alone, and every fingerprint call failed isHardwareDetected.
Runs the real block with stand-ins for getprop and resetprop.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROPS = (ROOT / 'switcher/assets/guest_props.sh').read_text()


def block():
    start = PROPS.index('    if [ -e "$ROOTFS/system/system_ext/framework/miui-services.jar" ] || [ -e "$ROOTFS/system_ext/framework/miui-services.jar" ]; then\n        k=persist.vendor.sys.fp.vendor')
    end = PROPS.index('\n    fi\n', PROPS.index('k=persist.vendor.sys.fp.vendor', start)) + len('\n    fi\n')
    return PROPS[start:end]


class FpVendorTest(unittest.TestCase):
    def run_block(self, host_has, host_value, miui=True, rp_ok=True):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            rootfs = r / 'rootfs'
            (rootfs / 'system/system_ext/framework').mkdir(parents=True)
            if miui:
                (rootfs / 'system/system_ext/framework/miui-services.jar').write_text('x')
            host = r / 'host'
            host.write_text('persist.vendor.sys.fp.vendor\n' if host_has else 'ro.other\n')
            new = r / 'new'
            new.write_text('persist.vendor.sys.fp.vendor=old\n')
            ovr = r / 'ovr'
            ovr.write_text('')
            rp = r / 'rp'
            rp.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {r}/rp.log\nexit {0 if rp_ok else 1}\n')
            os.chmod(rp, 0o755)
            script = (f'getprop() {{ printf "%s" "{host_value}"; }}\n'
                      f'ROOTFS={rootfs}; HOST={host}; NEW={new}; OVR={ovr}; RP={rp}\n' + block())
            done = subprocess.run(['sh', '-c', script], capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stderr)
            rp_log = (r / 'rp.log').read_text() if (r / 'rp.log').exists() else ''
            return done.stdout, new.read_text(), ovr.read_text(), rp_log

    def test_empty_host_value_is_overridden_and_restored_later(self):
        out, new, ovr, rp = self.run_block(True, '')
        self.assertIn("overridden to goodix", out)
        self.assertEqual('-n persist.vendor.sys.fp.vendor goodix\n', rp)
        self.assertEqual('persist.vendor.sys.fp.vendor=\n', ovr)

    def test_none_counts_as_empty(self):
        out, new, ovr, rp = self.run_block(True, 'none')
        self.assertIn('goodix', rp)
        self.assertEqual('persist.vendor.sys.fp.vendor=none\n', ovr)

    def test_a_real_host_vendor_is_left_alone(self):
        out, new, ovr, rp = self.run_block(True, 'fpc')
        self.assertIn("is One UI's (fpc), left alone", out)
        self.assertEqual('', rp)
        self.assertEqual('', ovr)

    def test_no_host_name_goes_in_the_guest_file(self):
        out, new, ovr, rp = self.run_block(False, '')
        self.assertIn('persist.vendor.sys.fp.vendor=goodix', new)
        self.assertNotIn('=old', new)
        self.assertEqual('', rp)

    def test_a_refused_override_is_said(self):
        out, new, ovr, rp = self.run_block(True, '', rp_ok=False)
        self.assertIn('could not be set', out)
        self.assertEqual('', ovr)

    def test_not_hyperos_does_nothing(self):
        out, new, ovr, rp = self.run_block(True, '', miui=False)
        self.assertEqual('', out)
        self.assertEqual('', rp)


if __name__ == '__main__':
    unittest.main()
