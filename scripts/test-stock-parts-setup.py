#!/usr/bin/env python3
"""Exercise the root stock snapshot operation with small local device fixtures."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'app-android/app/src/full/assets/root/oneos_root.sh').read_text()
FUNCTION = SCRIPT[SCRIPT.index('do_stock_parts() {'):SCRIPT.index('# --- Main ---')]
HELPER = (ROOT / 'switcher/assets/stock_parts.sh').read_text()


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stage = Path(self.tmp.name)
        for name in ('vendor', 'odm', 'system.raw'):
            (self.stage / name).write_bytes(name.encode() * 50)
        self.function = FUNCTION
        self.helper = HELPER
        for name, sha, size in (
            ('vendor', '3ace2530806e1eeba86007eab46a4fbebe607caf04ea73ce6cbb2ec6a17c63e7', 1907937280),
            ('odm', '1c1e9e3f95fa3e635dcfbb63e37a76fc40f60014b6a6bc8c8ed7e1457d29f4de', 778240),
        ):
            data = (self.stage / name).read_bytes()
            self.helper = self.helper.replace(sha, hashlib.sha256(data).hexdigest()).replace(str(size), str(len(data)))
        (self.stage / 'guest_partitions.sh').write_text('''oneos_parts_file() { [ -f "$1" ]; }
oneos_parts_vendor_host() { [ "$REJECT" != vendor ] && OP_HOST_DEV="$STAGE/vendor"; }
oneos_parts_odm_host() { [ "$REJECT" != odm ] && OP_HOST_DEV="$STAGE/odm"; }
''')
        (self.stage / 'stock_parts.sh').write_text(self.helper.replace('[ "$(id -u)" = 0 ]', '[ 1 = 1 ]').replace('/data/local/tmp/oneos', str(self.stage)))
        (self.stage / 'bb').write_text('#!/bin/sh\nshift\nexec /bin/sh "$@"\n')
        (self.stage / 'bb').chmod(0o700)
        self.prefix = '''usage() { exit 2; }; refuse() { echo "$*"; exit 3; }; die() { echo "$*"; exit 4; }
say() { echo "$*"; }
read_marker() { return 0; }
set_files() { :; }
sha() { sha256sum "$1" | cut -d " " -f1; }
is_hex64() { [ ${#1} = 64 ]; }
idle_or_refuse() { [ "$REJECT" != busy ] || refuse busy; }
check_files() { [ "$REJECT" != scripts ] || refuse scripts; }
find_tools() { BB="$STAGE/bb"; }
to_attic() { [ ! -e "$STAGE/$1" ] || mv "$STAGE/$1" "$STAGE/saved"; }
'''

    def run_setup(self, mode='on', reject=''):
        return subprocess.run(['sh', '-c', self.prefix + self.function + '\ndo_stock_parts "$MODE"'],
                              env=dict(os.environ, STAGE=str(self.stage), LOCK=str(self.stage / 'lock'),
                                       REJECT=reject, MODE=mode), capture_output=True, text=True, timeout=10)

    def test_snapshots_and_manifest_published_together(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        bundle = self.stage / 'partition_images'
        for name in ('vendor', 'odm'):
            self.assertEqual((bundle / f'{name}.img').read_bytes(), (self.stage / name).read_bytes())
            self.assertEqual((bundle / f'{name}.img').stat().st_mode & 0o777, 0o600)
        self.assertEqual(bundle.stat().st_mode & 0o777, 0o700)
        self.assertIn(hashlib.sha256((self.stage / 'system.raw').read_bytes()).hexdigest(), (bundle / 'manifest').read_text())
        self.assertEqual((bundle / 'manifest').read_text().count(' stock\n'), 2)
        self.assertFalse(list(self.stage.glob('.stock-parts.*')))

    def test_refusals_never_publish(self):
        for reject in ('busy', 'scripts', 'vendor', 'odm'):
            with self.subTest(reject=reject):
                self.assertNotEqual(self.run_setup(reject=reject).returncode, 0)
                self.assertFalse((self.stage / 'partition_images').exists())
                self.assertFalse(list(self.stage.glob('.stock-parts.*')))

    def test_existing_bundle_retained(self):
        self.assertEqual(self.run_setup().returncode, 0)
        self.assertNotEqual(self.run_setup().returncode, 0)
        self.assertTrue((self.stage / 'partition_images/manifest').exists())

    def test_off_preserves_disabled_bundle(self):
        self.assertEqual(self.run_setup().returncode, 0)
        self.assertEqual(self.run_setup('off').returncode, 0)
        self.assertFalse((self.stage / 'partition_images').exists())
        self.assertTrue((self.stage / 'saved/manifest').exists())

    def test_root_dispatch_takes_normal_lock(self):
        self.assertIn('stock-parts|begin|receive', SCRIPT)
        self.assertIn('stock-parts) do_stock_parts "$@"', SCRIPT)


if __name__ == '__main__':
    unittest.main()
