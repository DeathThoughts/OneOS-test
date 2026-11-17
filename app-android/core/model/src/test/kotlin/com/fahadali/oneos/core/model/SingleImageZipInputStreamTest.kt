package com.fahadali.oneos.core.model

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.DigestInputStream
import java.security.MessageDigest
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipException
import java.util.zip.ZipOutputStream
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class SingleImageZipInputStreamTest {
    private val name = "system.img"
    private val image = ByteArray(4096) { it.toByte() }

    private fun archive(vararg entries: Pair<String, ByteArray>, stored: Boolean = false): ByteArray {
        val buffer = ByteArrayOutputStream()
        ZipOutputStream(buffer).use { zip ->
            for ((entryName, bytes) in entries) {
                val entry = ZipEntry(entryName)
                if (stored) {
                    entry.method = ZipEntry.STORED
                    entry.size = bytes.size.toLong()
                    entry.crc = CRC32().apply { update(bytes) }.value
                }
                zip.putNextEntry(entry)
                zip.write(bytes)
                zip.closeEntry()
            }
        }
        return buffer.toByteArray()
    }

    private fun open(archive: ByteArray, entryName: String = name, rawBytes: Long = image.size.toLong()) =
        SingleImageZipInputStream(ByteArrayInputStream(archive), entryName, rawBytes)

    private fun payloadOffset(archive: ByteArray): Int {
        val header = ByteBuffer.wrap(archive).order(ByteOrder.LITTLE_ENDIAN)
        val filenameLength = header.getShort(26).toInt() and 0xffff
        val extraLength = header.getShort(28).toInt() and 0xffff
        return 30 + filenameLength + extraLength
    }

    @Test fun streamsTheExactImageAndKeepsEof() {
        open(archive(name to image)).use { input ->
            assertEquals(image.first().toInt() and 0xff, input.read())
            assertArrayEquals(image.copyOfRange(1, image.size), input.readBytes())
            assertEquals(-1, input.read())
            assertEquals(-1, input.read(ByteArray(8)))
            assertEquals(0, input.read(ByteArray(0)))
        }
    }

    @Test fun checksKnownEntrySizeAtOpen() {
        val archive = archive(name to image, stored = true)
        open(archive).use { assertArrayEquals(image, it.readBytes()) }
        assertThrows(ZipException::class.java) { open(archive, rawBytes = image.size.toLong() + 1) }
    }

    @Test fun rejectsWrongNamesAndPaths() {
        for (wrong in listOf("vendor.img", "subdir/$name", "../$name", "./$name")) {
            assertThrows(ZipException::class.java) { open(archive(wrong to image)) }
        }
    }

    @Test fun rejectsEmptyArchiveAndDirectoryFirst() {
        assertThrows(ZipException::class.java) { open(archive()) }
        assertThrows(ZipException::class.java) { open(archive("$name/" to byteArrayOf()), "$name/") }
        assertThrows(ZipException::class.java) { open(archive("folder/" to byteArrayOf(), name to image)) }
    }

    @Test fun rejectsAnySecondEntryAndNeverStreamsIt() {
        for (extra in listOf("extra.img" to image, "folder/" to byteArrayOf())) {
            open(archive(name to image, extra)).use { input ->
                assertThrows(ZipException::class.java) { input.readBytes() }
                assertThrows(ZipException::class.java) { input.read() }
            }
        }
    }

    @Test fun rejectsCorruptEntryCrc() {
        val archive = archive(name to image, stored = true)
        val position = payloadOffset(archive) + image.size / 2
        archive[position] = (archive[position].toInt() xor 1).toByte()
        open(archive).use { input ->
            val failure = assertThrows(ZipException::class.java) { input.readBytes() }
            assertTrue(failure.message.orEmpty().contains("CRC", ignoreCase = true))
        }
    }

    @Test fun rejectsTruncatedEntry() {
        val valid = archive(name to image)
        val truncated = valid.copyOf(payloadOffset(valid) + 1)
        open(truncated).use { input ->
            assertThrows(IOException::class.java) { input.readBytes() }
        }
    }

    @Test fun drainingSourceAfterImageEofHashesEntireArchive() {
        val archive = archive(name to image) + ByteArray(4096) { 42 }
        val rawSource = ByteArrayInputStream(archive)
        val digest = MessageDigest.getInstance("SHA-256")
        val source = DigestInputStream(rawSource, digest)
        SingleImageZipInputStream(source, name, image.size.toLong()).use { input ->
            assertArrayEquals(image, input.readBytes())
            val unread = rawSource.available()
            assertTrue(unread > 0)
            assertEquals(unread, source.readBytes().size)
            assertEquals(0, rawSource.available())
            assertArrayEquals(MessageDigest.getInstance("SHA-256").digest(archive), digest.digest())
        }
    }
}
