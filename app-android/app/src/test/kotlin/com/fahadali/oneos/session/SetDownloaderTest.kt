package com.fahadali.oneos.session

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SetDownloaderTest {
    private val sha = "a".repeat(64)
    private val fork = "gh:DeathThoughts/OneOS-test:fork-payloads-v1"
    private val pyb = "gh:DeathThoughts/OneOS-test:pyb110-payloads-v2"

    @Test fun legacySourcesDoNotBlockValidServerSideCorrections() {
        val sources = listOf(fork, "gh:sets-1", "sf", "1pI-m5yEGgSuqN-BWHtjyM0IckKMBvl-z", fork, pyb)
        assertEquals(listOf(fork, pyb), SetDownloader.supportedSources(sources, sha))
    }

    @Test fun refusesToUseUnsupportedOrExternalDownloadUrls() {
        assertTrue(SetDownloader.supportedSources(listOf("sf", "drive-id", "https://example.com/payload"), sha).isEmpty())
        assertTrue(SetDownloader.supportedSources(listOf("gh:evil/repo:../../secret"), sha).isEmpty())
    }

    @Test fun keepsPinnedGitHubReleaseUrlFormat() {
        assertEquals(
            "https://github.com/DeathThoughts/OneOS-test/releases/download/pyb110-payloads-v2/$sha",
            SetDownloader.url(pyb, sha),
        )
    }
    // Local HTTP fixture: never depends on GitHub, real device network or credentials.
    private fun withServer(data: ByteArray, badRange: Boolean = false, block: (String) -> Unit) {
        val server = com.sun.net.httpserver.HttpServer.create(java.net.InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/payload") { exchange ->
            val start = exchange.requestHeaders.getFirst("Range")?.removePrefix("bytes=")?.removeSuffix("-")?.toIntOrNull()
            if (start != null) {
                val body = data.copyOfRange(start, data.size)
                exchange.responseHeaders.add("Content-Range", "bytes ${if (badRange) start - 1 else start}-${data.size - 1}/${data.size}")
                exchange.sendResponseHeaders(206, body.size.toLong())
                exchange.responseBody.use { it.write(body) }
            } else {
                exchange.sendResponseHeaders(200, data.size.toLong())
                exchange.responseBody.use { it.write(data) }
            }
            exchange.close()
        }
        server.start()
        try {
            block("http://127.0.0.1:${server.address.port}/payload")
        } finally {
            server.stop(0)
        }
    }

    @Test fun streamedHashAndResumedPrefixAreVerified() {
        val data = ByteArray(700_000) { (it % 239).toByte() }
        val expected = com.fahadali.oneos.core.model.Hashes.sha256(data)
        val part = java.io.File.createTempFile("oneos-test", ".part")
        try {
            withServer(data) { url ->
                SetDownloader.download(part, url, data.size.toLong(), "fixture", { false }, expectedSha = expected) {}
                org.junit.Assert.assertArrayEquals(data, part.readBytes())
                part.writeBytes(data.copyOfRange(0, 210_000))
                SetDownloader.download(part, url, data.size.toLong(), "fixture", { false }, expectedSha = expected) {}
                org.junit.Assert.assertArrayEquals(data, part.readBytes())
                part.writeBytes(byteArrayOf(0, 0, 0))
                val damaged = runCatching {
                    SetDownloader.download(part, url, data.size.toLong(), "fixture", { false }, expectedSha = expected) {}
                }.exceptionOrNull()
                assertTrue(damaged is SetDownloader.Failed)
                assertTrue(!part.exists())
            }
        } finally {
            part.delete()
        }
    }

    @Test fun invalidResumeRangeIsDiscardedRatherThanAppended() {
        val data = ByteArray(128) { it.toByte() }
        val part = java.io.File.createTempFile("oneos-range", ".part")
        try {
            part.writeBytes(data.copyOfRange(0, 50))
            withServer(data, badRange = true) { url ->
                val failed = runCatching {
                    SetDownloader.download(part, url, data.size.toLong(), "fixture", { false },
                        expectedSha = com.fahadali.oneos.core.model.Hashes.sha256(data)) {}
                }.exceptionOrNull()
                assertTrue(failed is java.io.IOException)
                assertTrue(!part.exists())
            }
        } finally {
            part.delete()
        }
    }
}
