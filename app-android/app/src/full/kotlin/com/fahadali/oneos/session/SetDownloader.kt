package com.fahadali.oneos.session

import android.content.Context
import com.fahadali.oneos.core.model.Hashes
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** Downloads this fork’s pinned patch payloads by SHA-256. GSI downloads use a separate path. */
object SetDownloader {
    open class Failed(message: String) : IOException(message)

    /** The server answered a page instead of the file: Drive's daily download limit for that copy (0.4.53). */
    class Refused(message: String) : Failed(message)

    /** The repository whose releases hold the set files as assets named by their SHA-256 (0.4.53). */
    const val FILES_REPO = "DeathThoughts/OneOS-test"

    /**
     * The pinned release asset for [source]. The fork's own payloads use `gh:DeathThoughts/OneOS-test:<tag>`;
     * pins added after fork-payloads-v1 may still name their original `gh:owner/repo:<tag>` release until the
     * next migration, so the hash, size and release tag remain the gate. GSI images never use this path.
     */
    fun url(source: String, sha256: String): String {
        require(Regex("[0-9a-f]{64}").matches(sha256))
        require(source.startsWith("gh:")) { "Patch payload source is not a GitHub release" }
        val parts = source.removePrefix("gh:").split(':')
        val repo = if (parts.size == 2) parts[0] else FILES_REPO
        val tag = parts.last()
        require(Regex("[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*").matches(repo) &&
            Regex("[a-z0-9][a-z0-9.-]{0,39}").matches(tag)) { "Patch payload source is not a pinned release" }
        return "https://github.com/$repo/releases/download/$tag/$sha256"
    }

    fun dir(ctx: Context): File = File(ctx.applicationContext.filesDir, "oneos-files").apply { mkdirs() }

    /** The downloaded file when it is complete (size checked; its hash was checked when it was kept). */
    fun cached(ctx: Context, f: AppAssets.Staged): File? =
        File(dir(ctx), f.sha256).takeIf { it.isFile && it.length() == f.bytes }

    /**
     * Only recognized GitHub Release entries are usable. Some migrated package lists
     * still contain legacy Drive/SourceForge identifiers after a valid gh: entry;
     * rejecting the entire list would prevent downloading an otherwise valid set.
     * Keep the strict URL validation for every source actually requested.
     */
    internal fun supportedSources(sources: List<String>, sha256: String): List<String> =
        sources.distinct().filter { source ->
            // A migrated fork entry must name its repository explicitly.
            // Short "gh:sets-1" entries are leftovers from the upstream feed.
            source.count { it == ':' } == 2 && runCatching { url(source, sha256) }.isSuccess
        }

    /** Resumes an interrupted download, retaining a file only after the pinned size and hash match. */
    fun fetch(
        ctx: Context, f: AppAssets.Staged, sources: List<String>, cancelled: () -> Boolean,
        connections: MutableSet<HttpURLConnection>? = null, progress: (Long) -> Unit,
    ) {
        if (cached(ctx, f) != null) { progress(f.bytes); return }
        if (sources.isEmpty()) throw Failed("${f.name} has no download source")
        val part = File(dir(ctx), "${f.sha256}.part")
        if (part.length() > f.bytes) part.delete()
        val order = supportedSources(sources, f.sha256)
        if (order.isEmpty()) throw Failed("${f.name} has no supported GitHub Release source")
        // Legacy entries are ignored; every requested URL still passes url()'s strict validation.
        var last: Exception? = null
        var refused = 0
        for (source in order) {
            for (attempt in 1..3) {
                if (cancelled()) throw Failed("stopped")
                try {
                    download(part, url(source, f.sha256), f.bytes, f.name, cancelled, connections, progress)
                    verify(part, f.bytes, f.sha256, f.name)
                    if (!part.renameTo(File(dir(ctx), f.sha256))) throw Failed("could not keep ${f.name}")
                    return
                } catch (e: Refused) {
                    refused++
                    last = e
                    break
                } catch (e: Failed) {
                    throw e
                } catch (e: IOException) {
                    if (cancelled()) throw Failed("stopped")
                    last = e
                    try { Thread.sleep(2_000L * attempt) } catch (ie: InterruptedException) { throw Failed("stopped") }
                }
            }
        }
        if (refused == order.size) {
            throw Failed("the fork release is not handing out ${f.name} right now: try again later")
        }
        throw Failed("${f.name}: ${last?.message ?: "download failed"}")
    }

