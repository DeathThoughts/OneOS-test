package com.fahadali.oneos.core.root

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

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

    @Test
    fun aRefusalHasNoState() {
        assertNull(OneUiMediaState.parse(reply("@@REFUSED OneOS has no prepared stage on this phone: prepare one first")))
        assertNull(OneUiMediaState.parse(reply("@@RESULT tool_app com.example", "@@OK tool-app list")))
        assertNull(OneUiMediaState.parse(emptyList()))
    }
}
