package com.fahadali.oneos.guest

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.AtomicFile
import android.util.Log
import com.fahadali.oneos.patch.GuestStartPatch
import com.fahadali.oneos.patch.PatchDownload
import java.io.File
import java.io.IOException
import java.util.concurrent.Executors

/** A patch reaches the app's private inbox only after verification, never by streaming to it. */
internal object GuestPatches {
    enum class Phase { IDLE, CHECKING, READY, STAGED, FAILED }

    private data class Target(val fields: List<String>, val automatic: Boolean, val floor: Long)
    private val targetFile = File("/data/oneos_bootui/signed_patch")
    private const val inbox = "oneos-patch.pending"
    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val network = PatchDownload()
    private var checkedAutomatically = false
    private var candidate: ByteArray? = null
    var phase = Phase.IDLE
        private set

    fun supported(): Boolean = BuildConfig.SIGNED_PATCH_PUBLIC_KEY.isNotEmpty() && targetFile.isFile

    private fun target(): Target {
        val lines = targetFile.inputStream().use { it.readNBytes(513) }
            .takeIf { it.size <= 512 }?.toString(Charsets.US_ASCII)?.trimEnd()?.split('\n')
            ?: throw IOException("invalid target size")
        if (lines.size != 2) throw IOException("invalid target")
        val flags = lines[1].split(' ')
        if (flags.size != 2 || flags[0] !in listOf("0", "1")) throw IOException("invalid target options")
        val floor = flags[1].toLongOrNull()?.takeIf { it >= 0 } ?: throw IOException("invalid sequence floor")
        return Target(lines[0].split(' '), flags[0] == "1", floor)
    }

    fun checkAutomatically(context: Context) {
        if (!supported() || checkedAutomatically || phase != Phase.IDLE) return
        checkedAutomatically = true
        val app = context.applicationContext
        io.execute {
            val auto = runCatching { target().automatic }.getOrDefault(false)
            if (auto) main.post {
                if (phase == Phase.IDLE) check(app, true)
            }
        }
    }

    fun check(context: Context, automatic: Boolean = false) {
        if (!supported() || phase == Phase.CHECKING) return
        phase = Phase.CHECKING
        candidate = null
        val app = context.applicationContext
        io.execute {
            try {
                val info = target()
                if (automatic && !info.automatic) {
                    main.post { phase = Phase.IDLE }
                    return@execute
                }
                val binding = GuestPatchTool.compatibility(info.fields.toTypedArray())
                val key = GuestPatchTool.key()
                val file = File(app.filesDir, inbox)
                val queued = runCatching {
                    AtomicFile(file).openRead().use { GuestStartPatch.verify(it, key, binding, info.floor) }
                }.isSuccess
                if (queued) {
                    main.post { phase = Phase.STAGED }
                    return@execute
                }
                val channel = if (BuildConfig.DEBUG) PatchDownload.Channel.DEBUG else PatchDownload.Channel.RELEASE
                val prefs = app.getSharedPreferences("guest-patch-transfer", Context.MODE_PRIVATE)
                val throttle = object : PatchDownload.Throttle {
                    override fun blockedUntil(channel: PatchDownload.Channel) = prefs.getLong(channel.name, 0)
                    override fun blockUntil(channel: PatchDownload.Channel, epochMillis: Long) {
                        if (!prefs.edit().putLong(channel.name, epochMillis).commit()) {
                            throw IOException("could not persist feed cooldown")
                        }
                    }
                }
                val bytes = network.download(channel, throttle, key, binding, info.floor)
                if (automatic) write(app, bytes)
                main.post {
                    candidate = if (automatic) null else bytes
                    phase = if (automatic) Phase.STAGED else Phase.READY
                }
            } catch (error: Exception) {
                // Only these fixed messages are safe to log. No exception trace, URL or response body.
                val message = error.message.orEmpty()
                val detail = when {
                    message == "patch feed is rate limited" -> "cooldown"
                    message.matches(Regex("patch feed returned HTTP [0-9]{3}")) -> message
                    else -> error.javaClass.simpleName
                }
                Log.w("OneOSGuestPatch", "Patch check failed ($detail)")
                main.post { phase = Phase.FAILED }
            }
        }
    }

    fun stage(context: Context) {
        val bytes = candidate ?: return
        phase = Phase.CHECKING
        val app = context.applicationContext
        io.execute {
            try {
                val info = target()
                GuestStartPatch.verify(bytes.inputStream(), GuestPatchTool.key(),
                    GuestPatchTool.compatibility(info.fields.toTypedArray()), info.floor)
                write(app, bytes)
                main.post {
                    candidate = null
                    phase = Phase.STAGED
                }
            } catch (error: Exception) {
                Log.w("OneOSGuestPatch", "Patch staging failed (${error.javaClass.simpleName})")
                main.post { phase = Phase.FAILED }
            }
        }
    }

    private fun write(context: Context, bytes: ByteArray) {
        val target = AtomicFile(File(context.filesDir, inbox))
        val output = target.startWrite()
        try {
            output.write(bytes)
            target.finishWrite(output)
        } catch (error: Exception) {
            target.failWrite(output)
            throw error
        }
    }
}
