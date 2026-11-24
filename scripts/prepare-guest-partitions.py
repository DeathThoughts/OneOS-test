#!/usr/bin/env python3
"""Build a raw, hashed guest partition bundle; never connects to a phone or flashes."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

NAMES = {'product', 'system_ext', 'vendor', 'odm', 'vendor_dlkm', 'odm_dlkm', 'system_dlkm'}
ACTIVE = {'product', 'system_ext'}
STOCK_VENDOR_SHA = '3ace2530806e1eeba86007eab46a4fbebe607caf04ea73ce6cbb2ec6a17c63e7'
STOCK_VENDOR_SIZE = 1907937280


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def filesystem(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'{path}: expected a regular raw image, not a symlink')
    with path.open('rb') as f:
        if f.read(4) == b'\x3a\xff\x26\xed':
            raise ValueError(f'{path}: Android sparse image; convert with simg2img first')
        f.seek(1024)
        if f.read(4) == b'\xe2\xe1\xf5\xe0':
            return 'erofs'
        f.seek(1080)
        if f.read(2) == b'\x53\xef':
            return 'ext4'
    raise ValueError(f'{path}: expected raw ext4/EROFS, not super, boot or firmware')


def build(system, images, activate, output, stock_vendor=False, stock_odm=False):
    if not images or not set(images) <= NAMES:
        raise ValueError('specify at least one supported partition')
    if not activate <= ACTIVE or not activate <= set(images):
        raise ValueError('only supplied product/system_ext images can be activated; hardware partitions are inspection-only')
    if stock_vendor and 'vendor' not in images:
        raise ValueError('stock vendor activation requires a vendor image')
    if stock_odm and 'odm' not in images:
        raise ValueError('stock ODM activation requires an ODM image')
    filesystem(system)
    if output.exists() or output.is_symlink():
        raise ValueError(f'{output}: output must not exist')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Copy into a new directory; publish the manifest last and rename atomically.
    temp = Path(tempfile.mkdtemp(prefix='.oneos-parts-', dir=output.parent))
    try:
        lines = [f'oneos-partitions-v1 {digest(system)}']
        for name, source in sorted(images.items()):
            fs = filesystem(source)
            target = temp / f'{name}.img'
            shutil.copyfile(source, target)
            target.chmod(0o600)
            sha = digest(target)
            size = target.stat().st_size
            mode = 'activate' if name in activate else 'inspect'
            if stock_vendor and name == 'vendor':
                if (fs, sha, size) != ('erofs', STOCK_VENDOR_SHA, STOCK_VENDOR_SIZE):
                    raise ValueError('stock vendor must be the exact audited SM-S911B GZIF image')
                mode = 'stock'
            if stock_odm and name == 'odm':
                if (fs, sha, size) != ('erofs', '1c1e9e3f95fa3e635dcfbb63e37a76fc40f60014b6a6bc8c8ed7e1457d29f4de', 778240):
                    raise ValueError('stock ODM must be the exact audited SM-S911B GZIF image')
                mode = 'stock'
            lines.append(f'{name} {fs} {sha} {size} {mode}')
        (temp / 'manifest').write_text('\n'.join(lines) + '\n')
        (temp / 'manifest').chmod(0o600)
        os.rename(temp, output)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return lines


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--system', type=Path, required=True, help='exact already-patched system.raw used by OneOS')
    p.add_argument('--image', action='append', required=True, metavar='NAME=RAW_IMG')
    p.add_argument('--activate', action='append', default=[], choices=sorted(ACTIVE),
                   help='experimental guest mount; otherwise image is only verified, never mounted')
    p.add_argument('--stock-vendor', action='store_true',
                   help='experimental S23 stock vendor mount; requires exact audited image and matching live partition')
    p.add_argument('--stock-odm', action='store_true',
                   help='experimental S23 stock ODM mount; requires exact audited image and matching live partition')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    try:
        images = {}
        for entry in a.image:
            name, sep, filename = entry.partition('=')
            if not sep or name not in NAMES or name in images:
                raise ValueError(f'invalid or duplicate partition: {entry}')
            images[name] = Path(filename)
        for line in build(a.system, images, set(a.activate), a.output, a.stock_vendor, a.stock_odm):
            print(line)
        print('Bundle created. No device was modified. Hashes identify files; they do not prove ROM compatibility.')
    except (ValueError, OSError) as e:
        p.error(str(e))


if __name__ == '__main__':
    main()
