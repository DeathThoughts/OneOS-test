package com.fahadali.oneos.core.model

import java.net.URLDecoder
import org.junit.Assert.*
import org.junit.Test

class GitHubIssueReportTest {
    @Test fun fullReportKeepsLineBreaksAndNamesTheCompleteArchive() {
        val report = GitHubIssueReport.body("SM-S911B", "166", "logs.zip", "first\nsecond", "{\n  \"image\": \"coloros17\"\n}")
        assertTrue(report.contains("first\nsecond"))
        assertTrue(report.contains("logs.zip"))
        assertTrue(report.contains("image"))
        assertTrue(report.contains("before submitting"))
    }

    @Test fun largeUnicodeLogsProduceABoundedForkIssueLink() {
        val link = GitHubIssueReport.newIssueUrl("SM-A556B", "166", "logs.zip", "😃\n".repeat(100_000))
        assertTrue(link.startsWith("https://github.com/DeathThoughts/OneOS-test/issues/new?"))
        assertTrue(link.length < 8_000)
        assertTrue(URLDecoder.decode(link, "UTF-8").contains("logs.zip"))
        assertTrue(GitHubIssueReport.body("x", "x", "x", "z".repeat(100_000), "m".repeat(100_000)).length < 40_000)
    }
}
