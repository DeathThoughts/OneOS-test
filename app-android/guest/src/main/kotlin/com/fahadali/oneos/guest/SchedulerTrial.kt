package com.fahadali.oneos.guest

import android.content.Context
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

/** The debug guest sends one named trial to the existing session watcher. */
internal object SchedulerTrial {
    private const val REQUEST = "oneos_scheduler_trial"
    private val stateFile = File("/data/oneos_bootui/scheduler_trial")
    private val tokenPattern = Regex("[0-9a-f-]{36}")

    enum class Target(val id: String, val label: Int) {
        RENDER("render", R.string.scheduler_trial_render),
        MAIN("main", R.string.scheduler_trial_main),
        ENGINE("unirender-engine", R.string.scheduler_trial_engine),
        LAUNCHER_MAIN("launcher-main", R.string.scheduler_trial_launcher_main),
        LAUNCHER_WORKER0("launcher-worker0", R.string.scheduler_trial_launcher_worker0),
        LAUNCHER_WORKER1("launcher-worker1", R.string.scheduler_trial_launcher_worker1),
    }

    enum class Phase { IDLE, STARTING, RUNNING, RESTORED, FAILED }

    data class State(val token: String, val phase: Phase, val target: Target?) {
        val busy: Boolean get() = phase == Phase.STARTING || phase == Phase.RUNNING
    }

    fun current(): State? {
        if (!BuildConfig.DEBUG) return null
        val parts = runCatching {
            stateFile.takeIf { it.isFile && it.length() in 1..80 }?.readText()?.trim()?.split(' ')
        }.getOrNull() ?: return null
        if (parts.size !in 2..3 || !tokenPattern.matches(parts[0])) return null
        val phase = Phase.entries.firstOrNull { it.name.equals(parts[1], ignoreCase = true) } ?: return null
        val target = Target.entries.firstOrNull { it.id == parts.getOrNull(2) }
        if (phase != Phase.IDLE && target == null) return null
        return State(parts[0], phase, target)
    }

    fun pending(context: Context, token: String): Boolean = runCatching {
        File(context.filesDir, REQUEST).takeIf { it.isFile && it.length() in 1..80 }
            ?.readText()?.startsWith("$token ") == true
    }.getOrDefault(false)

    @Throws(IOException::class)
    fun request(context: Context, target: Target) {
        val state = current() ?: throw IOException("scheduler trials are unavailable")
        if (state.busy || pending(context, state.token) || AnimLevel.current() != AnimLevel.FULL) {
            throw IOException("enable full effects and wait for the current trial to finish")
        }
        val tmp = File.createTempFile("scheduler-", ".tmp", context.filesDir)
        try {
            FileOutputStream(tmp).use { out ->
                out.write("${state.token} ${target.id}".toByteArray(Charsets.US_ASCII))
                out.fd.sync()
            }
            if (!tmp.renameTo(File(context.filesDir, REQUEST))) throw IOException("could not submit the trial")
        } finally {
            tmp.delete()
        }
    }
}
