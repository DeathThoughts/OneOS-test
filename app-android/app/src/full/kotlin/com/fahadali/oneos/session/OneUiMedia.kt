package com.fahadali.oneos.session

import android.content.Context
import com.fahadali.oneos.core.root.OneUiMediaState
import com.fahadali.oneos.core.root.RootScript

/**
 * One UI photos in ColorOS (build 156). The owner turned the camera and screenshot sharing on by hand
 * from a root shell and did not want to: root `oneui-media on` makes the marker folder guest_start.sh
 * reads at every guest boot, `off` removes it, `status` reads it. Root's folder is the only truth, so
 * nothing is kept in the app's settings: Advanced asks root each time it opens.
 */
object OneUiMedia : BackgroundJob<OneUiMedia.State>(State(), "oneos-oneui-media") {
    data class State(
        val busy: Boolean = false,
        /** What root last said; null until it answered once. */
        val media: OneUiMediaState? = null,
        /** Why the last call changed or read nothing, as root said it. */
        val problem: String? = null,
        /** [problem] came from reading (status), not from turning it on or off. */
        val problemOnRead: Boolean = false,
    )

    fun refresh(ctx: Context) = run(ctx, "status")

    fun set(ctx: Context, on: Boolean) = run(ctx, if (on) "on" else "off")

    private fun run(ctx: Context, mode: String) {
        if (state.busy) return
        val app = ctx.applicationContext
        update(state.copy(busy = true, problem = null))
        executor.execute {
            val r = runCatching { RootVerbs.run(app, RootScript.Verb.ONEUI_MEDIA, listOf(mode), silenceMs = 60_000L, totalMs = 2 * 60_000L) }
            val reply = r.getOrNull()
            // A refusal comes before any change: what root said before stays true.
            val media = reply?.reply?.results?.let(OneUiMediaState::parse) ?: state.media
            val problem = if (reply?.ok == true) null else reply?.problem ?: r.exceptionOrNull()?.message ?: "no answer from root"
            update(State(busy = false, media = media, problem = problem, problemOnRead = mode == "status"))
        }
    }
}
