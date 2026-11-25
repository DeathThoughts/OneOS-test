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
import com.fahadali.oneos.BuildConfig
import com.fahadali.oneos.core.model.Images
import com.fahadali.oneos.core.model.GoogleAccountQueue
import com.fahadali.oneos.core.root.RootScript
import com.fahadali.oneos.core.root.RootShell
import com.fahadali.oneos.session.BringAppsJob
import com.fahadali.oneos.session.LaunchJob
import com.fahadali.oneos.session.PrepareJob
import com.fahadali.oneos.session.RootVerbs
import com.fahadali.oneos.session.StageCheck
import com.fahadali.oneos.session.LogJob
import com.fahadali.oneos.session.OldStagesJob
import com.fahadali.oneos.session.RemoveJob

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
        val count = TextView(this)
        fun updateCount() {
            count.text = if (names.isEmpty()) "No accounts are visible yet. Tap Choose another Google account to select an existing account in Android's picker."
                else "${selected.size} selected / ${names.size} available. Select any additional accounts with the picker."
        }
        updateCount()
        column.addView(count)
        for (name in names.sorted()) {
            column.addView(CheckBox(this).apply {
                text = name
                isChecked = name in selected
                isEnabled = !busy
                setOnCheckedChangeListener { _, checked -> if (checked) selected.add(name) else selected.remove(name); updateCount() }
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
            else submit(false)
        }
        button("Cancel pending transfer") { submit(true) }
        column.addView(TextView(this).apply {
            text = "You can select accounts before setup. To transfer, prepare or update the guest in OneOS first; no reset is required just to open this screen."
        })
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

    private fun operationsBusy(): Boolean = PrepareJob.state.busy || LaunchJob.state.busy ||
        BringAppsJob.state.busy || StageCheck.state.busy || LogJob.state.busy || OldStagesJob.state.busy || RemoveJob.state.busy

    private fun submit(clear: Boolean) {
        if (busy || operationsBusy()) {
            message = "Finish the current OneOS operation first."; render(); return
        }
        val count = selected.size
        val payload = runCatching { if (clear) ByteArray(0) else GoogleAccountQueue.encode(selected) }.getOrElse {
            message = "The selected account list is invalid or too large."; render(); return
        }
        val expectedImage = intent.getStringExtra("image")
        val expectedCreated = intent.getStringExtra("created")
        busy = true
        message = "Checking the prepared guest…"
        render()
        val app = applicationContext
        Thread {
            val checked = runCatching { StageCheck.runBlocking(app, StageCheck.Mode.STAGE_ONLY) }
            runOnUiThread {
                if (isDestroyed || isFinishing) return@runOnUiThread
                busy = false
                val status = checked.getOrNull()?.status
                val marker = status?.marker
                val problem = when {
                    status == null || !status.complete -> "Could not check the guest. Verify root access and try again."
                    !status.ours || marker == null -> "No prepared guest found. Prepare a ROM in OneOS first, then return to import."
                    expectedImage != null && (marker.imageId != expectedImage || marker.createdEpoch.toString() != expectedCreated) ->
                        "The prepared guest changed. Reopen this screen from the main menu."
                    status.rootScriptSha != BuildConfig.ROOT_SCRIPT_SHA256 ->
                        "The prepared guest uses an older OneOS root script. Use OneOS's update/setup option to update the guest, then retry. Do not reset data just for this."
                    status.persistActive -> "Restore One UI before transferring accounts."
                    else -> null
                }
                if (problem != null || marker == null) {
                    message = problem ?: "No prepared guest found."
                    render()
                    return@runOnUiThread
                }
                val image = marker.imageId
                val created = marker.createdEpoch.toString()
                val name = Images.byId(image)?.nameWithVariant ?: image
                message = "Prepared guest: " + name
                render()
                AlertDialog.Builder(this)
                    .setTitle(if (clear) "Clear pending transfer?" else "Import account addresses?")
                    .setMessage(if (clear) "Clear the pending transfer for " + name + "? Existing signed-in accounts will not be changed."
                        else "Queue " + count + " selected accounts for " + name + " only. The accounts in One UI remain unchanged.")
                    .setNegativeButton("Cancel", null)
                    .setPositiveButton(if (clear) "Clear" else "Import") { _, _ -> queue(clear, image, created, payload) }
                    .show()
            }
        }.start()
    }

    private fun queue(clear: Boolean, image: String, created: String, payload: ByteArray) {
        if (busy || operationsBusy()) {
            message = "Finish the current OneOS operation first."; render(); return
        }
        busy = true
        message = "Preparing private account list…"
        render()
        val app = applicationContext
        Thread {
            val result = runCatching {
                RootVerbs.run(app, RootScript.Verb.GOOGLE_ACCOUNTS,
                    listOf(image, created, payload.size.toString()), RootShell.Feed { it.write(payload) })
            }
            runOnUiThread {
                if (isDestroyed || isFinishing) return@runOnUiThread
                busy = false
                message = if (result.getOrNull()?.ok == true) {
                    if (clear) "Pending transfer cleared." else "Ready. Open Import Google accounts in the guest after boot."
                } else "Could not queue accounts. " + (result.getOrNull()?.problem ?: "Check root and the prepared guest, then try again.")
                render()
            }
        }.start()
    }
}
