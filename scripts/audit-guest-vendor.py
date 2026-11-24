#!/usr/bin/env python3
"""Read vendor configs offline and compare HAL/service contracts. Never activate a vendor."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import tarfile
import xml.etree.ElementTree as ET

MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILES = 10000
SELECTED_PROPS = {
    'ro.vendor.build.fingerprint', 'ro.vendor.build.version.release',
    'ro.vendor.build.version.sdk', 'ro.vendor.build.security_patch',
    'ro.vndk.version', 'ro.board.platform', 'ro.product.vendor.device',
    'ro.product.vendor.model', 'ro.product.vendor.manufacturer',
}


def config_name(name):
    if name.startswith('./'):
        name = name[2:]
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or any(c.isspace() for c in name):
        raise ValueError(f'unsafe config path: {name!r}')
    if str(p) != name:
        raise ValueError(f'noncanonical config path: {name!r}')
    return name


def wanted(name):
    return (name in ('build.prop', 'etc/build.prop') or
            name.startswith('etc/vintf/') and name.endswith('.xml') or
            name.startswith('etc/init/') and name.endswith('.rc') or
            name.startswith('etc/ld.config') and name.endswith('.txt'))


def read_configs(source):
    configs = {}
    total = 0
    def add(name, data):
        nonlocal total
        if name in configs:
            raise ValueError(f'duplicate archive path: {name}')
        total += len(data)
        if len(data) > MAX_FILE or total > MAX_TOTAL or len(configs) >= MAX_FILES:
            raise ValueError('vendor config input exceeds limits')
        configs[name] = data
    if source.is_dir():
        root = source.resolve(strict=True)
        for p in sorted(root.rglob('*')):
            name = config_name(p.relative_to(root).as_posix())
            if not wanted(name):
                continue
            # Do not follow any file or parent link, including a link into host /vendor.
            if p.is_symlink() or p.resolve() != p or not p.is_file():
                raise ValueError(f'linked or nonregular config: {name}')
            if p.stat().st_size > MAX_FILE:
                raise ValueError(f'config too large: {name}')
            add(name, p.read_bytes())
    else:
        # No extractall: archive paths and symlinks never reach the filesystem.
        with tarfile.open(source, 'r:*') as archive:
            members = 0
            archive_bytes = 0
            for entry in archive:
                archive_bytes += entry.size
                if archive_bytes > MAX_TOTAL:
                    raise ValueError('archive expanded size exceeds limits')
                members += 1
                if members > MAX_FILES:
                    raise ValueError('too many archive entries')
                name = config_name(entry.name)
                if not entry.isfile():
                    raise ValueError(f'nonregular archive entry: {name}')
                if entry.size > MAX_FILE:
                    raise ValueError(f'config too large: {name}')
                if wanted(name):
                    stream = archive.extractfile(entry)
                    add(name, stream.read(MAX_FILE + 1))
    if not configs:
        raise ValueError('no vendor properties, VINTF or init configs found')
    return configs


def parse_hals(text, name):
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError(f'DTD/entities are not accepted: {name}')
    root = ET.fromstring(text)
    # Compatibility matrices express requirements, not provided services.
    if root.tag != 'manifest':
        return []
    hals = []
    for hal in root.findall('hal'):
        package = hal.findtext('name', '').strip()
        interfaces = []
        for iface in hal.findall('interface'):
            for instance in iface.findall('instance'):
                interfaces.append(iface.findtext('name', '').strip() + '/' + (instance.text or '').strip())
        hals.append(dict(name=package, format=hal.get('format', ''),
                         versions=[(v.text or '').strip() for v in hal.findall('version')],
                         transport=hal.findtext('transport', '').strip(),
                         interfaces=sorted(interfaces),
                         fqnames=sorted((v.text or '').strip() for v in hal.findall('fqname')),
                         source=name))
    return hals


def init_lines(text):
    pending = ''
    start = 0
    for number, raw in enumerate(text.splitlines(), 1):
        if not pending:
            start = number
        slashes = len(raw) - len(raw.rstrip('\\'))
        if slashes % 2:
            pending += raw[:-1] + ' '
            continue
        yield start, pending + raw
        pending = ''
    if pending:
        raise ValueError('unterminated init line continuation')


def parse_init(text, name):
    services, imports = [], []
    current = None
    for number, raw in init_lines(text):
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        try:
            tokens = shlex.split(raw, comments=True)
        except ValueError as e:
            raise ValueError(f'{name}:{number}: {e}') from e
        if not tokens:
            continue
        if not raw[0].isspace():
            current = None
            if tokens[0] == 'import':
                imports.append(' '.join(tokens[1:]))
            elif tokens[0] == 'service' and len(tokens) >= 3:
                current = dict(name=tokens[1], executable=tokens[2], args=tokens[3:],
                               source=name, line=number, options={})
                services.append(current)
        elif current is not None:
            current['options'].setdefault(tokens[0], []).append(tokens[1:])
    return services, imports


def inventory(source):
    files = read_configs(source)
    props, hals, services, imports, errors = {}, [], [], [], []
    for name, data in sorted(files.items()):
        text = data.decode('utf-8', errors='replace')
        if name.endswith('build.prop'):
            for line in text.splitlines():
                key, sep, value = line.partition('=')
                if sep and key.strip() in SELECTED_PROPS:
                    props[key.strip()] = value.strip()
        elif name.startswith('etc/vintf/'):
            try:
                hals.extend(parse_hals(text, name))
            except (ET.ParseError, ValueError) as e:
                errors.append(f'{name}: {e}')
        elif name.startswith('etc/init/'):
            try:
                found, imported = parse_init(text, name)
                services.extend(found)
                imports.extend(dict(source=name, path=p) for p in imported)
            except ValueError as e:
                errors.append(str(e))
    return dict(properties=props, hals=hals, services=services, unresolved_imports=imports,
                parse_errors=errors,
                files={name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())})


def contracts(report):
    # Keep versions and interface instances in the key: a package-name match
    # alone does not imply Binder ABI compatibility.
    return {json.dumps({k: h[k] for k in ('name', 'format', 'versions', 'interfaces', 'fqnames')},
                       sort_keys=True) for h in report['hals']}


def audit(stock, donor=None):
    out = dict(schema=1, activation_allowed=False, stock=inventory(stock),
               limits=['Config inventory only; libraries, symbols and kernel ABI have not been verified.',
                       'Init imports are recorded, not evaluated; property-conditioned imports may be missing.',
                       'Matching contracts do not certify compatible behavior, SELinux or hardware.',
                       'No service start, flash, property change or driver load was performed.'])
    if donor is not None:
        other = inventory(donor)
        provided, requested = contracts(out['stock']), contracts(other)
        stock_services = {s['name']: s for s in out['stock']['services']}
        out.update(candidate=other, comparison=dict(
            candidate_contracts_absent_from_stock=[json.loads(k) for k in sorted(requested - provided)],
            stock_contracts_absent_from_candidate=[json.loads(k) for k in sorted(provided - requested)],
            service_name_collisions=[dict(name=s['name'], stock=stock_services[s['name']]['executable'],
                                          candidate=s['executable']) for s in other['services']
                                     if s['name'] in stock_services],
            changed_configs=sorted(n for n in out['stock']['files'].keys() & other['files'].keys()
                                   if out['stock']['files'][n] != other['files'][n])))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock', type=Path, required=True, help='extracted vendor directory or config tar')
    p.add_argument('--candidate', type=Path, help='donor vendor directory or config tar')
    a = p.parse_args()
    try:
        print(json.dumps(audit(a.stock, a.candidate), indent=2, sort_keys=True))
    except (ValueError, OSError, tarfile.TarError) as e:
        p.error(str(e))


if __name__ == '__main__':
    main()
