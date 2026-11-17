package com.fahadali.oneos.guest

import android.os.Bundle
import android.widget.LinearLayout
import android.widget.ScrollView
import androidx.appcompat.app.AppCompatActivity
import com.fahadali.oneos.shared.NativeCards

class GuestUpdatesActivity : AppCompatActivity() {
    private lateinit var list: LinearLayout
    private var rendered: GuestUpdates.State? = null
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())
    private val progress = object : Runnable {
        override fun run() {
            render(GuestUpdates.state)
            handler.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.guest_updates_title)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val padding = (20 * resources.displayMetrics.density).toInt()
        list.setPadding(padding, padding, padding, padding)
        setContentView(ScrollView(this).apply {
            fitsSystemWindows = true
            addView(list)
        })
        render(GuestUpdates.state)
    }

    override fun onResume() {
        super.onResume()
        handler.post(progress)
        if (GuestUpdates.supported() && GuestUpdates.rootActive() && !GuestUpdates.state.busy) GuestUpdates.refresh(::render)
    }

    override fun onPause() {
        handler.removeCallbacks(progress)
        super.onPause()
    }

    override fun onSupportNavigateUp(): Boolean { finish(); return true }

    private fun render(state: GuestUpdates.State) {
        if (isDestroyed || rendered == state) return
        rendered = state
        list.removeAllViews()
        val card = NativeCards.section(list, getString(R.string.guest_updates_title), getColor(R.color.preboot_card))
        NativeCards.row(card, getString(R.string.guest_updates_installed, BuildConfig.VERSION_NAME),
            getString(R.string.guest_updates_scope))
        if (!GuestUpdates.supported()) {
            NativeCards.row(card, getString(R.string.guest_updates_bootstrap), getString(R.string.guest_updates_bootstrap_detail))
            return
        }
        if (!GuestUpdates.rootActive()) {
            NativeCards.row(card, getString(R.string.guest_updates_root), "")
            return
        }
        val status = when (state.phase) {
            GuestUpdates.Phase.CHECKING -> getString(R.string.guest_updates_checking)
            GuestUpdates.Phase.DOWNLOADING -> getString(R.string.guest_updates_downloading, state.percent)
            GuestUpdates.Phase.PENDING -> getString(R.string.guest_updates_pending, state.pendingCode ?: 0)
            GuestUpdates.Phase.CURRENT -> getString(R.string.guest_updates_current)
            GuestUpdates.Phase.AVAILABLE -> getString(R.string.guest_updates_available, state.update?.versionName.orEmpty())
            GuestUpdates.Phase.FAILED -> getString(R.string.guest_updates_failed, state.error.orEmpty())
            else -> getString(R.string.guest_updates_check)
        }
        NativeCards.row(card, status, "")
        if (state.phase == GuestUpdates.Phase.PENDING && state.error != null) {
            NativeCards.row(card, getString(R.string.guest_updates_failed, state.error), "")
        }
        when (state.phase) {
            GuestUpdates.Phase.PENDING -> {
                NativeCards.row(card, getString(R.string.guest_updates_restart), getString(R.string.guest_updates_restart_detail)) {
                    GuestRestart.confirm(this)
                }
                NativeCards.row(card, getString(R.string.guest_updates_cancel), "") { GuestUpdates.cancel(::render) }
            }
            GuestUpdates.Phase.AVAILABLE -> NativeCards.row(card, getString(R.string.guest_updates_download), "") {
                GuestUpdates.download(this, ::render)
            }
            GuestUpdates.Phase.FAILED -> {
                NativeCards.row(card, getString(R.string.guest_updates_retry), "") { GuestUpdates.check(::render) }
                NativeCards.row(card, getString(R.string.guest_updates_cancel), "") { GuestUpdates.cancel(::render) }
            }
            else -> NativeCards.row(card, getString(R.string.guest_updates_check), "") {
                GuestUpdates.check(::render)
            }.isEnabled = !state.busy
        }
    }
}