    /**
     * Downloads [from] into [part], appending to what [part] already holds when the
     * server takes a Range; [bytes] is the size the file must have. Also the app's
     * own update (AppUpdater).
     */
    internal fun download(
        part: File, from: String, bytes: Long, name: String, cancelled: () -> Boolean,
        connections: MutableSet<HttpURLConnection>? = null, progress: (Long) -> Unit,
    ) {
        val have = part.length()
        if (have == bytes) return
        val c = URL(from).openConnection() as HttpURLConnection
        c.connectTimeout = 30_000
        // Drive took up to 80 s before the first byte of some files (0.4.41 on the Fold, and from the server).
        c.readTimeout = 150_000
        c.instanceFollowRedirects = true
        if (have > 0) c.setRequestProperty("Range", "bytes=$have-")
        // The caller can close it from another thread (a stop): a blocked read then ends at once.
        connections?.add(c)
        try {
            val code = c.responseCode
            val type = c.contentType.orEmpty()
            // Drive answers an HTML page instead of the file when it refuses (quota, a removed file).
            if (type.startsWith("text/html")) throw Refused("the server did not hand out $name (HTTP $code): try again later")
            // Not there, or refused for now (GitHub's 404 for a missing asset, a rate limit): the next source.
            if (code == HttpURLConnection.HTTP_NOT_FOUND || code == HttpURLConnection.HTTP_FORBIDDEN || code == 429) {
                throw Refused("the server did not hand out $name (HTTP $code)")
            }
            val append = have > 0 && code == HttpURLConnection.HTTP_PARTIAL
            if (code != HttpURLConnection.HTTP_OK && !append) throw IOException("HTTP $code")
            // A stop while Drive was still answering: never touch the file after it (0.4.42 review:
            // a late answer truncated the .part a newer run was writing).
            if (cancelled()) throw Failed("stopped")
            var done = if (append) have else 0L
            FileOutputStream(part, append).use { out ->
                c.inputStream.use { input ->
                    val buf = ByteArray(1 shl 16)
                    while (true) {
                        if (cancelled()) throw Failed("stopped")
                        val n = input.read(buf)
                        // Checked again after a read that may have blocked for a long time.
                        if (cancelled()) throw Failed("stopped")
                        if (n < 0) break
                        if (done + n > bytes) throw Failed("$name is larger than the app expects")
                        out.write(buf, 0, n)
                        done += n
                        progress(done)
                    }
                }
            }
            if (done != bytes) throw IOException("$name: $done of $bytes bytes")
        } finally {
            connections?.remove(c)
            c.disconnect()
        }
    }

    /** Deletes [part] and fails unless it is [bytes] long with SHA-256 [sha256]. */
    internal fun verify(part: File, bytes: Long, sha256: String, name: String) {
        if (part.length() != bytes) {
            part.delete()
            throw Failed("$name arrived with ${part.length()} of $bytes bytes")
        }
        val md = MessageDigest.getInstance("SHA-256")
        RandomAccessFile(part, "r").use { r ->
            val buf = ByteArray(1 shl 16)
            while (true) {
                val n = r.read(buf)
                if (n < 0) break
                md.update(buf, 0, n)
            }
        }
        if (Hashes.hex(md.digest()) != sha256) {
            part.delete()
            throw Failed("$name arrived damaged (its hash is not the app's)")
        }
    }

    /** Removes downloaded files no list in this app names (an older set's), and stray parts. */
    fun prune(ctx: Context, keep: Set<String>) {
        dir(ctx).listFiles()?.forEach { file ->
            val sha = file.name.removeSuffix(".part")
            if (sha !in keep) file.delete()
        }
    }
}
