package com.fahadali.oneos.core.model

import java.io.IOException
import java.io.InputStream
import java.util.zip.ZipException
import java.util.zip.ZipInputStream

/** Streams one exact image entry. The caller verifies the raw image and the entire archive. */
class SingleImageZipInputStream(
    source: InputStream,
    private val entryName: String,
    rawBytes: Long,
) : InputStream() {
    private val zip = ZipInputStream(source)
    private val one = ByteArray(1)
    private var complete = false
    private var failure: IOException? = null

    init {
        try {
            val entry = zip.nextEntry
            if (entry == null || entry.isDirectory || entry.name != entryName) {
                throw ZipException("corrupt ZIP archive: expected $entryName as its only entry")
            }
            if (entry.size != -1L && entry.size != rawBytes) {
                throw ZipException("corrupt ZIP archive: $entryName declares ${entry.size} bytes, expected $rawBytes")
            }
        } catch (error: Throwable) {
            runCatching { zip.close() }.exceptionOrNull()?.let(error::addSuppressed)
            throw error
        }
    }

    override fun read(): Int = if (read(one, 0, 1) < 0) -1 else one[0].toInt() and 0xff

    override fun read(b: ByteArray, off: Int, len: Int): Int {
        if (off < 0 || len < 0 || off > b.size - len) throw IndexOutOfBoundsException()
        if (len == 0) return 0
        failure?.let { throw it }
        if (complete) return -1

        try {
            val n = zip.read(b, off, len)
            if (n < 0) {
                // ZipInputStream verifies the entry's CRC before reporting EOF.
                zip.closeEntry()
                if (zip.nextEntry != null) throw ZipException("corrupt ZIP archive: expected only $entryName")
                complete = true
            }
            return n
        } catch (error: IOException) {
            failure = error
            throw error
        }
    }

    override fun close() = zip.close()
}
