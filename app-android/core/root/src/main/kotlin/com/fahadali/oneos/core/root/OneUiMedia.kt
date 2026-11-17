package com.fahadali.oneos.core.root

import java.util.Base64

/**
 * What `oneui-media` answered (build 156, the One UI photos switch): the root script's
 * `@@RESULT` lines, read the same way after status, on, off, albums and exclude.
 */
data class OneUiMediaState(
    /** The marker is a real root:root 0700 folder: the next guest boot shares the photos. */
    val on: Boolean,
    /** Something is at the marker's path that is not the marker (a link, a file, another owner or mode), as root said it. */
    val why: String? = null,
    /** The S23 prototype's test-only marker is still there; off removes it too. */
    val legacy: Boolean = false,
    /**
     * One UI's albums, after `albums` or `exclude` (null after the other modes, which do not list
     * them). Every album the owner did not untick is shared, also one made later in One UI
     * (the owner's choice of albums, Galaxy S25 Ultra session 92032e7ae892154c).
     */
    val albums: List<OneUiAlbum>? = null,
    /** Albums root left out because their line would not fit a result line (very long paths). */
    val albumsLeftOut: Int = 0,
    /**
     * The list of unticked albums is there but is not root's own 0600 file (a link, say), as root said
     * it: guest_start.sh then shares no album at all.
     */
    val exclusionsWhy: String? = null,
) {
    companion object {
        private val ALBUM = Regex("^oneui-media-album ([0-9a-f]{16}) ([0-9]{1,9}) ([01]) ([A-Za-z0-9+/]+={0,2})$")
        private val ALBUMS = Regex("^oneui-media-albums ([0-9]{1,9}) ([0-9]{1,9})$")

        /** Null when the reply had no on/off line (a refusal or a failure before it). */
        fun parse(results: List<String>): OneUiMediaState? {
            val on = when {
                "oneui-media on" in results -> true
                "oneui-media off" in results -> false
                else -> return null
            }
            val why = results.firstOrNull { it.startsWith("oneui-media-why ") }?.removePrefix("oneui-media-why ")?.trim()?.takeIf { it.isNotEmpty() }
            val xWhy = results.firstOrNull { it.startsWith("oneui-media-exclusions-why ") }
                ?.removePrefix("oneui-media-exclusions-why ")?.trim()?.takeIf { it.isNotEmpty() }
            // The count line closes the list: without it the list is not whole, so there is none.
            val total = results.firstNotNullOfOrNull { ALBUMS.matchEntire(it) }
            val albums = total?.let { results.mapNotNull(OneUiAlbum::parse).distinctBy { it.id } }
            return OneUiMediaState(
                on, why, results.any { it.startsWith("oneui-media-legacy ") },
                albums = albums,
                albumsLeftOut = total?.groupValues?.get(2)?.toIntOrNull() ?: 0,
                exclusionsWhy = xWhy,
            )
        }

        internal fun albumMatch(line: String) = ALBUM.matchEntire(line)
    }
}

/** One album of One UI's media/0, as `oneui-media albums` listed it. */
data class OneUiAlbum(
    /** The first 16 hex digits of the SHA-256 of [path]: what `oneui-media exclude` takes. */
    val id: String,
    /** Relative to One UI's internal storage, e.g. `DCIM/Camera`. */
    val path: String,
    /** Media files directly in it. */
    val files: Int,
    /** The owner unticked it: ColorOS does not show it. */
    val excluded: Boolean,
) {
    /** The album's own folder name. */
    val leaf: String get() = path.substringAfterLast('/')

    /** The folders above it ("" for one directly in the storage root). */
    val parent: String get() = if ('/' in path) path.substringBeforeLast('/') else ""

    companion object {
        /** Null for anything but a whole, well-formed album line. */
        fun parse(line: String): OneUiAlbum? {
            val m = OneUiMediaState.albumMatch(line) ?: return null
            val bytes = runCatching { Base64.getDecoder().decode(m.groupValues[4]) }.getOrNull() ?: return null
            val path = String(bytes, Charsets.UTF_8)
            if (path.isEmpty() || path.any { it.isISOControl() } || path.startsWith("/") || path.endsWith("/")) return null
            val files = m.groupValues[2].toIntOrNull() ?: return null
            return OneUiAlbum(m.groupValues[1], path, files, m.groupValues[3] == "1")
        }
    }
}
