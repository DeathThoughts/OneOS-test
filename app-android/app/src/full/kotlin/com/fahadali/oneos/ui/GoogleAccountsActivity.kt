package com.fahadali.oneos.ui

import android.accounts.AccountManager
import android.app.Activity
import android.content.res.Configuration
import android.content.res.ColorStateList
import android.graphics.Color
import android.view.WindowInsets
import android.view.WindowInsetsController
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
    private val dark: Boolean get() = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
    private val foreground: Int get() = if (dark) Color.rgb(242, 242, 242) else Color.rgb(25, 25, 25)
    private val background: Int get() = if (dark) Color.rgb(18, 18, 18) else Color.rgb(250, 250, 250)
    private val accent: Int get() = if (dark) Color.rgb(150, 192, 255) else Color.rgb(25, 76, 170)
    private fun dp(n: Int): Int = (n * resources.displayMetrics.density + 0.5f).toInt()

    private fun textColors(): ColorStateList = ColorStateList(
        arrayOf(intArrayOf(-android.R.attr.state_enabled), intArrayOf()),
        intArrayOf(if (dark) Color.rgb(173, 173, 173) else Color.rgb(95, 95, 95), foreground)
    )
    private fun line(value: String, heading: Boolean = false): TextView = TextView(this).apply {
        text = value
        setTextColor(this@GoogleAccountsActivity.foreground)
        textSize = if (heading) 23f else 16f
        if (heading) setTypeface(typeface, android.graphics.Typeface.BOLD)
        setPadding(0, dp(8), 0, dp(8))
        isTextSelectable = false
    }

    private fun dialog(): AlertDialog.Builder = AlertDialog.Builder(this,
        if (dark) android.R.style.Theme_Material_Dialog_Alert else android.R.style.Theme_Material_Light_Dialog_Alert)
    private var message = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        setTheme(if (dark) android.R.style.Theme_Material_NoActionBar else android.R.style.Theme_Material_Light_NoActionBar)
        super.onCreate(savedInstanceState)
        window.setDecorFitsSystemWindows(false)
        window.insetsController?.setSystemBarsAppearance(
            if (dark) 0 else WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
            WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS
        )
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
        val scroll = ScrollView(this).apply {
            setBackgroundColor(this@GoogleAccountsActivity.background)
            isFillViewport = true
            addView(column)
            setOnApplyWindowInsetsListener { v, insets ->
                val safe = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
                v.setPadding(safe.left, safe.top, safe.right, safe.bottom)
                insets
            }
        }
        setContentView(scroll)
        scroll.requestApplyInsets()
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
        column.addView(Button(this).apply {
            text = label
            isAllCaps = false
            isEnabled = !busy
            setTextColor(textColors())
            backgroundTintList = ColorStateList.valueOf(if (dark) Color.rgb(49, 49, 49) else Color.rgb(226, 233, 245))
            minHeight = dp(48)
            setOnClickListener { action() }
        }, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(6); bottomMargin = dp(2) })
    }

    private fun render() {
        column.removeAllViews()
        column.addView(line("Import Google accounts", heading = true))
        column.addView(line("Select the accounts to use in the guest. Google handles authentication; no passwords or tokens are copied."))
        if (message.isNotEmpty()) column.addView(line(message))
        if (busy) column.addView(android.widget.ProgressBar(this).apply {
            isIndeterminate = true
            indeterminateTintList = ColorStateList.valueOf(accent)
        }, LinearLayout.LayoutParams(dp(28), dp(28)))
        val count = line("")
        fun updateCount() {
            count.text = if (names.isEmpty()) "No accounts are visible yet. Use the Android account picker below to grant visibility."
                else "${selected.size} selected / ${names.size} available. Tap any account to select or deselect it."
        }
        updateCount()
        column.addView(count)
        if (names.isNotEmpty()) {
            button("Select all accounts") { selected.addAll(names); render() }
            button("Deselect all accounts") { selected.clear(); render() }
        }
        for (name in names.sorted()) {
            column.addView(CheckBox(this).apply {
                text = name
                setTextColor(textColors())
                buttonTintList = ColorStateList.valueOf(accent)
                minHeight = dp(48)
                setPadding(dp(4), dp(4), dp(4), dp(4))
                isChecked = name in selected
                isEnabled = !busy
                setOnCheckedChangeListener { _, checked -> if (checked) selected.add(name) else selected.remove(name); updateCount() }
            })
        }
        button("Refresh visible accounts") {
            val newlyVisible = visible().filter { it !in names }
            names.addAll(newlyVisible)
            selected.addAll(newlyVisible)
            render()
        }
        button(if (names.isEmpty()) "Choose Google account in Android" else "Choose additional account in Android") {
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
        column.addView(line("The Android picker selects one account at a time. You can select all visible accounts together here. A prepared, updated guest is required to transfer."))
        button("Back") { finish() }

    }

    @Deprecated("Android account picker result")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 701 && resultCode == RESULT_OK &&
            data?.getStringExtra(AccountManager.KEY_ACCOUNT_TYPE) == GoogleAccountQueue.TYPE) {
            val name = data?.getStringExtra(AccountManager.KEY_ACCOUNT_NAME)
            if (name != null && GoogleAccountQueue.valid(name)) { names.add(name); selected.add(name) }
            val newlyVisible = visible().filter { it !in names }
            names.addAll(newlyVisible)
            selected.addAll(newlyVisible)
            message = if (names.isEmpty()) "No account was made visible." else "Select all accounts you want to transfer."
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
                dialog()
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
