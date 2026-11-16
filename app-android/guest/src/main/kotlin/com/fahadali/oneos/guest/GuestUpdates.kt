package com.fahadali.oneos.guest

import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import com.fahadali.oneos.core.model.AppUpdate
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URI
import java.security.MessageDigest
import java.util.concurrent.Executors

/** Queues a same-signer guest app update for the existing guest restart. */
object GuestUpdates {
    enum class Phase { IDLE, CHECKING, CURRENT, AVAILABLE, DOWNLOADING, PENDING, FAILED }
    data class State(
        val phase: Phase = Phase.IDLE,
        val update: AppUpdate? = null,
        val percent: Int = 0,
        val pendingCode: Int? = null,
        val error: String? = null,
    ) {
        val busy get() = phase == Phase.CHECKING || phase == Phase.DOWNLOADING
    }

    @Volatile var state = State()
        private set
    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private const val LIMIT = 64L * 1024 * 1024

    fun supported() = File("/data/oneos_bootui/manager_updates").isFile
    fun rootActive() = File("/data/adb/oneos/run/su.sock").exists()

    private fun run(initial: State, done: (State) -> Unit, action: () -> State) {
        if (state.busy) return
        state = initial
        done(initial)
        io.execute {
            val next = runCatching(action).getOrElse { State(Phase.FAILED, error = it.message) }
            state = next
            main.post { done(next) }
        }
    }

    fun refresh(done: (State) -> Unit) = run(State(Phase.CHECKING), done) {
        val status = GuestRuntime.control("manager-update-status").trim()
        when {
            status.startsWith("pending ") -> State(Phase.PENDING, pendingCode = status.lineSequence().first().substringAfter(' ').toInt(),
                error = status.substringAfter('\n', "").ifBlank { null })
            status.startsWith("failed") -> State(Phase.FAILED, error = status.substringAfter('\n', status))
            else -> State()
        }
    }

    fun check(done: (State) -> Unit) = run(State(Phase.CHECKING), done) {
        val c = connection(AppUpdate.manifestUrl(BuildConfig.DEBUG, guest = true))
        val json = try { c.inputStream.use { it.readNBytes(4097).toString(Charsets.UTF_8) } }
                   finally { c.disconnect() }
        val update = AppUpdate.parse(json, guest = true, debug = BuildConfig.DEBUG).getOrThrow()
        require(update.bytes <= LIMIT) { "Guest update exceeds 64 MiB" }
        State(if (update.newerThan(BuildConfig.VERSION_CODE)) Phase.AVAILABLE else Phase.CURRENT, update)
    }

    fun download(context: Context, done: (State) -> Unit) {
        val update = state.update ?: return
        val app = context.applicationContext
        run(State(Phase.DOWNLOADING, update), done) {
            val directory = File(app.filesDir, "oneos-update")
            check(directory.isDirectory || directory.mkdirs()) { "Could not create update directory" }
            val part = File(directory, "OneOSGuest.part")
            val apk = File(directory, "OneOSGuest.apk")
            try {
                val c = connection(update.apk)
                try {
                    val digest = MessageDigest.getInstance("SHA-256")
                    var count = 0L
                    c.inputStream.use { input -> FileOutputStream(part).use { output ->
                        val buffer = ByteArray(65536)
                        while (true) {
                            val size = input.read(buffer)
                            if (size < 0) break
                            count += size
                            require(count <= update.bytes) { "Download exceeds the declared size" }
                            output.write(buffer, 0, size)
                            digest.update(buffer, 0, size)
                            val percent = (count * 100 / update.bytes).toInt()
                            if (percent != state.percent) {
                                state = State(Phase.DOWNLOADING, update, percent)
                                val progress = state
                                main.post { done(progress) }
                            }
                        }
                        output.fd.sync()
                    } }
                    require(count == update.bytes) { "Incomplete guest update" }
                    val hash = digest.digest().joinToString("") { "%02x".format(it) }
                    require(hash == update.sha256) { "Guest update checksum differs" }
                } finally { c.disconnect() }
                check(part.renameTo(apk)) { "Could not keep the downloaded update" }
                verifyApk(app, apk, update)
                GuestRuntime.control("manager-update-stage", apk.path, update.bytes.toString(),
                    update.sha256, update.versionCode.toString())
                State(Phase.PENDING, update, 100, update.versionCode)
            } finally {
                part.delete()
                apk.delete()
            }
        }
    }

    fun cancel(done: (State) -> Unit) = run(State(Phase.CHECKING), done) {
        GuestRuntime.control("manager-update-cancel")
        State()
    }

    private fun verifyApk(context: Context, apk: File, update: AppUpdate) {
        val pm = context.packageManager
        val flags = PackageManager.GET_SIGNING_CERTIFICATES
        val installed = pm.getPackageInfo(context.packageName, flags)
        val candidate = pm.getPackageArchiveInfo(apk.path, flags) ?: error("Invalid guest APK")
        require(candidate.packageName == context.packageName) { "Unexpected guest package" }
        require(candidate.longVersionCode == update.versionCode.toLong() &&
            candidate.longVersionCode > installed.longVersionCode) { "Unexpected guest update version" }
        val expected = installed.signingInfo?.apkContentsSigners?.toSet().orEmpty()
        val actual = candidate.signingInfo?.apkContentsSigners?.toSet().orEmpty()
        require(expected.isNotEmpty() && actual == expected) { "Guest update uses another signing key" }
    }

    private fun connection(url: String): HttpURLConnection {
        var next = URI(url)
        repeat(6) {
            require(next.scheme == "https" && next.userInfo == null) { "Update requires HTTPS" }
            val c = next.toURL().openConnection() as HttpURLConnection
            c.connectTimeout = 15000
            c.readTimeout = 30000
            c.instanceFollowRedirects = false
            try {
                if (c.responseCode in 300..399) {
                    next = next.resolve(c.getHeaderField("Location") ?: error("Invalid update redirect"))
                    c.disconnect()
                } else {
                    check(c.responseCode == 200) { "Update server returned HTTP ${c.responseCode}" }
                    return c
                }
            } catch (error: Exception) {
                c.disconnect()
                throw error
            }
        }
        error("Too many update redirects")
    }
}
