package com.fahadali.oneos.session

import android.content.Context
import com.fahadali.oneos.core.root.OneUiAlbum
import com.fahadali.oneos.core.root.OneUiMediaState
import com.fahadali.oneos.core.root.RootScript

/**
 * One UI photos in ColorOS (build 156). The owner turned the camera and screenshot sharing on by hand
 * from a root shell and did not want to: root `oneui-media on` makes the marker folder guest_start.sh
 * reads at every guest boot, `off` removes it, `status` reads it. Root's folder is the only truth, so
 * nothing is kept in the app's settings: Settings asks root each time it opens.
 *
 * The album picker (owner's decision after the Galaxy S25 Ultra, session 92032e7ae892154c: every One
 * UI album, each its own in ColorOS's Gallery, unticked ones left out): `albums` lists them, `exclude`
 * saves the unticked ones in root's own list, also only on the phone.
 */
object OneUiMedia : BackgroundJob<OneUiMedia.State>(State(), "oneos-oneui-media") {
    data class State(
        val busy: Boolean = false,
        /** What root last said; null until it answered once. */
        val media: OneUiMediaState? = null,
        /** Why the last call changed or read nothing, as root said it. */
        val problem: String? = null,
        /** [problem] came from reading (status or albums), not from a change. */
        val problemOnRead: Boolean = false,
        /** [problem] came from the album picker (albums or exclude), not from the switch. */
        val problemOnAlbums: Boolean = false,
        /** One UI's albums as root last listed them; null until it listed them once. */
        val albums: List<OneUiAlbum>? = null,
        /** Albums root could not list (paths too long for a result line): they stay shown. */
        val albumsLeftOut: Int = 0,
        /** Why guest_start.sh would share no album at all (its list of unticked albums is not root's own file). */
        val exclusionsWhy: String? = null,
        /** Counts up each time a tap on "Choose albums" got the list: the screen opens the picker once per count. */
        val pick: Int = 0,
    )

    fun refresh(ctx: Context) = run(ctx, listOf("status"))

    fun set(ctx: Context, on: Boolean) = run(ctx, listOf(if (on) "on" else "off"))

    /** Lists the albums, then the screen shows the picker. */
    fun choose(ctx: Context) = run(ctx, listOf("albums"))

    /** Saves [excluded] (album ids) as the albums ColorOS does not show; none = every album shown. */
    fun saveExcluded(ctx: Context, excluded: List<String>) = run(ctx, listOf("exclude") + excluded.distinct())

    private fun run(ctx: Context, args: List<String>) {
        if (state.busy) return
        val app = ctx.applicationContext
        val mode = args.first()
        val picker = mode == "albums" || mode == "exclude"
        update(state.copy(busy = true, problem = null))
        executor.execute {
            val r = runCatching { RootVerbs.run(app, RootScript.Verb.ONEUI_MEDIA, args, silenceMs = 60_000L, totalMs = 2 * 60_000L) }
            val reply = r.getOrNull()
            // A refusal comes before any change: what root said before stays true.
            val parsed = reply?.reply?.results?.let(OneUiMediaState::parse)
            val media = parsed ?: state.media
            val problem = if (reply?.ok == true) null else reply?.problem ?: r.exceptionOrNull()?.message ?: "no answer from root"
            // Only albums and exclude list the albums; status, on and off keep the last list.
            val listed = parsed?.albums
            update(
                state.copy(
                    busy = false, media = media, problem = problem,
                    problemOnRead = mode == "status" || mode == "albums", problemOnAlbums = picker,
                    albums = listed ?: state.albums,
                    albumsLeftOut = if (listed != null) parsed?.albumsLeftOut ?: 0 else state.albumsLeftOut,
                    exclusionsWhy = if (listed != null) parsed?.exclusionsWhy else state.exclusionsWhy,
                    pick = if (mode == "albums" && listed != null && problem == null) state.pick + 1 else state.pick,
                ),
            )
        }
    }
}
