package com.fahadali.oneos.guest

import android.Manifest
import android.accounts.AccountManager
import android.app.Activity
import android.content.res.Configuration
import android.content.res.ColorStateList
import android.graphics.Color
import android.view.WindowInsets
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PersistableBundle
import android.provider.Settings
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.fahadali.oneos.core.model.GoogleAccountQueue

/** A foreground queue, not a login service. Google owns all password/passkey/security UI. */
class GoogleAccountsActivity : Activity() {
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
    }

    private fun dialog(): AlertDialog.Builder = AlertDialog.Builder(this,
        if (dark) android.R.style.Theme_Material_Dialog_Alert else android.R.style.Theme_Material_Light_Dialog_Alert)
    private val handler = Handler(Looper.getMainLooper())
    private val attempted = linkedSetOf<String>()
    private var batch = false
    private var copyEach = false
    private var pausedOn: String? = null
    private var inFlight = false
    private var resumed = false
    private var startFromNotification = false
    private var message = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        // Keep the app's existing OneUITheme; replacing the Activity theme at
        // runtime can break OEM window initialization. Apply colors to views.
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        title = "Import Google accounts"
        // After a process recreation, keep the persisted queue but require an explicit resume.
        startFromNotification = savedInstanceState == null &&
            intent.getBooleanExtra(GoogleAccountNotifications.SIGN_IN_ALL, false)
        column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val pad = (20 * resources.displayMetrics.density).toInt()
            setPadding(pad, pad, pad, pad)
        }
        val scroll = ScrollView(this).apply {
            setBackgroundColor(this@GoogleAccountsActivity.background)
            isFillViewport = true
            addView(column)
        }
        setContentView(scroll)
        // Install the inset listener after the content and decor exist.
        scroll.setOnApplyWindowInsetsListener { v, insets ->
            val safe = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            v.setPadding(safe.left, safe.top, safe.right, safe.bottom)
            WindowInsets.CONSUMED
        }
        scroll.post { scroll.requestApplyInsets() }
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        reconcileVisibleAccounts()
        GoogleAccountNotifications.refresh(this)
        render()
    }

    override fun onPostResume() {
        super.onPostResume()
        if (startFromNotification) {
            startFromNotification = false
            beginBatch(false)
        } else scheduleNext()
    }

    override fun onPause() {
        resumed = false
        super.onPause()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    private fun visible(): Set<String> = runCatching {
        AccountManager.get(this).getAccountsByType(GoogleAccountQueue.TYPE).map { it.name }.toSet()
    }.getOrDefault(emptySet())

    private fun reconcileVisibleAccounts() {
        val existing = visible()
        GoogleAccountInbox.load(this).filter { it in existing }.forEach { GoogleAccountInbox.remove(this, it) }
    }

    private fun refresh() {
        GoogleAccountNotifications.refresh(this)
        if (!isDestroyed) render()
    }

    private fun button(label: String, enabled: Boolean = !inFlight, action: () -> Unit) {
        column.addView(Button(this).apply {
            text = label
            isAllCaps = false
            isEnabled = enabled
            setTextColor(textColors())
            backgroundTintList = ColorStateList.valueOf(if (dark) Color.rgb(49, 49, 49) else Color.rgb(226, 233, 245))
            minHeight = dp(48)
            setOnClickListener { action() }
        }, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(6); bottomMargin = dp(2) })
    }

    private fun render() {
        if (isDestroyed || !::column.isInitialized) return
        column.removeAllViews()
        val accounts = GoogleAccountInbox.load(this)
        column.addView(line("Import Google accounts", heading = true))
        column.addView(line("${accounts.size} Google accounts pending. Google handles sign-in, passkeys and security confirmations. If a login hint is ignored, use Copy email."))
        if (message.isNotEmpty()) column.addView(line(message))
        if (inFlight) column.addView(android.widget.ProgressBar(this).apply {
            isIndeterminate = true
            indeterminateTintList = ColorStateList.valueOf(accent)
        }, LinearLayout.LayoutParams(dp(28), dp(28)))
        if (accounts.isNotEmpty()) {
            button("Sign in all", !inFlight && !batch) { beginBatch(false) }
            button("Sign in all + copy each email", !inFlight && !batch) {
                dialog()
                    .setMessage("Copy each address to the clipboard immediately before opening Google's sign-in. Paste it if needed. No passwords are copied.")
                    .setNegativeButton("Cancel", null).setPositiveButton("Continue") { _, _ -> beginBatch(true) }.show()
            }
        }
        val paused = pausedOn
        if (batch && paused != null) {
            column.addView(line("Not confirmed: $paused"))
            button("Retry this account") {
                pausedOn = null
                login(paused)
            }
            button("Skip for now and continue") {
                pausedOn = null
                scheduleNext()
            }
        }
        if (batch) button("Stop sequence (keep pending accounts)", !inFlight) {
            batch = false; pausedOn = null; attempted.clear()
            message = "Sequence paused. Pending addresses are saved."
            refresh()
        }
        for (name in accounts) {
            column.addView(line(name).apply { textSize = 17f; setTypeface(typeface, android.graphics.Typeface.BOLD) })
            button("Sign in with Google", !inFlight && !batch) { login(name) }
            button("Copy email and open Google sign-in", !inFlight && !batch) { copy(name); login(name) }
            button("Copy email", !inFlight) { copy(name); render() }
            button("Open Android add-account screen", !inFlight && !batch) {
                copy(name)
                openAccountSettings(true)
            }
            button("Already signed in / remove from queue", !inFlight && !batch) {
                dialog()
                    .setMessage("Remove this address from the pending list? This does not change the Google account itself.")
                    .setNegativeButton("Cancel", null).setPositiveButton("Remove") { _, _ ->
                        GoogleAccountInbox.remove(this, name); refresh()
                    }.show()
            }
        }
        if (accounts.isNotEmpty()) {
            button("Enable account reminders", !inFlight) {
                GoogleAccountNotifications.enable(this)
                if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 702)
                } else if (!GoogleAccountNotifications.available(this)) {
                    startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName))
                }
                refresh()
            }
            button("Clear pending accounts", !inFlight && !batch) {
                dialog().setMessage("Delete all pending addresses? Signed-in accounts are not removed.")
                    .setNegativeButton("Cancel", null).setPositiveButton("Clear") { _, _ -> GoogleAccountInbox.clear(this); refresh() }.show()
            }
        }
        button("Manage existing accounts", !inFlight && !batch) { openAccountSettings(false) }
        button("Back", !inFlight) { finish() }
    }

    @Deprecated("Permission result")
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 702) refresh()
    }

    private fun openAccountSettings(add: Boolean) {
        val target = if (add) Intent(Settings.ACTION_ADD_ACCOUNT)
            .putExtra(Settings.EXTRA_ACCOUNT_TYPES, arrayOf(GoogleAccountQueue.TYPE)) else Intent(Settings.ACTION_SYNC_SETTINGS)
        runCatching { startActivity(target) }.recoverCatching { startActivity(Intent(Settings.ACTION_SETTINGS)) }
            .onFailure { message = "Account settings unavailable."; render() }
    }

    private fun copy(name: String) {
        val clip = ClipData.newPlainText("Google account", name)
        clip.description.extras = PersistableBundle().apply { putBoolean("android.content.extra.IS_SENSITIVE", true) }
        getSystemService(ClipboardManager::class.java)?.setPrimaryClip(clip)
        message = "Email copied. Paste it into Google's sign-in if needed."
    }

    private fun beginBatch(copyBeforeLogin: Boolean) {
        if (inFlight || batch) return
        attempted.clear()
        batch = true
        copyEach = copyBeforeLogin
        pausedOn = null
        scheduleNext()
    }

    private fun scheduleNext() {
        if (!batch || inFlight || pausedOn != null || !resumed || isFinishing || isDestroyed) return
        handler.post {
            if (!batch || inFlight || pausedOn != null || !resumed || isFinishing || isDestroyed) return@post
            val pending = GoogleAccountInbox.load(this)
            val next = GoogleAccountQueue.next(pending, attempted)
            if (next == null) {
                batch = false
                message = if (pending.isEmpty()) "All queued accounts are complete." else "${pending.size} accounts still pending. You can continue later."
                refresh()
            } else {
                attempted.add(next)
                if (copyEach) copy(next)
                login(next)
            }
        }
    }

    private fun finishAttempt(name: String, confirmed: Boolean) {
        inFlight = false
        if (isDestroyed || isFinishing) return
        val complete = confirmed || name in visible()
        if (complete) {
            GoogleAccountInbox.remove(this, name)
            message = "Account added."
        } else {
            message = "Sign-in was not confirmed. This account remains pending."
            if (batch) pausedOn = name
        }
        refresh()
        if (complete) scheduleNext()
    }

    private fun login(name: String) {
        if (inFlight || !resumed || isFinishing || isDestroyed) return
        if (name in visible()) {
            finishAttempt(name, true)
            return
        }
        val manager = AccountManager.get(this)
        val available = runCatching { manager.authenticatorTypes.any { it.type == GoogleAccountQueue.TYPE } }.getOrDefault(false)
        if (!available) {
            // Pause once rather than trying every account on a guest without GMS.
            batch = false; pausedOn = null
            message = "Google authentication is not available in this guest."
            refresh()
            return
        }
        inFlight = true
        render()
        val options = Bundle().apply {
            putString(AccountManager.KEY_ACCOUNT_NAME, name)
            putString("login_hint", name)
        }
        try {
            manager.addAccount(GoogleAccountQueue.TYPE, null, null, options, this, { future ->
                val confirmed = runCatching {
                    val result = future.result
                    GoogleAccountQueue.confirmed(name, result.getString(AccountManager.KEY_ACCOUNT_NAME),
                        result.getString(AccountManager.KEY_ACCOUNT_TYPE))
                }.getOrDefault(false)
                // Persist an explicit result even if the activity was recreated while signing in.
                if (confirmed) {
                    GoogleAccountInbox.remove(applicationContext, name)
                    GoogleAccountNotifications.refresh(applicationContext)
                }
                finishAttempt(name, confirmed)
            }, handler)
        } catch (_: Exception) {
            finishAttempt(name, false)
        }
    }
}
