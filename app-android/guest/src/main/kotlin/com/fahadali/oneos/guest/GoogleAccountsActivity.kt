package com.fahadali.oneos.guest

import android.accounts.AccountManager
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.os.Bundle
import android.os.PersistableBundle
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.fahadali.oneos.core.model.GoogleAccountQueue

/** Explicit Google authenticator UI only. OneOS supplies a login hint, never credentials. */
class GoogleAccountsActivity : Activity() {
    private lateinit var column: LinearLayout
    private var busy = false
    private var message = ""
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        title = "Import Google accounts"
        column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val pad = (20 * resources.displayMetrics.density).toInt()
            setPadding(pad, pad, pad, pad)
        }
        setContentView(ScrollView(this).apply { addView(column) })
    }
    override fun onResume() { super.onResume(); render() }

    private fun button(label: String, action: () -> Unit) {
        column.addView(Button(this).apply { text = label; isEnabled = !busy; setOnClickListener { action() } })
    }

    private fun render() {
        column.removeAllViews()
        val accounts = GoogleAccountInbox.load(this)
        column.addView(TextView(this).apply {
            text = "Google handles sign-in. Use a passkey or approval if offered. If the email hint is not accepted, copy and paste the address."
        })
        if (accounts.isEmpty()) column.addView(TextView(this).apply { text = "No pending accounts. Select them in One UI before booting this guest." })
        for (name in accounts) {
            column.addView(TextView(this).apply { text = name; textSize = 17f })
            button("Sign in with Google") { login(name) }
            button("Copy email") { copy(name) }
            button("Remove from list") { GoogleAccountInbox.remove(this, name); render() }
        }
        if (accounts.isNotEmpty()) button("Clear pending accounts") {
            AlertDialog.Builder(this).setMessage("Delete all pending addresses? Signed-in accounts are not removed.")
                .setNegativeButton("Cancel", null).setPositiveButton("Clear") { _, _ -> GoogleAccountInbox.clear(this); render() }.show()
        }
        button("Back") { finish() }
        if (message.isNotEmpty()) column.addView(TextView(this).apply { text = message })
    }

    private fun copy(name: String) {
        val clip = ClipData.newPlainText("Google account", name)
        clip.description.extras = PersistableBundle().apply { putBoolean("android.content.extra.IS_SENSITIVE", true) }
        getSystemService(ClipboardManager::class.java)?.setPrimaryClip(clip)
        message = "Email copied. Paste it into Google's sign-in screen if necessary."
        render()
    }

    private fun login(name: String) {
        if (busy) return
        val manager = AccountManager.get(this)
        if (runCatching { manager.getAccountsByType(GoogleAccountQueue.TYPE).any { it.name == name } }.getOrDefault(false)) {
            GoogleAccountInbox.remove(this, name)
            message = "This account is already visible in the guest."
            render(); return
        }
        if (manager.authenticatorTypes.none { it.type == GoogleAccountQueue.TYPE }) {
            message = "Google Play services authentication is not available in this guest."
            render(); return
        }
        busy = true
        render()
        val options = Bundle().apply {
            putString(AccountManager.KEY_ACCOUNT_NAME, name)
            putString("login_hint", name)
        }
        try {
            manager.addAccount(GoogleAccountQueue.TYPE, null, null, options, this, { future ->
                // Read only the returned account identity. Never ask for an auth token.
                val match = runCatching {
                    val result = future.result
                    result.getString(AccountManager.KEY_ACCOUNT_NAME) == name &&
                        result.getString(AccountManager.KEY_ACCOUNT_TYPE) == GoogleAccountQueue.TYPE
                }.getOrDefault(false)
                if (match) GoogleAccountInbox.remove(applicationContext, name)
                if (!isDestroyed) {
                    busy = false
                    message = if (match) "Account added." else "Sign-in not confirmed for this address. It remains in the list."
                    render()
                }
            }, null)
        } catch (_: Exception) {
            busy = false
            message = "Could not open Google's sign-in screen."
            render()
        }
    }
}
