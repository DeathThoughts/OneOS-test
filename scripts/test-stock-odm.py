#!/usr/bin/env python3
"""Test strict stock-odm selection, live-device proof and guest-only mounts."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('part_tests', ROOT / 'scripts/test-guest-partitions.py')
parts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parts)


class StockOdm(unittest.TestCase):
    setUp = parts.Partitions.setUp
    make = parts.Partitions.make
    run_shell = parts.Partitions.run_shell

    def stock(self):
        self.make(('odm',))
        p = self.parts / 'manifest'
        p.write_text(p.read_text().replace('inspect', 'stock'))
        (self.stage / 'rootfs/odm').mkdir(parents=True)
        self.bb.write_text(self.bb.read_text().replace('case "$1" in', '''case "$1" in
 test) exit 0 ;;
 blockdev) [ "$WRONG_SIZE" != 1 ] && echo 4096 || echo 123 ;;
''', 1))

    def environment(self):
        # The fixture uses a small image and a simulated device. Production's
        # exact profile pin is tested independently and never bypassed in code.
        return '''oneos_parts_odm_profile() { return 0; }
getprop() {
 case "$1" in
  ro.odm.build.fingerprint) echo "${WRONG_FP:-samsung/dm1qxxx/dm1q:13/TP1A.220624.014/S911BXXUAGZIF:user/release-keys}" ;;
  ro.product.odm.model) echo SM-S911B ;;
  ro.board.platform) echo kalama ;;
 esac
}
stat() {
 if [ "$1" = -c ] && [ "$2" = %u ]; then echo 0
 elif [ "$1" = -L ] && [ "$4" = /dev/block/dm-42 ]; then echo 'fd 2a'
 else command stat "$@"; fi
}
sha256sum() {
 if [ "$1" = /dev/block/dm-42 ]; then
  if [ "$WRONG_BYTES" = 1 ]; then echo 'wrong  /dev/block/dm-42'
  else command sha256sum "$STAGE/partition_images/odm.img"; fi
 else command sha256sum "$@"; fi
}
awk() {
 case "$*" in
  *'dev=$(i+2)'*) [ "$BAD_LIVE_MOUNT" != 1 ] && echo '253:42 /dev/block/dm-42' ;;
  *'odm\\/'*) printf '%s' "$CHILDREN" ;;
  */proc/self/mountinfo*)
    case "$*" in *'index($5,p'*) return 1 ;; *) return 0 ;; esac ;;
  *) command awk "$@" ;;
 esac
}
'''

    def test_profile_accepts_only_audited_raw_image(self):
        run = self.run_shell('''oneos_parts_odm_profile erofs 1c1e9e3f95fa3e635dcfbb63e37a76fc40f60014b6a6bc8c8ed7e1457d29f4de 778240''')
        self.assertEqual(run.returncode, 0)
        for values in ('ext4 hash 4096', 'erofs other 778240',
                       'erofs 1c1e9e3f95fa3e635dcfbb63e37a76fc40f60014b6a6bc8c8ed7e1457d29f4de 4096'):
            self.assertNotEqual(self.run_shell('oneos_parts_odm_profile ' + values).returncode, 0)

    def test_unknown_image_rejected_even_with_stock_manifest_mode(self):
        self.stock()
        run = self.run_shell()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('unknown stock odm profile', run.stdout)
        self.assertNotIn('losetup', self.log.read_text())

    def test_wrong_host_fingerprint_refused(self):
        self.stock()
        run = self.run_shell(prefix=self.environment() + '\nWRONG_FP=other')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('device/build mismatch', run.stdout)
        self.assertNotIn('losetup', self.log.read_text())

    def test_wrong_live_partition_size_or_bytes_refused(self):
        self.stock()
        for variable in ('WRONG_SIZE', 'WRONG_BYTES', 'BAD_LIVE_MOUNT'):
            with self.subTest(variable=variable):
                run = self.run_shell(prefix=self.environment() + f'\nexport {variable}=1')
                self.assertNotEqual(run.returncode, 0, run.stdout)
                self.assertNotIn('losetup', self.log.read_text())

    def test_verified_snapshot_is_mounted_only_under_guest(self):
        self.stock()
        run = self.run_shell('oneos_parts_mount; result=$?; [ "$result" = 0 ] && [ "$OP_ODM_ACTIVE" = 1 ]',
                             self.environment())
        self.assertEqual(run.returncode, 0, run.stdout)
        calls = self.log.read_text()
        self.assertIn('losetup -r /dev/null ', calls)
        self.assertIn('mount -t ext4 -o ro,nodev,nosuid,noload /dev/null ' + str(self.stage / 'rootfs/odm'), calls)
        self.assertNotIn(' /odm\n', calls)

    def test_odm_target_uses_root_odm_when_two_real_directories_exist(self):
        self.stock()
        (self.stage / 'rootfs/system/odm').mkdir(parents=True)
        run = self.run_shell('oneos_parts_mount', self.environment())
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertNotIn(str(self.stage / 'rootfs/system/odm'), self.log.read_text())

    def test_unsafe_submount_path_refused(self):
        self.stock()
        run = self.run_shell(prefix=self.environment() + '\nCHILDREN="/odm/../system"')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('unsafe live odm mount path', run.stdout)
        self.assertNotIn('losetup', self.log.read_text())

    def test_guest_submount_symlink_refused_and_loop_cleaned(self):
        self.stock()
        (self.stage / 'rootfs/odm/firmware_mnt').symlink_to('/odm/firmware_mnt')
        run = self.run_shell('oneos_parts_mount', self.environment() + '\nCHILDREN=/odm/firmware_mnt')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('no safe guest target', run.stdout)
        self.assertIn('losetup -d /dev/null', self.log.read_text())

    def test_live_mountinfo_source_is_checked_with_real_awk(self):
        self.stock()
        table = self.stage / 'mountinfo'
        prefix = self.environment() + '\n' + r'''awk() {
 case "$*" in
  */proc/self/mountinfo*) command awk "$1" "$STAGE/mountinfo" ;;
  *) command awk "$@" ;;
 esac
}
'''
        good = '101 1 253:42 / /odm ro,relatime shared:4 - erofs /dev/block/dm-42 ro\n'
        table.write_text(good)
        run = self.run_shell(prefix=prefix)
        self.assertEqual(run.returncode, 0, run.stdout)
        for content in (good + good, good.replace('erofs', 'overlay'),
                        good.replace(' / /odm ', ' /other /odm '), ''):
            table.write_text(content)
            run = self.run_shell(prefix=prefix)
            self.assertNotEqual(run.returncode, 0, content)

    def test_submount_is_private_before_cleanup_can_unmount(self):
        # Invoke the actual child bind path with source type probes simulated;
        # no directory on the real host /odm is created or mounted.
        root = self.stage / 'rootfs'
        target = root / 'odm/firmware_mnt'
        target.mkdir(parents=True)
        prefix = self.environment() + r'''
OP_ODM_CHILDREN=/odm/firmware_mnt
OP_ODM_BOUND=
OP_MOUNTED=
'''
        run = self.run_shell('oneos_parts_odm_children && oneos_parts_rollback', prefix)
        self.assertEqual(run.returncode, 0, run.stdout)
        calls = self.log.read_text()
        self.assertIn('mount --rbind /odm/firmware_mnt ' + str(target), calls)
        self.assertLess(calls.index('mount --make-rslave ' + str(target)),
                        calls.index('umount ' + str(target)))
        self.assertLess(calls.index('mount --make-rprivate ' + str(target)),
                        calls.index('umount ' + str(target)))

    def test_failed_isolation_never_unmounts_a_possibly_shared_target(self):
        root = self.stage / 'rootfs/odm'
        root.mkdir(parents=True)
        self.bb.write_text(self.bb.read_text().replace('case "$1" in', '''case "$1" in
 mount) [ "$2" != --make-rprivate ] || exit 1 ;;
''', 1))
        run = self.run_shell('oneos_parts_rollback',
                             'OP_MOUNTED="$ROOTFS/odm"; OP_LOOPS=/dev/null')
        self.assertNotEqual(run.returncode, 0)
        calls = self.log.read_text()
        self.assertNotIn('umount', calls)
        self.assertNotIn('losetup -d', calls)

    def test_packager_refuses_unrecognized_stock_odm(self):
        with self.assertRaises(ValueError):
            parts.bundle.build(self.system, {'odm': self.ext}, set(), self.parts, stock_odm=True)
        self.assertFalse(self.parts.exists())

    def test_packager_requires_odm_for_stock_mode(self):
        with self.assertRaises(ValueError):
            parts.bundle.build(self.system, {'product': self.product}, set(), self.parts, stock_odm=True)


if __name__ == '__main__':
    unittest.main()
