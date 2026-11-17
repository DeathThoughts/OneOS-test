package com.fahadali.oneos.core.root

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.Base64

/** Build 156: the One UI photos switch reads root's oneui-media lines, and nothing else. */
class OneUiMediaStateTest {
    private fun reply(vararg lines: String) = RootReply().apply { lines.forEach { accept(it) } }.results

    @Test
    fun readsOnOffWhyAndTheLegacyMarker() {
        assertEquals(OneUiMediaState(false), OneUiMediaState.parse(reply("@@RESULT oneui-media off", "@@OK oneui-media status")))
        assertEquals(OneUiMediaState(true), OneUiMediaState.parse(reply("@@STEP share One UI's photos", "@@RESULT oneui-media on", "@@OK oneui-media on")))
        assertEquals(
            OneUiMediaState(false, "/data/local/tmp/oneos-oneui-media is a symlink", legacy = true),
            OneUiMediaState.parse(
                reply(
                    "@@RESULT oneui-media off",
                    "@@RESULT oneui-media-why /data/local/tmp/oneos-oneui-media is a symlink",
                    "@@RESULT oneui-media-legacy /data/local/tmp/oneos-media-share-test",
                    "@@OK oneui-media status",
                ),
            ),
        )
    }

    private fun b64(s: String) = Base64.getEncoder().encodeToString(s.toByteArray())

    /** The album picker (Galaxy S25 Ultra session 92032e7ae892154c): albums only with their closing count line; bad lines left out. */
    @Test
    fun readsTheAlbumsAndLeavesOutMalformedLines() {
        val st = OneUiMediaState.parse(
            reply(
                "@@RESULT oneui-media on",
                "@@RESULT oneui-media-album 0123456789abcdef 12 0 ${b64("DCIM/Camera")}",
                "@@RESULT oneui-media-album 89abcdef01234567 3 1 ${b64("Pictures/صور العائلة")}",
                "@@RESULT oneui-media-album 0011223344556677 1 0 ${b64("Movies")}",
                "@@RESULT oneui-media-album 0123456789ABCDEF 1 0 ${b64("Upper/Id")}",
                "@@RESULT oneui-media-album 1111111111111111 1 2 ${b64("Bad/Flag")}",
                "@@RESULT oneui-media-album 2222222222222222 1 0 not*base64",
                "@@RESULT oneui-media-album 3333333333333333 1 0 ${b64("new\nline")}",
                "@@RESULT oneui-media-album 4444444444444444 1 0 ${b64("/absolute")}",
                "@@RESULT oneui-media-album 0123456789abcdef 9 0 ${b64("DCIM/Camera")}",
                "@@RESULT oneui-media-albums 9 2",
                "@@OK oneui-media albums",
            ),
        )!!
        assertEquals(
            listOf(
                OneUiAlbum("0123456789abcdef", "DCIM/Camera", 12, false),
                OneUiAlbum("89abcdef01234567", "Pictures/صور العائلة", 3, true),
                OneUiAlbum("0011223344556677", "Movies", 1, false),
            ),
            st.albums,
        )
        assertEquals(2, st.albumsLeftOut)
        assertEquals("صور العائلة", st.albums!![1].leaf)
        assertEquals("Pictures", st.albums!![1].parent)
        assertEquals("", st.albums!![2].parent)
        // No count line: the list is not whole, so there is none; status never lists albums.
        assertNull(OneUiMediaState.parse(reply("@@RESULT oneui-media off", "@@RESULT oneui-media-album 0123456789abcdef 1 0 ${b64("A")}"))!!.albums)
        assertEquals(emptyList<OneUiAlbum>(), OneUiMediaState.parse(reply("@@RESULT oneui-media off", "@@RESULT oneui-media-albums 0 0"))!!.albums)
        assertEquals(
            "/data/local/tmp/oneos-oneui-media-excluded is a symlink",
            OneUiMediaState.parse(
                reply(
                    "@@RESULT oneui-media off",
                    "@@RESULT oneui-media-exclusions-why /data/local/tmp/oneos-oneui-media-excluded is a symlink",
                    "@@RESULT oneui-media-albums 0 0",
                ),
            )!!.exclusionsWhy,
        )
    }

    @Test
    fun aRefusalHasNoState() {
        assertNull(OneUiMediaState.parse(reply("@@REFUSED OneOS has no prepared stage on this phone: prepare one first")))
        assertNull(OneUiMediaState.parse(reply("@@RESULT tool_app com.example", "@@OK tool-app list")))
        assertNull(OneUiMediaState.parse(emptyList()))
    }
}
