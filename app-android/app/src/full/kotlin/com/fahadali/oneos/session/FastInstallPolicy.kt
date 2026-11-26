package com.fahadali.oneos.session

/** Pure policies, separate from Android services for regression testing. */
internal object FastInstallPolicy {
    fun parallelism(files: Int, metered: Boolean, thermal: Int): Int {
        require(files > 0)
        val limit = when {
            thermal >= 3 -> 2
            thermal >= 2 -> 3
            metered -> 4
            else -> 8
        }
        return minOf(files, limit)
    }

    /** Matches root file_mode; root still independently validates the real file. */
    fun mode(name: String): String =
        if (name.endsWith(".sh") || name in setOf("guest_root", "guest_zygiskd", "guest_zygisk_ptrace",
                "zygote_exec", "oneos_btaudio", "oneos_essential_key", "surfaceflinger_patched", "vold_patched")) "755" else "644"

    fun validResume(header: String?, offset: Long, size: Long): Boolean {
        if (offset <= 0 || offset >= size) return false
        return header?.trim()?.equals("bytes $offset-${size - 1}/$size", ignoreCase = true) == true
    }
}
