package com.fahadali.oneos.ui

import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.fragment.app.Fragment
import androidx.preference.Preference
import androidx.preference.PreferenceCategory
import androidx.preference.PreferenceFragmentCompat
import com.fahadali.oneos.BuildConfig
import com.fahadali.oneos.R
import com.fahadali.oneos.core.model.GitHubIssueReport
import com.fahadali.oneos.core.model.Units
import com.fahadali.oneos.probe.ProbeSession
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors

/** Prepares a public issue in this fork. The user attaches the full ZIP and submits in the browser. */
class SendReportActivity : ToolbarActivity() {
    override val screenTitle: CharSequence get() = getString(R.string.send_title)

    val zip: File? by lazy {
        intent.getStringExtra(EXTRA_ZIP)?.let(::File)?.takeIf { f ->
            val dir = f.parentFile?.canonicalPath
            f.isFile && !java.nio.file.Files.isSymbolicLink(f.toPath()) && f.name.endsWith(".zip") &&
                (dir == FindingsActivity.logsDir(this).canonicalPath || dir == ProbeSession.findingsDir(this).canonicalPath)
        }
    }

    override fun createFragment(): Fragment = SendReportFragment()

    companion object {
        const val EXTRA_ZIP = "com.fahadali.oneos.extra.REPORT_ZIP"
        fun intent(ctx: Context, zip: File): Intent = Intent(ctx, SendReportActivity::class.java).putExtra(EXTRA_ZIP, zip.path)
    }
}

class SendReportFragment : PreferenceFragmentCompat() {
    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private var saving = false
    private val zip: File? get() = (activity as? SendReportActivity)?.zip
    private val save = registerForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        val file = zip
        if (uri != null && file != null) saveZip(file, uri)
    }

    override fun onCreatePreferences(savedInstanceState: Bundle?, rootKey: String?) {
        preferenceScreen = preferenceManager.createPreferenceScreen(preferenceManager.context)
        val file = zip ?: run {
            category("").row(getString(R.string.findings_none), null)
            return
        }
        val cat = category(getString(R.string.github_report_destination))
        cat.row(getString(R.string.github_report_instructions), getString(R.string.github_report_privacy))
        cat.row(file.name, Units.bytes(file.length()))
        cat.row(getString(R.string.github_report_preview), null) {
            startActivity(Intent(requireContext(), FindingsActivity::class.java).apply {
                if (file.parentFile?.canonicalPath == FindingsActivity.logsDir(requireContext()).canonicalPath)
                    putExtra(FindingsActivity.EXTRA_ZIP, file.path)
            })
        }
        cat.row(getString(R.string.github_report_save), getString(R.string.github_report_save_summary)) { save.launch(file.name) }
        cat.row(getString(R.string.github_report_copy), getString(R.string.github_report_copy_summary)) { prepare(file, open = false) }
        cat.row(getString(R.string.github_report_open), getString(R.string.github_report_open_summary)) { prepare(file, open = true) }
    }

    private fun prepare(file: File, open: Boolean) {
        val ctx = requireContext()
        io.execute {
            val result = runCatching {
                // Read only the same already-redacted folder that Findings previews. Never a raw capture.
                val folder = ProbeSession.folderOf(file)
                fun read(name: String, limit: Int): String {
                    val source = File(folder, name)
                    if (!source.isFile || java.nio.file.Files.isSymbolicLink(source.toPath()) || source.canonicalFile.parentFile != folder.canonicalFile)
                        return ""
                    return source.reader().use { it.readTextBounded(limit) }
                }
                val summary = read("summary.txt", 12_001)
                val json = runCatching { JSONObject(read("report.json", 1_048_576)) }.getOrNull()
                // File inventory stays complete in report.json in the ZIP, keeping the pasted issue readable.
                val metadata = json?.apply { remove("files") }?.toString(2).orEmpty()
                Triple(summary, metadata, GitHubIssueReport.body(Build.MODEL,
                    "${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})", file.name, summary, metadata))
            }
            main.post {
                if (!isAdded) return@post
                val (summary, _, body) = result.getOrElse {
                    Toast.makeText(ctx, R.string.github_report_prepare_failed, Toast.LENGTH_LONG).show()
                    return@post
                }
                (ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                    .setPrimaryClip(ClipData.newPlainText("OneOS issue report", body))
                Toast.makeText(ctx, R.string.github_report_copied, Toast.LENGTH_LONG).show()
                if (open) try {
                    startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(GitHubIssueReport.newIssueUrl(
                        Build.MODEL, "${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})", file.name, summary,
                    ))).addCategory(Intent.CATEGORY_BROWSABLE))
                } catch (_: ActivityNotFoundException) {
                    Toast.makeText(ctx, R.string.about_no_browser, Toast.LENGTH_SHORT).show()
                }
            }
        }
    }

    private fun java.io.Reader.readTextBounded(limit: Int): String {
        val out = StringBuilder()
        val buffer = CharArray(4096)
        while (out.length < limit) {
            val n = read(buffer, 0, minOf(buffer.size, limit - out.length))
            if (n < 0) break
            out.append(buffer, 0, n)
        }
        return out.toString()
    }

    private fun saveZip(file: File, uri: Uri) {
        if (saving) return
        saving = true
        val app = requireContext().applicationContext
        io.execute {
            val ok = runCatching {
                app.contentResolver.openOutputStream(uri, "w")!!.use { out -> file.inputStream().use { it.copyTo(out) } }
            }.isSuccess
            main.post {
                saving = false
                Toast.makeText(app, if (ok) R.string.findings_saved else R.string.findings_save_failed, Toast.LENGTH_LONG).show()
            }
        }
    }

    override fun onDestroy() {
        io.shutdown()
        super.onDestroy()
    }

    private fun category(title: String) = PreferenceCategory(preferenceManager.context).also {
        it.title = title
        it.isIconSpaceReserved = false
        preferenceScreen.addPreference(it)
    }

    private fun PreferenceCategory.row(title: String, summary: String?, click: (() -> Unit)? = null) {
        addPreference(Preference(context).apply {
            this.title = title
            this.summary = summary
            isIconSpaceReserved = false
            if (click == null) isSelectable = false else setOnPreferenceClickListener { click(); true }
        })
    }
}
