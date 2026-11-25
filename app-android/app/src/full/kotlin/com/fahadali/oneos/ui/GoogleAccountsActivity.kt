package com.fahadali.oneos.ui

import android.accounts.AccountManager
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.view.WindowManager
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.fahadali.oneos.core.model.GoogleAccountQueue
import com.fahadali.oneos.core.root.RootScript
import com.fahadali.oneos.core.root.RootShell
import com.fahadali.oneos.session.BringAppsJob
import com.fahadali.oneos.session.LaunchJob
import com.fahadali.oneos.session.PrepareJob
import com.fahadali.oneos.session.RootVerbs
import com.fahadali.oneos.session.StageCheck

/** The Android account picker grants visibility; never inspect AccountManager's private data. */
class GoogleAccountsActivity : Activity() {
    private val selected = linkedSetOf<String>()
    private val names = linkedSetOf<String>()
    private var busy = false
    private lateinit var column: LinearLayout
    private var message = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        title = "Import Google accounts"
        names.addAll(visible())
        names.addAll(savedInstanceState?.getStringArrayList("names").orEmpty().filter(GoogleAccountQueue::valid))
        selected.addAll(savedInstanceState?.getStringArrayList("selected")?.filter { it in names } ?: names)
        column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val pad = (20 * resources.displayMetrics.density).toInt()
            setPadding(pad, pad, pad, pad)
        }
        setContentView(ScrollView(this).apply { addView(column) })
        render()
    }

    private fun visible(): List<String> = runCatching {
        AccountManager.get(this).getAccountsByType(GoogleAccountQueue.TYPE).map { it.name }.filter(GoogleAccountQueue::valid)
    }.getOrDefault(emptyList())

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putStringArrayList("names", ArrayList(names))
        outState.putStringArrayList("selected", ArrayList(selected))
    }

    private fun button(label: String, action: () -> Unit) {
        column.addView(Button(this).apply { text = label; isEnabled = !busy; setOnClickListener { action() } })
    }

    private fun render() {
        column.removeAllViews()
        column.addView(TextView(this).apply {
            text = "Import account addresses to the prepared guest. Google will handle authentication there. No passwords or tokens are copied. Accounts not visible to OneOS can be selected using Android's account picker."
        })
        for (name in names.sorted()) {
            column.addView(CheckBox(this).apply {
                text = name
                isChecked = name in selected
                isEnabled = !busy
                setOnCheckedChangeListener { _, checked -> if (checked) selected.add(name) else selected.remove(name) }
            })
        }
        button("Select all visible accounts") { names.addAll(visible()); selected.addAll(names); render() }
        button("Choose another Google account") {
            runCatching {
                startActivityForResult(AccountManager.newChooseAccountIntent(null, null,
                    arrayOf(GoogleAccountQueue.TYPE), null, null, null, null), 701)
            }.onFailure { message = "The Android account picker is unavailable."; render() }
        }
        button("Import selected on next guest boot") {
            if (selected.isEmpty()) { message = "Select at least one account."; render() }
            else AlertDialog.Builder(this).setTitle("Import account addresses?")
                .setMessage("Queue ${selected.size} selected accounts for this guest only. The accounts in One UI remain unchanged.")
                .setNegativeButton("Cancel", null).setPositiveButton("Import") { _, _ -> submit(false) }.show()
        }
        button("Cancel pending transfer") { submit(true) }
        button("Back") { finish() }
        if (message.isNotEmpty()) column.addView(TextView(this).apply { text = message })
    }

    @Deprecated("Android account picker result")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 701 && resultCode == RESULT_OK &&
            data?.getStringExtra(AccountManager.KEY_ACCOUNT_TYPE) == GoogleAccountQueue.TYPE) {
            val name = data?.getStringExtra(AccountManager.KEY_ACCOUNT_NAME)
            if (name != null && GoogleAccountQueue.valid(name)) { names.add(name); selected.add(name) }
            names.addAll(visible())
            render()
        }
    }

    private fun submit(clear: Boolean) {
        if (busy || PrepareJob.state.busy || LaunchJob.state.busy || BringAppsJob.state.busy || StageCheck.state.busy) {
            message = "Finish the current OneOS operation first."; render(); return
        }
        val image = intent.getStringExtra("image") ?: return
        val created = intent.getStringExtra("created") ?: return
        val payload = runCatching { if (clear) ByteArray(0) else GoogleAccountQueue.encode(selected) }.getOrElse {
            message = "The selected account list is invalid or too large."; render(); return
        }
        busy = true
        message = "Preparing private account list…"
        render()
        val app = applicationContext
        Thread {
            val ok = runCatching {
                RootVerbs.run(app, RootScript.Verb.GOOGLE_ACCOUNTS,
                    listOf(image, created, payload.size.toString()), RootShell.Feed { it.write(payload) }).ok
            }.getOrDefault(false)
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                busy = false
                message = if (ok) {
                    if (clear) "Pending transfer cleared." else "Ready. Open Import Google accounts in the guest after boot."
                } else "Could not queue accounts. Prepare/update this guest and try again."
                render()
            }
        }.start()
    }
}
