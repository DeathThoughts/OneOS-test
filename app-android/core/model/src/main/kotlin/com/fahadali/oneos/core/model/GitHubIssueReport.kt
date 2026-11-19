package com.fahadali.oneos.core.model

import java.net.URLEncoder
import java.nio.charset.StandardCharsets

/** User-reviewed issues, with the complete redacted logs attached separately as a ZIP. */
object GitHubIssueReport {
    const val ISSUES = "https://github.com/DeathThoughts/OneOS-test/issues"

    fun body(model: String, app: String, archive: String, summary: String, metadata: String): String = buildString {
        append("## What happened\n\nDescribe the problem and steps to reproduce it here.\n\n")
        append("## Device and build\n\n")
        append("Phone: ${Reports.cleanText(model, 100)}\n\nOneOS collecting this report: ${Reports.cleanText(app, 150)}\n\n")
        append("## Logs\n\nAttach **${Reports.cleanText(archive, 180)}** below before submitting. ")
        append("The ZIP contains all collected, redacted logs, including the complete report.json and file hashes. ")
        append("Check the logs for personal information before making them public.\n\n")
        append("### Summary\n\n~~~~text\n${excerpt(summary, 12_000)}\n~~~~\n\n")
        append("### Report details\n\n~~~~json\n${excerpt(metadata, 24_000)}\n~~~~\n")
    }

    /** A bounded link for browsers. Large summaries stay on the clipboard and in the archive. */
    fun newIssueUrl(model: String, app: String, archive: String, summary: String): String {
        val title = "[Report] ${Reports.cleanText(model, 80)} · ${Reports.cleanText(app, 100)}"
        val body = body(model, app, archive, summary.take(500), "Full details are in the attached ZIP.")
        return "$ISSUES/new?title=${encode(title)}&body=${encode(body)}"
    }

    private fun encode(text: String) = URLEncoder.encode(text, StandardCharsets.UTF_8.name())
    private fun excerpt(text: String, limit: Int): String = text.take(limit).replace("~~~~", "~~~ ~") +
        if (text.length > limit) "\n[Excerpt. Complete information is in the ZIP.]" else ""
}
