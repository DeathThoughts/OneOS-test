#!/usr/bin/env python3
"""Vendor audit input safety and isolated mount lifecycle, without real mounts."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


audit = module('audit_vendor', ROOT / 'scripts/audit-guest-vendor.py')
parts = module('parts_tests', ROOT / 'scripts/test-guest-partitions.py')


class OfflineAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def vendor(self, name, version='1', executable='/vendor/bin/hw/power'):
        root = self.base / name
        (root / 'etc/vintf').mkdir(parents=True)
        (root / 'etc/init').mkdir()
        (root / 'build.prop').write_text('ro.vendor.build.fingerprint=samsung/test\n'
                                       'ro.serialno=DO-NOT-INCLUDE\n')
        (root / 'etc/vintf/manifest.xml').write_text(
            '<manifest version="1.0" type="device"><hal format="aidl">'
            '<name>android.hardware.power</name><version>' + version + '</version>'
            '<interface><name>IPower</name><instance>default</instance></interface>'
            '</hal></manifest>')
        (root / 'etc/init/power.rc').write_text(
            'import /vendor/etc/init/${ro.hardware}.rc\n'
            'service vendor.power ' + executable + ' --argument\n'
            '    class hal\n    user system\n    interface aidl android.hardware.power.IPower/default\n'
            'on boot\n    write /sys/test 1\n')
        return root

    def test_inventory_does_not_execute_rc_or_include_serial(self):
        root = self.vendor('stock', executable='/bin/false')
        report = audit.audit(root)
        self.assertFalse(report['activation_allowed'])
        self.assertEqual(report['stock']['properties'], {'ro.vendor.build.fingerprint': 'samsung/test'})
        self.assertEqual(report['stock']['services'][0]['executable'], '/bin/false')
        self.assertEqual(report['stock']['services'][0]['options']['user'], [['system']])
        self.assertNotIn('on', report['stock']['services'][0]['options'])
        self.assertIn('${ro.hardware}', report['stock']['unresolved_imports'][0]['path'])

    def test_version_and_service_collisions_remain_visible(self):
        report = audit.audit(self.vendor('stock'), self.vendor('donor', '2', '/vendor/bin/hw/oplus-power'))
        comparison = report['comparison']
        self.assertEqual(comparison['candidate_contracts_absent_from_stock'][0]['versions'], ['2'])
        self.assertEqual(comparison['service_name_collisions'][0]['stock'], '/vendor/bin/hw/power')
        self.assertIn('etc/vintf/manifest.xml', comparison['changed_configs'])
        self.assertFalse(report['activation_allowed'])

    def test_matrix_is_not_a_provided_hal(self):
        root = self.vendor('stock')
        (root / 'etc/vintf/matrix.xml').write_text(
            '<compatibility-matrix><hal><name>not.a.provided.hal</name></hal></compatibility-matrix>')
        names = [h['name'] for h in audit.inventory(root)['hals']]
        self.assertNotIn('not.a.provided.hal', names)

    def test_fqname_and_interface_instances_affect_contract_comparison(self):
        root = self.vendor('stock')
        other = self.vendor('donor')
        p = other / 'etc/vintf/manifest.xml'
        p.write_text(p.read_text().replace('default', 'secondary'))
        self.assertTrue(audit.audit(root, other)['comparison']['candidate_contracts_absent_from_stock'])

    def test_invalid_xml_and_entity_declarations_are_reported(self):
        root = self.vendor('stock')
        for text in ('<broken>', '<!DOCTYPE manifest [<!ENTITY x "data">]><manifest/>'):
            (root / 'etc/vintf/manifest.xml').write_text(text)
            report = audit.audit(root)
            self.assertTrue(report['stock']['parse_errors'])
            self.assertFalse(report['activation_allowed'])

    def tar(self, name, entries):
        p = self.base / name
        with tarfile.open(p, 'w') as t:
            for filename, kind, data in entries:
                entry = tarfile.TarInfo(filename)
                entry.type = kind
                entry.linkname = '/etc/passwd' if kind == tarfile.SYMTYPE else ''
                entry.size = len(data)
                t.addfile(entry, io.BytesIO(data))
        return p

    def test_archive_inventory_has_no_extraction(self):
        p = self.tar('input.tar', [('./build.prop', tarfile.REGTYPE,
                                  b'ro.vendor.build.version.sdk=37\n')])
        self.assertEqual(audit.inventory(p)['properties']['ro.vendor.build.version.sdk'], '37')
        self.assertFalse((self.base / 'build.prop').exists())

    def test_archive_traversal_links_and_duplicate_paths_are_refused(self):
        for entries in ([('../build.prop', tarfile.REGTYPE, b'x')],
                        [('/build.prop', tarfile.REGTYPE, b'x')],
                        [('build.prop', tarfile.SYMTYPE, b'')],
                        [('build.prop', tarfile.REGTYPE, b'x')] * 2):
            with self.subTest(entries=entries):
                p = self.tar('bad.tar', entries)
                with self.assertRaises(ValueError):
                    audit.inventory(p)

    def test_external_config_symlink_refused(self):
        root = self.vendor('stock')
        p = root / 'build.prop'
        p.unlink()
        p.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            audit.inventory(root)


class VendorMount(unittest.TestCase):
    setUp = parts.Partitions.setUp
    make = parts.Partitions.make
    run_shell = parts.Partitions.run_shell

    def private_namespace(self):
        return '''readlink() {
 case "$1" in
  /proc/self/ns/mnt) echo 'mnt:[22]' ;;
  /proc/1/ns/mnt) echo 'mnt:[11]' ;;
  *) command readlink "$@" ;;
 esac
}
'''

    def test_init_namespace_refused_without_mounts(self):
        run = self.run_shell('oneos_parts_inspect_vendor',
                             "readlink() { echo 'mnt:[11]'; }")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('separate mount namespace', run.stdout)
        self.assertFalse(self.log.exists())

    def test_invalid_vendor_refused_before_namespace_mount_change(self):
        self.make(('vendor',))
        (self.parts / 'vendor.img').write_bytes(b'tampered')
        run = self.run_shell('oneos_parts_inspect_vendor', self.private_namespace())
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_existing_inspection_path_is_never_removed(self):
        self.make(('vendor',))
        dest = self.stage / 'vendor_inspection'
        dest.mkdir()
        (dest / 'keep').write_text('keep')
        run = self.run_shell('oneos_parts_inspect_vendor', self.private_namespace())
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual((dest / 'keep').read_text(), 'keep')
        self.assertNotIn('losetup', self.log.read_text())

    def backend(self):
        self.bb.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CALL_LOG"
case "$1" in
 blkid) printf '%s TYPE="ext4"\\n' "$2" ;;
 losetup) [ "$2" != -f ] || echo /dev/null ;;
 mount)
  if [ "$2" = -t ] && [ "$3" = ext4 ]; then
   [ "$FAIL_MOUNT" != 1 ] || exit 1
   for last do :; done
   mkdir -p "$last/etc/init"
   printf 'service test /vendor/bin/hw/test\\n' > "$last/etc/init/test.rc"
   # An image directory link to host /etc must never be walked.
   ln -s /etc "$last/host-etc"
  fi ;;
 umount)
  case "$2" in
   */vendor_inspection/root)
    rm "$2/etc/init/test.rc" "$2/host-etc"
    rmdir "$2/etc/init" "$2/etc" ;;
   */vendor_inspection)
    rm -f "$2/files"
    rmdir "$2/root" ;;
   *) exit 1 ;;
  esac ;;
 tar) cat "$5" ;;
esac
''')

    def test_inspection_uses_noexec_and_cleans_up_successfully(self):
        self.make(('vendor',))
        self.backend()
        run = self.run_shell('oneos_parts_inspect_vendor', self.private_namespace())
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn('./etc/init/test.rc', run.stdout)
        self.assertNotIn('host-etc', run.stdout)
        calls = self.log.read_text()
        self.assertIn('mount --make-rprivate /', calls)
        self.assertIn('ro,nodev,nosuid,noexec,noload', calls)
        self.assertIn('losetup -d /dev/null', calls)
        self.assertNotIn(' /vendor\n', calls)
        self.assertFalse((self.stage / 'vendor_inspection').exists())

    def test_mount_failure_detaches_loop(self):
        self.make(('vendor',))
        self.backend()
        run = self.run_shell('oneos_parts_inspect_vendor',
                             self.private_namespace() + '\nexport FAIL_MOUNT=1')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('losetup -d /dev/null', self.log.read_text())
        self.assertFalse((self.stage / 'vendor_inspection').exists())


if __name__ == '__main__':
    unittest.main()
