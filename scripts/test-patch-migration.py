#!/usr/bin/env python3
"""Check migration completeness and reject corrupt payloads without network access."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('migration', Path(__file__).with_name('migrate-patch-payloads.py'))
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    def test_every_shipped_remote_pin_is_in_the_migration(self):
        items = migration.inventory()
        packages = migration.ROOT / 'app-android/packages'
        sizes = dict(line.split() for line in (packages / 'sizes.txt').read_text().splitlines()
                     if line and not line.startswith('#'))
        remote = dict(line.split(None, 1) for line in (packages / 'remote.txt').read_text().splitlines()
                      if line and not line.startswith('#'))
        for sha, source in remote.items():
            if sha not in items:
                # A pin added after fork-payloads-v1 may still come from its original GitHub release.
                self.assertTrue(source.startswith('gh:'), sha)
                continue
            self.assertEqual(source, f'gh:{migration.REPO}:{migration.TAG}')
            self.assertEqual(int(sizes[sha]), items[sha]['bytes'])
        for manifest in (migration.ROOT / 'scripts/patch-migration').glob('*-*.json'):
            import json
            for payload in json.loads(manifest.read_text())['files']:
                self.assertEqual(f'gh:{migration.REPO}:{migration.TAG}', payload['source'])
                self.assertEqual(payload['bytes'], items[payload['sha256']]['bytes'])

    def test_channel_snapshot_preserves_every_guarded_app_pin(self):
        import json
        packages = migration.ROOT / 'app-android/packages'
        base = {line.split()[1]: line.split()[0] for line in (migration.ROOT / 'scripts/patch-migration/n6-global-f8-baseline.sha256').read_text().splitlines()
                if line and not line.startswith('#')}
        snapshot = json.loads((migration.ROOT / 'scripts/patch-migration/f8-n6-global-f8.json').read_text())
        self.assertEqual(set(base), {file['name'] for file in snapshot['files']})
        for file in snapshot['files']:
            if file['name'] not in ('Fold8SystemUI.apk', 'GlobalVerifiedAddons.tar'):
                self.assertEqual(base[file['name']], file['sha256'])

    def test_corrupt_same_size_and_truncated_downloads_are_discarded(self):
        for data in (b'evil', b'go'):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp:
                sha = hashlib.sha256(b'good').hexdigest()
                directory = Path(tmp)
                def fake_download(*args, **kwargs):
                    (directory / sha).write_bytes(data)
                    return type('Result', (), {'returncode': 0})()
                with patch.object(migration.subprocess, 'run', side_effect=fake_download):
                    with self.assertRaises(RuntimeError):
                        migration.fetch(sha, dict(bytes=4, sources=['gh:origin/repo:tag']), directory)
                self.assertFalse((directory / sha).exists())

    def test_valid_download_is_retained_and_existing_release_must_match_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            sha = hashlib.sha256(b'good').hexdigest()
            directory = Path(tmp)
            def fake_download(*args, **kwargs):
                (directory / sha).write_bytes(b'good')
                return type('Result', (), {'returncode': 0})()
            with patch.object(migration.subprocess, 'run', side_effect=fake_download):
                self.assertEqual(directory / sha, migration.fetch(sha, dict(bytes=4, sources=['gh:origin/repo:tag']), directory))
            self.assertFalse(migration.matches(dict(state='uploaded', size=4, digest='sha256:bad'), sha, 4))
            self.assertTrue(migration.matches(dict(state='uploaded', size=4, digest=f'sha256:{sha}'), sha, 4))


if __name__ == '__main__':
    unittest.main()
