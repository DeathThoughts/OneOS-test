#!/usr/bin/env python3
"""Build the OneOS ColorOS 17 ext4 guest from the pinned Twinity4c release."""
import argparse
from bisect import bisect_right
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import zipfile


ROOT = Path(__file__).resolve().parent.parent
RELEASE = "https://github.com/DeathThoughts/OneOS-test/releases/tag/images"
ROM = "OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0.zip"
NAME = "twinity4c"
MYSTIC_GSI_REV = "825b850dd8ddff11fdb8ed024034fc00e94b1451"
CHUNK = 4 * 1024 * 1024
SECTOR = 512
PARTS = (
    (2_000_000_000, "afcac9a46ade06dfe5b7b6a735df872b2b9c02e29c277b7a10cff3761da58a21"),
    (2_000_000_000, "8bf350f7e99e7483ea812f29571f53b4effccd2677886cb7949e06213aaceffb"),
    (2_000_000_000, "b305e4ad647c99eb63f66e8f3eb15d44b7d338d41d186bbe098b1fc2ff295bd1"),
    (702_545_504, "6a6325b368f640521199b9e2c6f5c2237efe1d0744cfcba2492a57f45f793559"),
)
WRAPPER_SIZE = 6_702_545_504
INNER_ENTRY = (ROM, zipfile.ZIP_STORED, 6_702_545_150, 6_702_545_150, 0x3E7D9984)
INNER_COUNT = 50
SUPER_ENTRY = (
    "images/super.img.zst", zipfile.ZIP_DEFLATED, 6_488_935_881, 6_481_053_494, 0x2AC6F7BE,
)
SUPER_SIZE = 9_126_805_504
SUPER_SHA = "4bf2205500990fb72e3f370ebfd0d78bc7e65817faa3e96f1b512f71a3505c01"
PARTITIONS = {
    # Start sector, byte length and SHA-256 from the pinned raw super image.
    "odm": (2048, 40_038_400, "a59b85b9154f4c6be25f66067cad80b77688fbe21b0584052d9cbffd88501248"),
    "product": (81920, 161_112_064, "308a1c09f4bdc447ee57638d8217c2c252c5a9e230f604a9c079e0c2ceac104d"),
    "system": (397312, 5_434_658_816, "b879dbdfc7385e031561025bf5bd8b9e39e15f0de1227183122959fac1a71b39"),
    "system_ext": (11012096, 1_143_083_008, "202eed18ca00505e44b2bb40662dc2acaec6f0b9de72145df627e0b12f1cf369"),
}
ANDROID = {"sdk": 37, "codename": "REL", "release": "17", "build_id": "CP2A.260605.016",
           "security_patch": "2026-09-01"}
LOCAL_HEADER = struct.Struct("<4s5H3I2H")


class BuildError(Exception):
    pass


class BoundedReader(io.RawIOBase):
    """Expose a window of a seekable binary stream without owning that stream."""

    def __init__(self, stream, offset, size):
        if offset < 0 or size < 0:
            raise ValueError("negative stream window")
        self.stream = stream
        self.offset = offset
        self.size = size
        self.pos = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.seek(0, os.SEEK_CUR)

    def seek(self, offset, whence=os.SEEK_SET):
        self._checkClosed()
        if whence == os.SEEK_CUR:
            offset += self.pos
        elif whence == os.SEEK_END:
            offset += self.size
        elif whence != os.SEEK_SET:
            raise ValueError("invalid seek origin")
        if offset < 0:
            raise OSError("negative seek")
        self.pos = offset
        return self.pos

    def readinto(self, target):
        self._checkClosed()
        count = min(len(target), self.size - self.pos)
        if count <= 0:
            return 0
        self.stream.seek(self.offset + self.pos)
        data = self.stream.read(count)
        if len(data) != count:
            raise BuildError("short read inside stream window")
        target[:count] = data
        self.pos += count
        return count


class SplitReader(BoundedReader):
    """Present release parts as one seekable archive."""

    def __init__(self, parts):
        self.starts = [0]
        self.parts = []
        for stream, size in parts:
            if size <= 0:
                raise ValueError("empty split part")
            self.parts.append(stream)
            self.starts.append(self.starts[-1] + size)
        super().__init__(None, 0, self.starts[-1])

    def readinto(self, target):
        self._checkClosed()
        count = min(len(target), self.size - self.pos)
        copied = 0
        while copied < count:
            index = bisect_right(self.starts, self.pos) - 1
            part = self.parts[index]
            part.seek(self.pos - self.starts[index])
            take = min(count - copied, self.starts[index + 1] - self.pos)
            data = part.read(take)
            if len(data) != take:
                raise BuildError(f"short read in split part {index + 1}")
            target[copied:copied + take] = data
            self.pos += take
            copied += take
        return copied


