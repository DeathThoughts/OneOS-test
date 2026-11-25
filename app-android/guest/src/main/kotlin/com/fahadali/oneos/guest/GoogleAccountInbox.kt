package com.fahadali.oneos.guest

import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.net.Uri
import android.os.Binder
import android.os.Bundle
import android.os.ParcelFileDescriptor
import android.util.AtomicFile
import com.fahadali.oneos.core.model.GoogleAccountQueue
import com.fahadali.oneos.core.model.Hashes
import java.io.File
import java.io.FileNotFoundException
import java.io.InputStream
import java.io.ByteArrayOutputStream

private fun InputStream.readAccountQueue(): ByteArray {
    val out = ByteArrayOutputStream()
    val buf = ByteArray(4096)
    while (true) {
        val n = read(buf)
        if (n < 0) break
        require(out.size() + n <= GoogleAccountQueue.MAX_BYTES)
        out.write(buf, 0, n)
    }
    return out.toByteArray()
}

/** The inbox is local and app-private, excluded from backup. Only root in this guest can submit hints. */
internal object GoogleAccountInbox {
    private fun file(ctx: Context) = AtomicFile(File(ctx.noBackupFilesDir, "google-account-queue"))
    @Synchronized fun load(ctx: Context): List<String> = runCatching {
        file(ctx).openRead().use { GoogleAccountQueue.decode(it.readAccountQueue()) }
    }.getOrDefault(emptyList())
    @Synchronized fun store(ctx: Context, names: Collection<String>) {
        val data = GoogleAccountQueue.encode(names)
        val f = file(ctx)
        val out = f.startWrite()
        try { out.write(data); f.finishWrite(out) }
        catch (e: Exception) { f.failWrite(out); throw e }
    }
    @Synchronized fun merge(ctx: Context, names: List<String>) = store(ctx, load(ctx) + names)
    @Synchronized fun remove(ctx: Context, name: String) = store(ctx, load(ctx).filterNot { it == name })
    @Synchronized fun clear(ctx: Context) = file(ctx).delete()
}

/** Write-only bridge. No query, read, delete, token handling or external network. */
class GoogleAccountInboxProvider : ContentProvider() {
    override fun onCreate(): Boolean = true
    private fun requireRoot() { if (Binder.getCallingUid() != 0) throw SecurityException("Root only") }
    private fun incoming() = File(requireNotNull(context).cacheDir, "google-account-transfer.part")

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        requireRoot()
        if (uri.path != "/queue" || mode != "w") throw FileNotFoundException("Write-only queue")
        val f = incoming()
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_CREATE or
            ParcelFileDescriptor.MODE_TRUNCATE or ParcelFileDescriptor.MODE_WRITE_ONLY)
    }

    override fun call(method: String, arg: String?, extras: Bundle?): Bundle {
        requireRoot()
        val f = incoming()
        val ok = if (method != "commit" || arg == null || !Regex("[0-9a-f]{64}").matches(arg)) false else runCatching {
            val bytes = f.inputStream().use { it.readAccountQueue() }
            require(Hashes.sha256(bytes) == arg)
            val names = GoogleAccountQueue.decode(bytes)
            GoogleAccountInbox.merge(requireNotNull(context), names)
            true
        }.getOrDefault(false)
        if (method == "commit") f.delete()
        return Bundle().apply { putBoolean("ok", ok) }
    }

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, selectionArgs: Array<out String>?, sortOrder: String?): Cursor? = null
    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = throw SecurityException("Write-only queue")
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = throw SecurityException("Write-only queue")
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = throw SecurityException("Write-only queue")
}
