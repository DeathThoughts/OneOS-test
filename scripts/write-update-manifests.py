#!/usr/bin/env python3
"""Publish version and hash metadata for the APKs built by this workflow run."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', choices=('debug', 'release'), required=True)
    parser.add_argument('--directory', type=Path, default=Path('dist'))
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    if not args.run_id.isdecimal():
        parser.error('run-id must be decimal')
    aapt = Path(os.environ['ANDROID_HOME']) / 'build-tools/36.0.0/aapt2'
    for prefix, manifest in (('OneOS', 'update'), ('OneOSGuest', 'guest-update')):
        apk = args.directory / f'{prefix}-{args.variant}.apk'
        result = subprocess.check_output([str(aapt), 'dump', 'badging', str(apk)], text=True)
        package = result.splitlines()[0]
        code = re.search(r" versionCode='([0-9]+)'", package)
        name = re.search(r" versionName='([^']+)'", package)
        if not code or not name:
            raise ValueError(f'Could not read {apk.name} version')
        digest = hashlib.sha256(apk.read_bytes()).hexdigest()
        data = {
            'versionCode': int(code[1]), 'versionName': name[1],
            'apk': f'https://github.com/DeathThoughts/OneOS-test/releases/download/oneos-build-{args.run_id}/{apk.name}',
            'bytes': apk.stat().st_size, 'sha256': digest,
        }
        (args.directory / f'{manifest}-{args.variant}.json').write_text(json.dumps(data, indent=2) + '\n')
        (args.directory / f'{apk.name}.sha256').write_text(f'{digest}  {apk.name}\n')


if __name__ == '__main__':
    main()