def verify_stream(source, size, sha256, label, destination=None):
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        data = source.read(min(CHUNK, remaining))
        if not data:
            raise BuildError(f"{label}: expected {size} bytes, got {size - remaining}")
        digest.update(data)
        if destination is not None:
            destination.write(data)
        remaining -= len(data)
    if source.read(1):
        raise BuildError(f"{label}: exceeds {size} bytes")
    actual = digest.hexdigest()
    if sha256 is not None and actual != sha256:
        raise BuildError(f"{label}: SHA-256 {actual}, expected {sha256}")
    return actual


def check_file(path, size, sha256):
    with path.open("rb") as source:
        if os.fstat(source.fileno()).st_size != size:
            raise BuildError(f"{path}: expected {size} bytes")
        return verify_stream(source, size, sha256, path)


@contextmanager
def atomic_file(path):
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as destination:
            yield destination
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def source_parts():
    for index, (size, sha256) in enumerate(PARTS, start=1):
        yield f"{ROM}.t0.{index:03d}", size, sha256


def open_release(stack, directory):
    parts = []
    for name, size, sha256 in source_parts():
        path = directory / name
        source = stack.enter_context(path.open("rb"))
        if os.fstat(source.fileno()).st_size != size:
            raise BuildError(f"{path}: expected {size} bytes")
        print(f"Verifying {path}", flush=True)
        verify_stream(source, size, sha256, path)
        parts.append((source, size))
    stream = stack.enter_context(io.BufferedReader(SplitReader(parts)))
    if stream.seek(0, os.SEEK_END) != WRAPPER_SIZE:
        raise BuildError("unexpected wrapper size")
    return stream


def zip64_local_sizes(extra, size, compressed_size):
    needs_size = size == 0xFFFFFFFF
    needs_compressed_size = compressed_size == 0xFFFFFFFF
    while needs_size or needs_compressed_size:
        if len(extra) < 4:
            raise BuildError("missing ZIP64 local sizes")
        field, length = struct.unpack_from("<HH", extra)
        if len(extra) < 4 + length:
            raise BuildError("truncated ZIP extra field")
        if field == 1:
            count = int(needs_size) + int(needs_compressed_size)
            if length < count * 8:
                raise BuildError("truncated ZIP64 local sizes")
            values = iter(struct.unpack_from("<" + "Q" * count, extra, 4))
            return (next(values) if needs_size else size,
                    next(values) if needs_compressed_size else compressed_size)
        extra = extra[4 + length:]
    return size, compressed_size


def validate_local(stream, entry, limit):
    name = entry.orig_filename
    if entry.flag_bits & 0x2041:
        raise BuildError(f"{name}: encrypted ZIP entry")
    if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise BuildError(f"{name}: unexpected ZIP compression method")
    if entry.compress_type == zipfile.ZIP_STORED and entry.file_size != entry.compress_size:
        raise BuildError(f"{name}: stored ZIP sizes differ")
    if not 0 <= entry.header_offset <= limit - LOCAL_HEADER.size:
        raise BuildError(f"{name}: local header outside ZIP entry bounds")
    stream.seek(entry.header_offset)
    header = stream.read(LOCAL_HEADER.size)
    if len(header) != LOCAL_HEADER.size:
        raise BuildError(f"{name}: truncated local ZIP header")
    signature, _, flags, method, _, _, crc, compressed_size, size, name_len, extra_len = (
        LOCAL_HEADER.unpack(header)
    )
    if signature != b"PK\x03\x04" or flags != entry.flag_bits or method != entry.compress_type:
        raise BuildError(f"{name}: local ZIP header disagrees with central directory")
    data_offset = entry.header_offset + LOCAL_HEADER.size + name_len + extra_len
    if data_offset + entry.compress_size > limit:
        raise BuildError(f"{name}: ZIP entry overlaps the next record")
    encoding = "utf-8" if flags & 0x800 else "cp437"
    if stream.read(name_len) != name.encode(encoding):
        raise BuildError(f"{name}: local ZIP name disagrees with central directory")
    extra = stream.read(extra_len)
    if len(extra) != extra_len:
        raise BuildError(f"{name}: truncated local ZIP extra data")
    size, compressed_size = zip64_local_sizes(extra, size, compressed_size)
    central = (entry.CRC, entry.file_size, entry.compress_size)
    local = (crc, size, compressed_size)
    # When bit 3 is set, zero marks a value deferred to the data descriptor.
    if local != central and not (
        flags & 8 and all(value in (0, expected) for value, expected in zip(local, central))
    ):
        raise BuildError(f"{name}: local ZIP sizes or CRC disagree with central directory")
    return data_offset


