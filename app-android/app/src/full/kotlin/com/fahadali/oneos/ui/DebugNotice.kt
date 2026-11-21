package com.fahadali.oneos.ui

import android.content.Context
import android.view.View
import com.fahadali.oneos.BuildConfig
import com.fahadali.oneos.R

/** An inline, dismissible disclosure for this debug build. */
internal object DebugNotice {
    fun bind(root: View) {
        if (!BuildConfig.DEBUG) return
        val banner = root.findViewById<View>(R.id.debug_notice)
        val prefs = root.context.getSharedPreferences("debug_notice", Context.MODE_PRIVATE)
        if (prefs.getInt("dismissed_version", -1) == BuildConfig.VERSION_CODE) {
            banner.visibility = View.GONE
            return
        }

        banner.visibility = View.VISIBLE
        banner.findViewById<View>(R.id.debug_notice_dismiss).setOnClickListener {
            prefs.edit().putInt("dismissed_version", BuildConfig.VERSION_CODE).apply()
            banner.visibility = View.GONE
        }
    }
}
