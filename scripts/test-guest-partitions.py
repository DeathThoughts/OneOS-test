#!/usr/bin/env python3
"""Exercise validation and mount failure handling without mounting anything."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'switcher/assets/guest_partitions.sh'
spec = importlib.util.spec_from_file_location('bundle', ROOT / 'scripts/prepare-guest-partitions.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def image(p):
    with p.open('wb') as f:
        f.truncate(4096)
        f.seek(1080)
        f.write(b'\x53\xef')
    p.chmod(0o600)
    return p


class Partitions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stage = Path(self.tmp.name)
        self.system = image(self.stage / 'system.raw')
        self.product = image(self.stage / 'product.raw')
        self.ext = image(self.stage / 'ext.raw')
        self.parts = self.stage / 'partition_images'
        self.log = self.stage / 'calls'
        self.bb = self.stage / 'bb'
        self.bb.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CALL_LOG"
case "$1" in
 blkid) printf '%s TYPE="ext4"\\n' "$2" ;;
 losetup)
  case "$2" in
   -f) echo /dev/null ;;
   -r) [ "$FAIL_ATTACH" != 1 ] ;;
  esac ;;
 mount)
  case "$2" in
   -t) [ "$FAIL_MOUNT" != 1 ] ;;
  esac ;;
esac
''')
        self.bb.chmod(0o700)

    def make(self, names=('product',), active=()):
        bundle.build(self.system, {n: self.product if n == 'product' else self.ext for n in names},
                     set(active), self.parts)

    def run_shell(self, action='oneos_parts_validate', prefix=''):
        # Override read-only sysfs queries and mountinfo only; no real mount backend.
        script = '''. "$PART_HELPER"
# Model Android's root-owned stage on hosts where tests run as an ordinary user.
stat() {
 if [ "$1" = -c ] && [ "$2" = %u ]; then echo "${TEST_UID:-0}"; else command stat "$@"; fi
}
awk() {
 case "$*" in
  */proc/self/mountinfo*)
   case "$*" in
    *'index($5,p'*) return 1 ;;
    *) return 0 ;;
   esac ;;
  *) command awk "$@" ;;
 esac
}
cat() { case "$1" in /sys/dev/block/*/ro) echo 1 ;; *) command cat "$@" ;; esac; }
''' + prefix + '\n' + action
        env = dict(os.environ, STAGE=str(self.stage), ROOTFS=str(self.stage / 'rootfs'),
                   BB=str(self.bb), CALL_LOG=str(self.log), PART_HELPER=str(HELPER))
        return subprocess.run(['sh', '-c', script], env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)

    def assert_refused(self):
        run = self.run_shell()
        self.assertNotEqual(run.returncode, 0, run.stdout)
        calls = self.log.read_text() if self.log.exists() else ''
        self.assertNotIn('losetup', calls)
        self.assertNotIn('mount ', calls)

    def test_absent_bundle_is_noop(self):
        self.assertEqual(self.run_shell().returncode, 0)
        self.assertFalse(self.log.exists())

    def test_verified_hardware_images_do_not_mount(self):
        self.make(('vendor', 'odm', 'vendor_dlkm', 'system_dlkm'))
        (self.stage / 'rootfs').mkdir()
        run = self.run_shell('oneos_parts_mount')
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertNotIn('losetup', self.log.read_text())
        self.assertNotIn('mount -t', self.log.read_text())

    def test_tampered_image(self):
        self.make()
        (self.parts / 'product.img').write_bytes(b'changed')
        self.assert_refused()

    def test_last_record_without_newline_is_still_verified(self):
        self.make()
        manifest = self.parts / 'manifest'
        manifest.write_text(manifest.read_text().rstrip('\n'))
        (self.parts / 'product.img').write_bytes(b'tampered')
        self.assert_refused()

    def test_wrong_base(self):
        self.make()
        self.system.write_bytes(b'another system')
        self.assert_refused()

    def test_forbidden_activation_even_in_manually_edited_manifest(self):
        self.make(('vendor',))
        p = self.parts / 'manifest'
        p.write_text(p.read_text().replace('inspect', 'activate'))
        self.assert_refused()

    def test_traversal_duplicate_and_unknown_partition(self):
        for name in ('../vendor', 'boot', 'super', 'product'):
            with self.subTest(name=name):
                if self.parts.exists():
                    import shutil
                    shutil.rmtree(self.parts)
                self.make()
                p = self.parts / 'manifest'
                record = p.read_text().splitlines()[1].replace('product', name, 1)
                p.write_text(p.read_text() + record + '\n')
                self.assert_refused()

    def test_symlink_image(self):
        self.make()
        p = self.parts / 'product.img'
        p.unlink()
        p.symlink_to(self.product)
        self.assert_refused()

    def test_symlink_directory(self):
        self.make()
        target = self.stage / 'elsewhere'
        self.parts.rename(target)
        self.parts.symlink_to(target)
        self.assert_refused()

    def test_writable_manifest_and_directory(self):
        self.make()
        (self.parts / 'manifest').chmod(0o666)
        self.assert_refused()
        (self.parts / 'manifest').chmod(0o600)
        self.parts.chmod(0o777)
        self.assert_refused()

    def test_nonroot_owner_refused(self):
        self.make()
        run = self.run_shell(prefix='export TEST_UID=1000')
        self.assertNotEqual(run.returncode, 0, run.stdout)
        self.assertFalse(self.log.exists())

    def test_wrong_filesystem_probe(self):
        self.make()
        self.bb.write_text('#!/bin/sh\necho \'/dev/test TYPE="f2fs"\'\n')
        self.assert_refused()

    def target(self, layout):
        root = self.stage / 'rootfs'
        (root / 'system').mkdir(parents=True)
        if layout == 'merged':
            (root / 'system/product').mkdir()
            (root / 'product').symlink_to('/system/product')
        elif layout == 'split':
            (root / 'product').mkdir()
            (root / 'system/product').symlink_to('/product')
        elif layout == 'escape':
            (root / 'product').symlink_to('/vendor')
        return root

    def test_allowed_layouts_and_readonly_mount_flags(self):
        for layout in ('merged', 'split'):
            with self.subTest(layout=layout):
                if (self.stage / 'rootfs').exists():
                    import shutil
                    shutil.rmtree(self.stage / 'rootfs')
                root = self.target(layout)
                if not self.parts.exists():
                    self.make(active=('product',))
                run = self.run_shell('oneos_parts_mount')
                self.assertEqual(run.returncode, 0, run.stdout)
                calls = self.log.read_text()
                self.assertIn('losetup -r /dev/null ', calls)
                self.assertIn('mount -t ext4 -o ro,nodev,nosuid,noload /dev/null ', calls)
                expected = root / ('system/product' if layout == 'merged' else 'product')
                self.assertIn(str(expected), calls)

    def test_partition_layouts_share_one_runtime_target(self):
        root = self.stage / 'rootfs'
        nested = root / 'system/system_ext'
        nested.mkdir(parents=True)
        top = root / 'system_ext'
        cmd = 'oneos_parts_target system_ext && echo "$OP_TARGET"'
        self.assertEqual(self.run_shell(cmd).stdout.strip(), str(nested))
        top.symlink_to('system/system_ext')
        self.assertEqual(self.run_shell(cmd).stdout.strip(), str(nested))
        top.unlink()
        top.mkdir()
        self.assertEqual(self.run_shell(cmd).stdout.strip(), str(top))
        self.assertEqual(self.run_shell(cmd, 'SEXT_MOUNT=1').stdout.strip(), str(nested))
        nested.rmdir()
        nested.symlink_to('../system_ext')
        self.assertEqual(self.run_shell(cmd).stdout.strip(), str(top))
        nested.unlink()
        nested.symlink_to('/vendor')
        self.assertNotEqual(self.run_shell(cmd).returncode, 0)

    def test_root_overlay_precedes_optional_mounts_and_host_binds(self):
        setup = HELPER.with_name('guest_prepare.sh').read_text()
        self.assertLess(setup.index('SEXT_MOUNT=1'), setup.index('oneos_parts_mount ||'))
        self.assertLess(setup.index('oneos_parts_mount ||'), setup.index('Setting up Core System Bind Mounts'))

    def test_image_headers_match_the_offline_reader(self):
        raw = bytearray(4096)
        raw[1024:1028] = bytes.fromhex('e2e1f5e0')
        for prefix, ext, size, expected in (
            (b'RAW0', b'00', 4096, 'erofs'), (b'RAW0', b'00', 1081, None),
            (b'RAW0', bytes.fromhex('53ef'), 4096, None),
            (bytes.fromhex('3aff26ed'), b'00', 4096, None),
            (b'ANDR', b'00', 4096, None), (b'VNDR', b'00', 4096, None),
        ):
            with self.subTest(prefix=prefix, ext=ext, size=size):
                raw[:4], raw[1080:1082] = prefix, ext
                self.system.write_bytes(raw[:size])
                result = self.run_shell('oneos_image_fs "$STAGE/system.raw"')
                if expected:
                    self.assertEqual(bundle.filesystem(self.system), expected)
                    self.assertEqual(result.stdout.strip(), expected)
                    self.assertEqual(result.returncode, 0)
                else:
                    with self.assertRaises(ValueError):
                        bundle.filesystem(self.system)
                    self.assertNotEqual(result.returncode, 0)

    def test_absolute_target_escape_refused_before_attach(self):
        self.make(active=('product',))
        self.target('escape')
        run = self.run_shell('oneos_parts_mount')
        self.assertNotEqual(run.returncode, 0, run.stdout)
        self.assertNotIn('losetup', self.log.read_text())

    def test_failed_mount_detaches_readonly_loop(self):
        self.make(active=('product',))
        self.target('merged')
        run = self.run_shell('oneos_parts_mount', 'export FAIL_MOUNT=1')
        self.assertNotEqual(run.returncode, 0, run.stdout)
        self.assertIn('losetup -d /dev/null', self.log.read_text())

    def test_writable_loop_refused_before_mount(self):
        self.make(active=('product',))
        self.target('merged')
        run = self.run_shell('oneos_parts_mount',
                             'cat() { case "$1" in /sys/dev/block/*/ro) echo 0 ;; *) command cat "$@" ;; esac; }')
        self.assertNotEqual(run.returncode, 0, run.stdout)
        calls = self.log.read_text()
        self.assertNotIn('mount -t', calls)
        self.assertIn('losetup -d /dev/null', calls)

    def test_mount_propagation_failure_refused_before_attach(self):
        self.make(active=('product',))
        self.target('merged')
        self.bb.write_text(self.bb.read_text().replace('case "$2" in\n   -t)',
                           'case "$2" in\n   --make-rprivate) exit 1 ;;\n   -t)'))
        run = self.run_shell('oneos_parts_mount')
        self.assertNotEqual(run.returncode, 0, run.stdout)
        self.assertNotIn('losetup', self.log.read_text())

    def test_failure_of_second_partition_unmounts_first(self):
        self.make(('product', 'system_ext'), ('product', 'system_ext'))
        root = self.target('merged')
        # system_ext has an unsafe link, after product was successfully mounted.
        (root / 'system_ext').symlink_to('/vendor')
        run = self.run_shell('oneos_parts_mount')
        self.assertNotEqual(run.returncode, 0, run.stdout)
        self.assertIn('umount ' + str(root / 'system/product'), self.log.read_text())
        self.assertIn('losetup -d /dev/null', self.log.read_text())


class Packaging(unittest.TestCase):
    def test_hardware_activation_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = image(Path(d) / 'raw')
            with self.assertRaises(ValueError):
                bundle.build(p, {'vendor': p}, {'vendor'}, Path(d) / 'bundle')

    def test_sparse_and_nonfilesystem_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'raw'
            for content in (b'\x3a\xff\x26\xed', b'ANDROID!', b'garbage'):
                p.write_bytes(content)
                with self.assertRaises(ValueError):
                    bundle.filesystem(p)

    def test_existing_output_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            p = image(Path(d) / 'raw')
            out = Path(d) / 'bundle'
            out.mkdir()
            with self.assertRaises(ValueError):
                bundle.build(p, {'product': p}, set(), out)
            self.assertEqual(list(out.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
