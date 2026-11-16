package com.fahadali.oneos.core.root

/**
 * What `oneui-media` answered (build 156, Advanced's One UI photos switch): the root script's
 * `@@RESULT` lines, read the same way after status, on and off.
 */
data class OneUiMediaState(
    /** The marker is a real root:root 0700 folder: the next guest boot shares the photos. */
    val on: Boolean,
    /** Something is at the marker's path that is not the marker (a link, a file, another owner or mode), as root said it. */
    val why: String? = null,
    /** The S23 prototype's test-only marker is still there; off removes it too. */
    val legacy: Boolean = false,
) {
    companion object {
        /** Null when the reply had no on/off line (a refusal or a failure before it). */
        fun parse(results: List<String>): OneUiMediaState? {
            val on = when {
                "oneui-media on" in results -> true
                "oneui-media off" in results -> false
                else -> return null
            }
            val why = results.firstOrNull { it.startsWith("oneui-media-why ") }?.removePrefix("oneui-media-why ")?.trim()?.takeIf { it.isNotEmpty() }
            return OneUiMediaState(on, why, results.any { it.startsWith("oneui-media-legacy ") })
        }
    }
}
