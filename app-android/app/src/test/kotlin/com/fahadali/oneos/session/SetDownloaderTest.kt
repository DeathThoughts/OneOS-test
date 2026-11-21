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
        assertEquals(listOf(fork, "gh:sets-1", pyb), SetDownloader.supportedSources(sources, sha))
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
}