def select_entry(stream, archive, count, expected):
    entries = sorted(archive.infolist(), key=lambda entry: entry.header_offset)
    names = {entry.orig_filename for entry in entries}
    if len(entries) != count or len(names) != count:
        raise BuildError(f"unexpected ZIP inventory (expected {count} unique entries)")
    offsets = [entry.header_offset for entry in entries[1:]] + [archive.start_dir]
    match = None
    for entry, limit in zip(entries, offsets):
        offset = validate_local(stream, entry, limit)
        if entry.orig_filename == expected[0]:
            actual = (entry.orig_filename, entry.compress_type, entry.file_size,
                      entry.compress_size, entry.CRC)
            if actual != expected:
                raise BuildError(f"{entry.orig_filename}: unexpected ZIP entry metadata")
            match = (entry, offset)
    if match is None:
        raise BuildError(f"{expected[0]}: missing ZIP entry")
    return match


def decompress_super(source, size, sha256, path):
    # Drain stdout while feeding stdin. A decompressor error or failed ZIP CRC
    # must invalidate the temporary output even if zstd had written enough data.
    with atomic_file(path) as destination:
        with subprocess.Popen(["zstd", "-d", "-q", "-c"], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE) as process:
            def feed():
                with process.stdin as stdin:
                    shutil.copyfileobj(source, stdin, CHUNK)

            with ThreadPoolExecutor(max_workers=1) as executor:
                feeding = executor.submit(feed)
                try:
                    verify_stream(process.stdout, size, sha256, path, destination)
                    feeding.result()
                    if process.wait():
                        raise BuildError("zstd failed while decoding super.img.zst")
                except BaseException:
                    process.kill()
                    raise


def prepare_super(release, path):
    with zipfile.ZipFile(release) as wrapper:
        inner, offset = select_entry(release, wrapper, 1, INNER_ENTRY)
        with io.BufferedReader(BoundedReader(release, offset, inner.file_size)) as stream:
            with zipfile.ZipFile(stream) as archive:
                entry, _ = select_entry(stream, archive, INNER_COUNT, SUPER_ENTRY)
                if path.exists():
                    print(f"Verifying {path}", flush=True)
                    check_file(path, SUPER_SIZE, SUPER_SHA)
                else:
                    print(f"Decoding {entry.filename} into {path}", flush=True)
                    with archive.open(entry) as source:
                        decompress_super(source, SUPER_SIZE, SUPER_SHA, path)


def prepare_partitions(super_path, work):
    directory = work / "partitions"
    directory.mkdir(exist_ok=True)
    with super_path.open("rb") as raw:
        for name, (sector, size, sha256) in PARTITIONS.items():
            path = directory / f"{name}.img"
            start = sector * SECTOR
            if start + size > SUPER_SIZE:
                raise BuildError(f"{name}: partition range exceeds raw super image")
            if path.exists():
                print(f"Verifying {path}", flush=True)
                check_file(path, size, sha256)
                continue
            print(f"Extracting {path}", flush=True)
            with io.BufferedReader(BoundedReader(raw, start, size)) as source:
                with atomic_file(path) as destination:
                    verify_stream(source, size, sha256, path, destination)
    return directory


def mystic_checkout():
    mystic = ROOT / "mysticgsi"
    if not (mystic / "cli.py").is_file():
        mystic = Path("/srv/projects/OneOS/mysticgsi")
    head = subprocess.check_output(
        ["git", "-C", str(mystic), "rev-parse", "HEAD"], text=True,
    ).strip()
    if head != MYSTIC_GSI_REV:
        raise BuildError(f"{mystic}: expected MysticGSI commit {MYSTIC_GSI_REV}, found {head}")
    return mystic


