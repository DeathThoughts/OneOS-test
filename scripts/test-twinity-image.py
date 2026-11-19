#!/usr/bin/env python3
"""Exercise Twinity's archive and image checks without downloading the ROM."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SPEC = importlib.util.spec_from_file_location(
    "build_twinity_image", Path(__file__).with_name("build-twinity-image.py")
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_zip(entries, zip64=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data, method in entries:
            entry = zipfile.ZipInfo(name)
            entry.compress_type = method
            with archive.open(entry, "w", force_zip64=zip64) as destination:
                destination.write(data)
    return stream.getvalue()


def inventory(data, name):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entry = archive.getinfo(name)
        return (name, entry.compress_type, entry.file_size, entry.compress_size, entry.CRC)


class ReaderTests(unittest.TestCase):
    def test_split_and_window_seek(self):
        split = builder.SplitReader([(io.BytesIO(b"abc"), 3), (io.BytesIO(b"defgh"), 5)])
        window = builder.BoundedReader(split, 2, 4)
        self.assertEqual(window.read(3), b"cde")
        self.assertEqual(window.seek(-2, 1), 1)
        self.assertEqual(window.read(), b"def")
        self.assertEqual(window.read(1), b"")
        self.assertEqual(window.seek(1, 2), 5)
        self.assertEqual(window.read(), b"")
        with self.assertRaisesRegex(OSError, "negative seek"):
            window.seek(-1)
        window.close()
        self.assertEqual(split.seek(-2, 2), 6)
        self.assertEqual(split.read(), b"gh")

    def test_short_underlying_stream(self):
        split = builder.SplitReader([(io.BytesIO(b"a"), 2)])
        window = builder.BoundedReader(io.BytesIO(b"a"), 1, 1)
        for source in (split, window):
            with self.subTest(reader=type(source)), self.assertRaisesRegex(
                    builder.BuildError, "short read"):
                source.read()

    def test_size_and_digest_checks(self):
        for data, size, digest, message in (
            (b"a", 2, None, "expected 2 bytes, got 1"),
            (b"ab", 1, None, "exceeds 1 bytes"),
            (b"a", 1, sha(b"b"), "SHA-256"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(builder.BuildError, message):
                builder.verify_stream(io.BytesIO(data), size, digest, "test")


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.data = make_zip([("firmware/super.zst", b"a" * 200, zipfile.ZIP_DEFLATED)], zip64=True)
        self.expected = inventory(self.data, "firmware/super.zst")
        self.central = self.data.index(b"PK\x01\x02")

    def select(self, data=None, count=1, expected=None):
        stream = io.BytesIO(self.data if data is None else data)
        with zipfile.ZipFile(stream) as archive:
            return builder.select_entry(stream, archive, count, expected or self.expected)

    def test_zip64_local_header(self):
        _, offset = self.select()
        self.assertEqual(offset, 30 + len(self.expected[0]) + 20)

    def test_incorrect_inventory_or_method(self):
        with self.assertRaisesRegex(builder.BuildError, "unexpected ZIP inventory"):
            self.select(count=2)
        with self.assertRaisesRegex(builder.BuildError, "unexpected ZIP entry metadata"):
            self.select(expected=(self.expected[0], zipfile.ZIP_STORED, *self.expected[2:]))
        with self.assertRaisesRegex(builder.BuildError, "missing ZIP entry"):
            self.select(expected=("other", *self.expected[1:]))

    def test_incorrect_local_header(self):
        for offset, size, value, message in (
            (0, 4, 0, "local ZIP header"),
            (8, 2, zipfile.ZIP_STORED, "local ZIP header"),
            (14, 4, 0, "local ZIP sizes or CRC"),
            (26, 2, 255, "overlaps"),
            (30, 1, ord("x"), "local ZIP name"),
            (30 + len(self.expected[0]) + 2, 2, 21, "truncated ZIP extra"),
        ):
            with self.subTest(offset=offset), self.assertRaisesRegex(builder.BuildError, message):
                data = bytearray(self.data)
                data[offset:offset + size] = value.to_bytes(size, "little")
                self.select(data)

    def test_encrypted_and_unsupported_methods(self):
        for local_offset, central_offset, value, message in (
            (6, self.central + 8, 1, "encrypted"),
            (8, self.central + 10, zipfile.ZIP_BZIP2, "compression method"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(builder.BuildError, message):
                data = bytearray(self.data)
                struct.pack_into("<H", data, local_offset, value)
                struct.pack_into("<H", data, central_offset, value)
                self.select(data)

    def test_truncated_archive(self):
        with self.assertRaises(zipfile.BadZipFile):
            self.select(self.data[:-5])


class AtomicTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def test_atomic_file_preserves_previous_contents(self):
        path = self.work / "super.raw"
        path.write_bytes(b"previous")
        with self.assertRaisesRegex(builder.BuildError, "test failure"):
            with builder.atomic_file(path) as output:
                output.write(b"partial")
                raise builder.BuildError("test failure")
        self.assertEqual(path.read_bytes(), b"previous")
        self.assertEqual(list(self.work.iterdir()), [path])

    def test_publish_verifies_ext4_and_does_not_clobber(self):
        source = self.work / "mystic.img"
        output = self.work / "system.img"
        manifest = self.work / "system.json"
        source.write_bytes(b"bad")
        with self.assertRaisesRegex(builder.BuildError, "missing ext4"):
            builder.publish(source, output, manifest)
        self.assertFalse(output.exists())
        self.assertFalse(manifest.exists())

        image = bytes(1080) + b"\x53\xef"
        source.write_bytes(image)
        builder.publish(source, output, manifest)
        self.assertEqual(output.stat().st_ino, source.stat().st_ino)
        self.assertEqual(json.loads(manifest.read_text())["image"], {
            "path": str(output), "sha256": sha(image), "size": len(image),
        })
        with self.assertRaises(FileExistsError):
            builder.publish(source, output, manifest)
        self.assertEqual(output.read_bytes(), image)

    def test_manifest_failure_removes_new_image_link(self):
        source = self.work / "mystic.img"
        source.write_bytes(bytes(1080) + b"\x53\xef")
        output = self.work / "system.img"
        manifest = self.work / "system.json"
        manifest.mkdir()
        with self.assertRaises(OSError):
            builder.publish(source, output, manifest)
        self.assertFalse(output.exists())
        self.assertTrue(manifest.is_dir())

    @unittest.skipUnless(shutil.which("zstd"), "requires the host zstd decoder")
    def test_nested_zip_to_verified_partition(self):
        data = b"partition contents" * 100
        compressed = subprocess.check_output(["zstd", "-q", "-c"], input=data)
        name = "images/super.img.zst"
        inner = make_zip([(name, compressed, zipfile.ZIP_DEFLATED),
                          ("installer", b"unused", zipfile.ZIP_STORED)], zip64=True)
        outer = make_zip([("inner.zip", inner, zipfile.ZIP_STORED)], zip64=True)
        split = builder.SplitReader(
            [(io.BytesIO(chunk), len(chunk)) for chunk in (outer[:13], outer[13:75], outer[75:])]
        )
        path = self.work / "super.raw"
        with patch.multiple(builder, INNER_ENTRY=inventory(outer, "inner.zip"), INNER_COUNT=2,
                            SUPER_ENTRY=inventory(inner, name), SUPER_SIZE=len(data),
                            SUPER_SHA=sha(data), PARTITIONS={"system": (0, len(data), sha(data))}):
            builder.prepare_super(split, path)
            builder.prepare_super(split, path)
            directory = builder.prepare_partitions(path, self.work)
            builder.prepare_partitions(path, self.work)
            self.assertEqual((directory / "system.img").read_bytes(), data)

    @unittest.skipUnless(shutil.which("zstd"), "requires the host zstd decoder")
    def test_decompressor_failure_keeps_previous_file(self):
        path = self.work / "super.raw"
        path.write_bytes(b"previous")
        for source, size, digest in (
            (io.BytesIO(b"not zstd"), 0, sha(b"")),
            (io.BytesIO(subprocess.check_output(["zstd", "-q", "-c"], input=b"too big")), 2, None),
        ):
            with self.assertRaises(builder.BuildError):
                builder.decompress_super(source, size, digest, path)
            self.assertEqual(path.read_bytes(), b"previous")
            self.assertEqual(list(self.work.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
