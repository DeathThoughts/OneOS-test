#!/usr/bin/env python3
"""Copy the pinned patch payloads once. Never rebuild, re-sign, or download a GSI.

Run with GH_TOKEN and --publish to publish immutable hash-named assets in this
fork. Normal builds only need --verify-remote after migration. Origin locations
are an audit record for this migration, never shipped to the app.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REPO = 'DeathThoughts/OneOS-test'
TAG = 'fork-payloads-v1'
CHANNEL_TAG = 'fork-fold8-experimental-v1'
PIN = re.compile(r'[0-9a-f]{64}')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory():
    data = json.loads((ROOT / 'scripts/patch-migration/sources.json').read_text())
    assert data['schema'] == 1
    items = data['payloads']
    for sha, item in items.items():
        assert PIN.fullmatch(sha) and 0 < item['bytes'] <= 268435456
        assert item['sources']
    packages = ROOT / 'app-android/packages'
    remote = dict(line.split(None, 1) for line in (packages / 'remote.txt').read_text().splitlines()
                  if line and not line.startswith('#'))
    assert all(sha in items and src == f'gh:{REPO}:{TAG}' for sha, src in remote.items())
    return items


def source_url(source, sha):
    if source == 'sf':
        return f'https://downloads.sourceforge.net/project/oneos-mirror/files/{sha}'
    if source.startswith('gh:'):
        parts = source[3:].split(':')
        repo, tag = parts if len(parts) == 2 else ('fahadalijaved/OneOS-files', parts[0])
        assert re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repo)
        assert re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,39}', tag)
        return f'https://github.com/{repo}/releases/download/{tag}/{sha}'
    assert re.fullmatch(r'[A-Za-z0-9_-]{20,80}', source)
    return f'https://drive.usercontent.google.com/download?id={source}&export=download&confirm=t'


def fetch(sha, item, directory):
    path = directory / sha
    for source in item['sources']:
        result = subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
            '--connect-timeout', '15', '--max-time', '180', '--retry', '2', '--retry-max-time', '360',
            '--max-filesize', str(item['bytes']), source_url(source, sha), '-o', str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode == 0 and path.stat().st_size == item['bytes'] and digest(path) == sha:
            print(f'Verified {sha} ({item["bytes"]} bytes)', flush=True)
            return path
        path.unlink(missing_ok=True)
        # Do not print curl errors or redirect URLs. Hash identifies the failed payload.
        print(f'Source failed for {sha}', flush=True)
    raise RuntimeError(f'No verified source for {sha}')


def gh(*args):
    return subprocess.check_output(['gh', *args, '--repo', REPO], text=True, timeout=300)


def assets(tag):
    result = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPO, '--json', 'assets'],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        if 'release not found' in result.stderr.lower():
            return None
        raise RuntimeError(f'Could not inspect fork release {tag}')
    # gh release's assets omit SHA-256 on some CLI versions. Use REST for the digest.
    release = json.loads(subprocess.check_output(
        ['gh', 'api', f'repos/{REPO}/releases/tags/{tag}'], text=True, timeout=60))
    all_assets = json.loads(subprocess.check_output(
        ['gh', 'api', '--paginate', '--slurp', f'repos/{REPO}/releases/{release["id"]}/assets?per_page=100'],
        text=True, timeout=60))
    return {a['name']: a for page in all_assets for a in page}


def ensure_release(tag, target):
    if assets(tag) is None:
        gh('release', 'create', tag, '--target', target, '--prerelease', '--latest=false',
           '--title', f'Fork patch payloads · {tag}', '--notes',
           'Hash-verified patch payloads for this independent OneOS fork. '
           'Original OneOS by Fahad Ali Javed; Fold8 contributions by Josh Skinner. '
           'Guest GSI images are separate. This is a payload store, not a stable app release.')


def matches(asset, sha, size):
    return asset is not None and asset.get('state') == 'uploaded' and asset.get('size') == size and asset.get('digest') == f'sha256:{sha}'


def verify(items):
    current = assets(TAG) or {}
    for sha, item in items.items():
        if not matches(current.get(sha), sha, item['bytes']):
            raise RuntimeError(f'Fork release lacks verified payload {sha}')
    channel = ROOT / 'scripts/patch-migration/f8-n6-global-f8.json'
    # The mutable contributor channel may later have a new revision. Verify its current
    # snapshot during migration; future publisher runs retain all the app's guard checks.
    current = assets(CHANNEL_TAG) or {}
    if not matches(current.get(channel.name), digest(channel), channel.stat().st_size):
        raise RuntimeError('Fork contributor manifest differs from the reviewed snapshot')
    print(f'Fork release verified: {len(items)} payloads and the contributor manifest', flush=True)


def publish(items, target):
    existing = assets(TAG) or {}
    missing = {}
    for sha, item in items.items():
        asset = existing.get(sha)
        if asset and not matches(asset, sha, item['bytes']):
            raise RuntimeError(f'Existing hash-named asset differs: {sha}. Refusing to overwrite it.')
        if not asset:
            missing[sha] = item
    with tempfile.TemporaryDirectory(prefix='oneos-patch-migration-') as tmp:
        directory = Path(tmp)
        # Nothing publishes until every missing file passes the pinned size and hash.
        with ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(lambda pair: fetch(*pair, directory), missing.items()))
        ensure_release(TAG, target)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda path: gh('release', 'upload', TAG, str(path)), paths))
        channel = ROOT / 'scripts/patch-migration/f8-n6-global-f8.json'
        ensure_release(CHANNEL_TAG, target)
        old = (assets(CHANNEL_TAG) or {}).get(channel.name)
        if old and not matches(old, digest(channel), channel.stat().st_size):
            raise RuntimeError('Existing fork channel differs. Publish a new reviewed revision explicitly.')
        if not old:
            gh('release', 'upload', CHANNEL_TAG, str(channel))
    verify(items)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--publish', action='store_true')
    mode.add_argument('--verify-remote', action='store_true')
    mode.add_argument('--check-inventory', action='store_true')
    parser.add_argument('--target')
    args = parser.parse_args()
    items = inventory()
    if args.publish:
        assert args.target and re.fullmatch(r'[0-9a-f]{40}', args.target)
        publish(items, args.target)
    elif args.verify_remote:
        verify(items)
    else:
        print(f'Inventory valid: {len(items)} pinned payloads')


if __name__ == '__main__':
    main()