def convert(mystic, partitions, work):
    with tempfile.TemporaryDirectory(prefix=".mystic-input-", dir=work) as staging:
        for name in PARTITIONS:
            filename = f"{name}.img"
            os.link(partitions / filename, Path(staging) / filename)
        # The helper drops these staging hardlinks once it has unpacked them.
        command = [str(mystic / ".venv/bin/python"), str(ROOT / "scripts/mysticgsi-from-images.py"),
                   "--drop", "--no-debloat", "--type=coloros", NAME, staging]
        last_line = ""
        with subprocess.Popen(command, stdout=subprocess.PIPE, text=True, errors="replace") as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                if line.strip():
                    last_line = line.strip()
            if process.wait():
                raise BuildError("MysticGSI conversion failed")
    result = (mystic / last_line).resolve()
    output_dir = (mystic / "out" / NAME).resolve()
    if (not last_line or output_dir not in result.parents or result.suffix != ".img"
            or not result.is_file()):
        raise BuildError(f"unexpected MysticGSI output path: {last_line!r}")
    return result


def image_details(path, sha256=None, size=None):
    with path.open("rb") as source:
        actual_size = os.fstat(source.fileno()).st_size
        source.seek(1080)
        if source.read(2) != b"\x53\xef":
            raise BuildError(f"{path}: missing ext4 superblock magic")
        source.seek(0)
        if size is not None and size != actual_size:
            raise BuildError(f"{path}: expected {size} bytes")
        actual_sha = verify_stream(source, actual_size, sha256, path)
    return {"size": actual_size, "sha256": actual_sha}


def entry_metadata(entry):
    name, method, size, compressed_size, crc = entry
    return {"name": name, "method": method, "size": size, "compressed_size": compressed_size,
            "crc32": f"{crc:08x}"}


def metadata(output, details):
    return {
        "release": RELEASE,
        "source_parts": [{"name": name, "size": size, "sha256": sha}
                         for name, size, sha in source_parts()],
        "wrapper": {"size": WRAPPER_SIZE, "inner_entry": entry_metadata(INNER_ENTRY)},
        "inner_zip": {"entries": INNER_COUNT, "super_entry": entry_metadata(SUPER_ENTRY)},
        "super": {"size": SUPER_SIZE, "sha256": SUPER_SHA, "sector_size": SECTOR},
        "partitions": {name: {"start_sector": start, "size": size, "sha256": sha}
                       for name, (start, size, sha) in PARTITIONS.items()},
        "mysticgsi_commit": MYSTIC_GSI_REV,
        "android": ANDROID,
        "image": {"path": str(output), **details},
    }


def publish(source, output, manifest):
    # Pin the inode before hashing it. MysticGSI may replace its named output
    # when another conversion finishes while this process is reading the image.
    with tempfile.TemporaryDirectory(prefix=".publish-", dir=output.parent) as temporary:
        staged = Path(temporary) / output.name
        os.link(source, staged)
        details = image_details(staged)
        os.link(staged, output)
        try:
            with atomic_file(manifest) as destination:
                destination.write((json.dumps(metadata(output, details), indent=2) + "\n").encode())
        except BaseException:
            output.unlink()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True, help="directory with the four release parts")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "out/twinity-image")
    parser.add_argument("--prepare-only", action="store_true", help="stop after extracting verified partitions")
    args = parser.parse_args()

    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        lock = stack.enter_context((work / ".lock").open("a"))
        fcntl.flock(lock, fcntl.LOCK_EX)
        release = open_release(stack, args.source_dir)
        output = work / "system.img"
        manifest = work / "system.json"
        if not args.prepare_only and (os.path.lexists(output) or os.path.lexists(manifest)):
            if not output.is_file() or not manifest.is_file():
                raise BuildError("incomplete output in work directory; use a fresh --work-dir")
            recorded = json.loads(manifest.read_text())
            image = recorded.get("image") if isinstance(recorded, dict) else None
            if (not isinstance(image, dict) or not isinstance(image.get("sha256"), str)
                    or not isinstance(image.get("size"), int)):
                raise BuildError("missing cached image digest or size")
            details = image_details(output, image["sha256"], image["size"])
            if recorded != metadata(output, details):
                raise BuildError("cached image metadata differs from the pinned build")
        else:
            mystic = None if args.prepare_only else mystic_checkout()
            super_path = work / "super.raw"
            prepare_super(release, super_path)
            partitions = prepare_partitions(super_path, work)
            if args.prepare_only:
                print(partitions)
                return
            publish(convert(mystic, partitions, work), output, manifest)
        print(output)


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, subprocess.SubprocessError, ValueError, zipfile.BadZipFile) as error:
        sys.exit(f"twinity image: {error}")
