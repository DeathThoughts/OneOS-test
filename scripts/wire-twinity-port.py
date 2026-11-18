#!/usr/bin/env python3
"""Wire the built Twinity ColorOS 17 China (TWNTY 4.0c) port into OneOS for base Galaxy S23.

Reads `out/twinity-metadata/twinity-4c-manifest.json` produced by `scripts/build-twinity-port.py`
and idempotently updates:
  - `SingleImageZipInputStream.kt`, `SingleImageZipInputStreamTest.kt`, `PrepareJob.kt` (ZIP-import support)
  - `Images.kt`, `PackageSets.kt`, `Profiles.kt`, `StageCheck.kt` (experimental S_8550-only option)
  - `ImageActivity.kt`, `SystemsActivity.kt`, `strings_advanced.xml` (EN + pt-rBR catalog & join instructions)
  - `app-android/packages/{twinity-4c-s23.sha256,twinity-4c-s23.guards.txt,sizes.txt,remote.txt,series.txt}`
  - `app-android/core/model/src/test/resources/packages/{twinity-4c-s23.sha256,twinity-4c-s23.guards.txt}`
  - `app-android/app/src/full/assets/root/oneos_root.sh`
  - `switcher/assets/oneos_switch.sh` & `scripts/test-restart-home.py` (base S23 record_restart_home fix)
  - `app-android/app/build.gradle.kts` & `app-android/guest/build.gradle.kts`
  - Unit tests (`ApkSeriesTest.kt`, `ImagesTest.kt`, `PackageSetsTest.kt`, `ProfilesTest.kt`, `StageScriptTest.kt`, `SessionEnvTest.kt`)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

IMAGE_ID = "coloros17-twinity-4c"
IMAGE_CONST = "COLOROS_TWINITY_4C"
IMAGE_ID_CONST = "ID_COLOROS_TWINITY_4C"
SET_ID = "twinity-4c-s23"
SET_CONST = "TWINITY_4C_S23"
SET_ID_CONST = "ID_TWINITY_4C_S23"
SOURCE_DIR_REL = "out/overlays_twinity_4c_r1"
VERSION_CODE = 170
VERSION_NAME = "0.5.19-twinity4c.1-teste"


SINGLE_IMAGE_ZIP_KT = """package com.fahadali.oneos.core.model

import java.io.IOException
import java.io.InputStream
import java.util.zip.ZipException
import java.util.zip.ZipInputStream

/** Streams one exact image entry. The caller verifies the raw image and the entire archive. */
class SingleImageZipInputStream(
    source: InputStream,
    private val entryName: String,
    rawBytes: Long,
) : InputStream() {
    private val zip = ZipInputStream(source)
    private val one = ByteArray(1)
    private var complete = false
    private var failure: IOException? = null

    init {
        try {
            val entry = zip.nextEntry
            if (entry == null || entry.isDirectory || entry.name != entryName) {
                throw ZipException("corrupt ZIP archive: expected $entryName as its only entry")
            }
            if (entry.size != -1L && entry.size != rawBytes) {
                throw ZipException("corrupt ZIP archive: $entryName declares ${entry.size} bytes, expected $rawBytes")
            }
        } catch (error: Throwable) {
            runCatching { zip.close() }.exceptionOrNull()?.let(error::addSuppressed)
            throw error
        }
    }

    override fun read(): Int = if (read(one, 0, 1) < 0) -1 else one[0].toInt() and 0xff

    override fun read(b: ByteArray, off: Int, len: Int): Int {
        if (off < 0 || len < 0 || off > b.size - len) throw IndexOutOfBoundsException()
        if (len == 0) return 0
        failure?.let { throw it }
        if (complete) return -1

        try {
            val n = zip.read(b, off, len)
            if (n < 0) {
                // ZipInputStream verifies the entry's CRC before reporting EOF.
                zip.closeEntry()
                if (zip.nextEntry != null) throw ZipException("corrupt ZIP archive: expected only $entryName")
                complete = true
            }
            return n
        } catch (error: IOException) {
            failure = error
            throw error
        }
    }

    override fun close() = zip.close()
}
"""

SINGLE_IMAGE_ZIP_TEST_KT = """package com.fahadali.oneos.core.model

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.DigestInputStream
import java.security.MessageDigest
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipException
import java.util.zip.ZipOutputStream
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class SingleImageZipInputStreamTest {
    private val name = "system.img"
    private val image = ByteArray(4096) { it.toByte() }

    private fun archive(vararg entries: Pair<String, ByteArray>, stored: Boolean = false): ByteArray {
        val buffer = ByteArrayOutputStream()
        ZipOutputStream(buffer).use { zip ->
            for ((entryName, bytes) in entries) {
                val entry = ZipEntry(entryName)
                if (stored) {
                    entry.method = ZipEntry.STORED
                    entry.size = bytes.size.toLong()
                    entry.crc = CRC32().apply { update(bytes) }.value
                }
                zip.putNextEntry(entry)
                zip.write(bytes)
                zip.closeEntry()
            }
        }
        return buffer.toByteArray()
    }

    private fun open(archive: ByteArray, entryName: String = name, rawBytes: Long = image.size.toLong()) =
        SingleImageZipInputStream(ByteArrayInputStream(archive), entryName, rawBytes)

    private fun payloadOffset(archive: ByteArray): Int {
        val header = ByteBuffer.wrap(archive).order(ByteOrder.LITTLE_ENDIAN)
        val filenameLength = header.getShort(26).toInt() and 0xffff
        val extraLength = header.getShort(28).toInt() and 0xffff
        return 30 + filenameLength + extraLength
    }

    @Test fun streamsTheExactImageAndKeepsEof() {
        open(archive(name to image)).use { input ->
            assertEquals(image.first().toInt() and 0xff, input.read())
            assertArrayEquals(image.copyOfRange(1, image.size), input.readBytes())
            assertEquals(-1, input.read())
            assertEquals(-1, input.read(ByteArray(8)))
            assertEquals(0, input.read(ByteArray(0)))
        }
    }

    @Test fun checksKnownEntrySizeAtOpen() {
        val archive = archive(name to image, stored = true)
        open(archive).use { assertArrayEquals(image, it.readBytes()) }
        assertThrows(ZipException::class.java) { open(archive, rawBytes = image.size.toLong() + 1) }
    }

    @Test fun rejectsWrongNamesAndPaths() {
        for (wrong in listOf("vendor.img", "subdir/$name", "../$name", "./$name")) {
            assertThrows(ZipException::class.java) { open(archive(wrong to image)) }
        }
    }

    @Test fun rejectsEmptyArchiveAndDirectoryFirst() {
        assertThrows(ZipException::class.java) { open(archive()) }
        assertThrows(ZipException::class.java) { open(archive("$name/" to byteArrayOf()), "$name/") }
        assertThrows(ZipException::class.java) { open(archive("folder/" to byteArrayOf(), name to image)) }
    }

    @Test fun rejectsAnySecondEntryAndNeverStreamsIt() {
        for (extra in listOf("extra.img" to image, "folder/" to byteArrayOf())) {
            open(archive(name to image, extra)).use { input ->
                assertThrows(ZipException::class.java) { input.readBytes() }
                assertThrows(ZipException::class.java) { input.read() }
            }
        }
    }

    @Test fun rejectsCorruptEntryCrc() {
        val archive = archive(name to image, stored = true)
        val position = payloadOffset(archive) + image.size / 2
        archive[position] = (archive[position].toInt() xor 1).toByte()
        open(archive).use { input ->
            val failure = assertThrows(ZipException::class.java) { input.readBytes() }
            assertTrue(failure.message.orEmpty().contains("CRC", ignoreCase = true))
        }
    }

    @Test fun rejectsTruncatedEntry() {
        val valid = archive(name to image)
        val truncated = valid.copyOf(payloadOffset(valid) + 1)
        open(truncated).use { input ->
            assertThrows(IOException::class.java) { input.readBytes() }
        }
    }

    @Test fun drainingSourceAfterImageEofHashesEntireArchive() {
        val archive = archive(name to image) + ByteArray(4096) { 42 }
        val rawSource = ByteArrayInputStream(archive)
        val digest = MessageDigest.getInstance("SHA-256")
        val source = DigestInputStream(rawSource, digest)
        SingleImageZipInputStream(source, name, image.size.toLong()).use { input ->
            assertArrayEquals(image, input.readBytes())
            val unread = rawSource.available()
            assertTrue(unread > 0)
            assertEquals(unread, source.readBytes().size)
            assertEquals(0, rawSource.available())
            assertArrayEquals(MessageDigest.getInstance("SHA-256").digest(archive), digest.digest())
        }
    }
}
"""

RESTART_HOME_SHELL_FUNC = r"""record_restart_home() {
    case \"\$GEN\" in ''|0|*[!0-9]*) return ;; esac
    ns=\$(cat $STAGE/guest_mntns 2>/dev/null)
    server_ns=\$(readlink /proc/\$PID/ns/mnt 2>/dev/null)
    host_ns=\$(readlink /proc/1/ns/mnt 2>/dev/null)
    guest_root=\$(stat -Lc '%d:%i' $STAGE/rootfs 2>/dev/null)
    # Android can put system_server in a descendant mount namespace. Require
    # the exact staged guest root and exclude the host's namespace either way.
    same_restart() {
        [ \"\$GEN\" = \"\$(cat $STAGE/tmp/softreboot.gen 2>/dev/null)\" ] &&
            [ \"\$ns\" = \"\$(cat $STAGE/guest_mntns 2>/dev/null)\" ] &&
            [ \"\$server_ns\" = \"\$(readlink /proc/\$PID/ns/mnt 2>/dev/null)\" ] &&
            [ \"\$guest_root\" = \"\$(stat -Lc '%d:%i' /proc/\$PID/root 2>/dev/null)\" ]
    }
    if [ -z \"\$ns\" ] || [ -z \"\$server_ns\" ] || [ -z \"\$host_ns\" ] ||
       [ \"\$server_ns\" = \"\$host_ns\" ] || [ \"\$ns\" = \"\$host_ns\" ] ||
       [ -z \"\$guest_root\" ] || ! same_restart; then
        echo \"[UI Activator] Restart Home generation \$GEN: guest system_server identity could not be confirmed\"
        return
    fi
    # ColorOS on the base S23 (OneOS build 162) missed Home twice with a
    # short sample after UI setup. Observe through the keyguard and launcher
    # startup, and retry resolution while the guest's package service warms up.
    read -r started _ < /proc/uptime || return
    deadline=\$((\${started%%.*} + 120))
    home_component=''
    while read -r now _ < /proc/uptime && [ \"\${now%%.*}\" -lt \"\$deadline\" ]; do
        same_restart || return
        if [ -z \"\$home_component\" ]; then
            home_component=\$(timeout 5 $BB nsenter -t \$PID -m /system/bin/sh /data/oneos_genv.sh /system/bin/cmd package resolve-activity --user 0 --brief -a android.intent.action.MAIN -c android.intent.category.HOME 2>/dev/null | tail -n 1)
            case \"\$home_component\" in
                *.*/*) ;;
                *) home_component=''; sleep 2; continue ;;
            esac
        fi
        if timeout 5 $BB nsenter -t \$PID -m /system/bin/sh /data/oneos_genv.sh /system/bin/dumpsys activity activities 2>/dev/null |
            awk -v component=\"\$home_component\" '
                function expand(value, parts) {
                    split(value, parts, \"/\")
                    if (parts[2] ~ /^[.]/) return parts[1] \"/\" parts[1] parts[2]
                    return value
                }
                /^[[:space:]]*(topResumedActivity|mResumedActivity|ResumedActivity)[[:space:]]*[=:]/ {
                    for (i = 2; i <= NF; i++)
                        if (\$(i - 1) == \"u0\" && expand(\$i) == expand(component)) found = 1
                }
                END { exit !found }
            ' && same_restart; then
            record=\$(mktemp $STAGE/tmp/restart-home.XXXXXX) || return
            if printf '%s %s\n' \"\$GEN\" \"\$ns\" > \"\$record\" &&
                mv -f \"\$record\" $STAGE/tmp/restart-home.\$GEN; then
                echo \"[UI Activator] Home resumed for restart generation \$GEN\"
            else
                rm -f \"\$record\"
                echo \"[UI Activator] Restart Home generation \$GEN: could not save acknowledgement\"
            fi
            return
        fi
        sleep 2
    done
    if [ -n \"\$home_component\" ]; then
        echo \"[UI Activator] Restart Home generation \$GEN: resumed Home not observed for \$home_component\"
    else
        echo \"[UI Activator] Restart Home generation \$GEN: no resolved Home component\"
    fi
}"""

TEST_RESTART_HOME_PY = '''"""Test the Home acknowledgement using the generated activator's shell code."""
import pathlib
import subprocess
import tempfile

source = pathlib.Path("switcher/assets/oneos_switch.sh").read_text()
start = source.index("record_restart_home() {")
end = source.index("\\nGEN=", start)
template = source[start:end]
mock = r"""
readlink() {
    case "$1" in
        /proc/1/ns/mnt) printf '%s\\n' 'mnt:[1]' ;;
        *) printf '%s\\n' "$TEST_NS" ;;
    esac
}
stat() {
    case "$*" in
        */rootfs) printf '%s\\n' '12:33' ;;
        *) printf '%s\\n' "$TEST_ROOT" ;;
    esac
}
timeout() {
    case "$*" in
        *resolve-activity*)
            if [ "$elapsed" -ge "$TEST_RESOLVE_AFTER" ]; then
                printf '%s\\n' 'com.example.launcher/.Home'
            fi
            ;;
        *dumpsys*)
            if [ "$elapsed" -ge "$TEST_RESUME_AFTER" ]; then
                printf '%s\\n' "topResumedActivity=ActivityRecord{1 $TEST_USER $TEST_COMPONENT t2}"
            else
                printf '%s\\n' 'topResumedActivity=ActivityRecord{1 u0 com.example.setup/.Finishing t2}'
            fi
            ;;
    esac
}
elapsed=0
read() {
    case "$2" in
        started) started=1000 ;;
        now) now="$((1000 + elapsed)).0" ;;
        *) return 1 ;;
    esac
}
sleep() {
    elapsed=$((elapsed + $1))
    if [ "$elapsed" -eq "$TEST_CHANGE_AFTER" ]; then
        printf '9\\n' > "$STATE/softreboot.gen"
    fi
}
"""
with tempfile.TemporaryDirectory() as directory:
    stage = pathlib.Path(directory)
    state = stage / "tmp"
    state.mkdir()
    (stage / "guest_mntns").write_text("mnt:[45]\\n")
    (state / "softreboot.gen").write_text("8\\n")
    script = subprocess.check_output(
        ["sh", "-c", f'STAGE=$1; BB=busybox; printf "%s" "{template}"', "sh", directory],
        text=True,
    )
    for name, generation, namespace, root, component, user, resolve_after, resume_after, change_after, expected in (
        ("late Home", 8, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u0", 0, 18, -1, True),
        ("package service warming up", 8, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u0", 4, 0, -1, True),
        ("stale generation", 7, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u0", 0, 0, -1, False),
        ("host namespace", 8, "mnt:[1]", "12:33", "com.example.launcher/.Home", "u0", 0, 0, -1, False),
        ("wrong root", 8, "mnt:[45]", "12:99", "com.example.launcher/.Home", "u0", 0, 0, -1, False),
        ("wrong activity", 8, "mnt:[45]", "12:33", "com.example.launcher/.HomeOther", "u0", 0, 0, -1, False),
        ("wrong user", 8, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u10", 0, 0, -1, False),
        ("new restart while waiting", 8, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u0", 0, 18, 6, False),
        ("Home after deadline", 8, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u0", 0, 122, -1, False),
        ("descendant namespace", 8, "mnt:[46]", "12:33", "com.example.launcher/com.example.launcher.Home", "u0", 0, 0, -1, True),
    ):
        (state / "softreboot.gen").write_text("8\\n")
        subprocess.run(
            ["sh", "-c", mock + script + "\\nGEN=$1; TEST_NS=$2; TEST_ROOT=$3; TEST_COMPONENT=$4; TEST_USER=$5; "
             "TEST_RESOLVE_AFTER=$6; TEST_RESUME_AFTER=$7; TEST_CHANGE_AFTER=$8; STATE=$9; PID=123\\nrecord_restart_home || :",
             "sh", str(generation), namespace, root, component, user, str(resolve_after), str(resume_after),
             str(change_after), str(state)],
            check=True,
            timeout=10,
        )
        record = state / f"restart-home.{generation}"
        assert record.exists() == expected, name
        if expected:
            assert record.read_text() == "8 mnt:[45]\\n"
            record.unlink()
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text and old not in text:
        return text
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{label}: expected 1 occurrence of anchor {old[:80]!r}, found {count}")
    return text.replace(old, new, 1)


def format_kotlin_long(value: int) -> str:
    s = f"{value:,}".replace(",", "_")
    return f"{s}L"


def classify_file_role(name: str, guarded: set[str]) -> str:
    if name in guarded:
        if name.endswith(".jar"):
            return "FileRole.GUARD_JAR"
        if name.endswith(".so"):
            return "FileRole.GUARD_LIBRARY"
        return "FileRole.GUARD_EXECUTABLE"
    if name == "guards.txt":
        return "FileRole.GUARDS"
    if name == "surfaceflinger_patched":
        return "FileRole.SURFACEFLINGER"
    if name == "OneOSGuest.apk":
        return "FileRole.GUEST_APP"
    if name == "oneos-telephony.jar":
        return "FileRole.INJECTION_JAR"
    if name == "oneos-telephony.sha256":
        return "FileRole.INJECTION_SHA256"
    if name.endswith("_payload.img"):
        return "FileRole.APEX_PAYLOAD"
    if name.endswith("_patched.jar"):
        return "FileRole.FRAMEWORK_JAR"
    if name.startswith("OneOS") and name.endswith(".apk"):
        return "FileRole.RRO"
    return "FileRole.GUEST_FIX"


def wire_zip_support(repo_root: Path) -> None:
    zip_kt = repo_root / "app-android/core/model/src/main/kotlin/com/fahadali/oneos/core/model/SingleImageZipInputStream.kt"
    zip_test_kt = repo_root / "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/SingleImageZipInputStreamTest.kt"
    zip_kt.write_text(SINGLE_IMAGE_ZIP_KT, encoding="utf-8")
    zip_test_kt.write_text(SINGLE_IMAGE_ZIP_TEST_KT, encoding="utf-8")

    prep_path = repo_root / "app-android/app/src/full/kotlin/com/fahadali/oneos/session/PrepareJob.kt"
    prep = prep_path.read_text(encoding="utf-8")
    if "SingleImageZipInputStream" not in prep:
        prep = replace_once(
            prep,
            "import com.fahadali.oneos.core.model.SessionEnv\n",
            "import com.fahadali.oneos.core.model.SessionEnv\nimport com.fahadali.oneos.core.model.SingleImageZipInputStream\n",
            "PrepareJob import",
        )
        old_block = """                val gz = GZIPInputStream(counting, 1 shl 16)
                val buf = ByteArray(1 shl 18)
                val head = ByteArray(2)
                while (true) {
                    if (cancelled) throw IOException("stopped")
                    val n = gz.read(buf)
                    if (n < 0) break
                    // ext4 superblock magic 0xEF53, little endian at byte 1080.
                    for (k in 0 until n) {
                        val at = rawBytes + k
                        if (at == Images.EXT4_MAGIC_OFFSET) head[0] = buf[k]
                        if (at == Images.EXT4_MAGIC_OFFSET + 1) {
                            head[1] = buf[k]
                            val magic = (head[0].toInt() and 0xff) or ((head[1].toInt() and 0xff) shl 8)
                            if (magic != Images.EXT4_MAGIC) throw IOException("not an ext4 image")
                        }
                        if (at > Images.EXT4_MAGIC_OFFSET + 1) break
                    }
                    rawMd.update(buf, 0, n)
                    rawBytes += n
                    if (rawBytes > image.rawBytes) throw IOException("the image unpacks to more than ${image.rawBytes} bytes")
                    out.write(buf, 0, n)
                }
                // Whatever follows the gzip stream counts for the file's hash too.
                do {
                    val k = counting.read(buf, 0, buf.size)
                } while (k >= 0)
                out.flush()
                progress(gzBytes)"""
        new_block = """                val entryName = image.zipEntryName
                val decoded = if (entryName == null) {
                    GZIPInputStream(counting, 1 shl 16)
                } else {
                    SingleImageZipInputStream(counting, entryName, image.rawBytes)
                }
                val buf = ByteArray(1 shl 18)
                val head = ByteArray(2)
                decoded.use {
                    while (true) {
                        if (cancelled) throw IOException("stopped")
                        val n = decoded.read(buf)
                        if (n < 0) break
                        // ext4 superblock magic 0xEF53, little endian at byte 1080.
                        for (k in 0 until n) {
                            val at = rawBytes + k
                            if (at == Images.EXT4_MAGIC_OFFSET) head[0] = buf[k]
                            if (at == Images.EXT4_MAGIC_OFFSET + 1) {
                                head[1] = buf[k]
                                val magic = (head[0].toInt() and 0xff) or ((head[1].toInt() and 0xff) shl 8)
                                if (magic != Images.EXT4_MAGIC) throw IOException("not an ext4 image")
                            }
                            if (at > Images.EXT4_MAGIC_OFFSET + 1) break
                        }
                        rawMd.update(buf, 0, n)
                        rawBytes += n
                        if (rawBytes > image.rawBytes) throw IOException("the image unpacks to more than ${image.rawBytes} bytes")
                        out.write(buf, 0, n)
                    }
                    // The ZIP central directory and any unread trailing bytes count for the archive hash.
                    do {
                        val k = counting.read(buf, 0, buf.size)
                    } while (k >= 0)
                    out.flush()
                    progress(gzBytes)
                }"""
        prep = replace_once(prep, old_block, new_block, "PrepareJob decode block")
        prep_path.write_text(prep, encoding="utf-8")


def wire_images_model(repo_root: Path, manifest: dict) -> None:
    guest = manifest["guest_image"]
    release_url = f"https://github.com/DeathThoughts/OneOS-test/releases/tag/{manifest['release_tag']}"
    images_path = repo_root / "app-android/core/model/src/main/kotlin/com/fahadali/oneos/core/model/Images.kt"
    text = images_path.read_text(encoding="utf-8")

    if "val zipEntryName: String? = null" not in text:
        text = replace_once(
            text,
            "    val family: SystemFamily,\n) {",
            "    val family: SystemFamily,\n"
            "    /** Exact image entry in a ZIP archive. A null name selects gzip. */\n"
            "    val zipEntryName: String? = null,\n"
            "    val mirrorUrlOverride: String? = null,\n) {",
            "Images.kt ImageRef fields",
        )
        text = replace_once(
            text,
            '    val mirrorUrl: String get() = "${Images.MIRROR_FILES_URL}$gzName/download"',
            '    val mirrorUrl: String get() = mirrorUrlOverride ?: "${Images.MIRROR_FILES_URL}$gzName/download"',
            "Images.kt mirrorUrl",
        )
        text = replace_once(
            text,
            '            } else if (raw != null) {\n'
            '                val pick = if (profile == null || profile.choice(raw.id) != null) "pick ${raw.gzName}" else wantText\n'
            '                ImageIdentity(\n'
            '                    ImageState.UNCOMPRESSED, raw,\n'
            '                    block("Pick the .img.gz file", "$name is the unpacked ${raw.shortName} image. OneOS unpacks the image itself: $pick.", R_UNCOMPRESSED),\n'
            '                )',
            '            } else if (raw != null) {\n'
            '                val title = if (raw.zipEntryName == null) "Pick the .img.gz file" else "Pick the ZIP file"\n'
            '                val pick = if (profile == null || profile.choice(raw.id) != null) "pick ${raw.gzName}" else wantText\n'
            '                ImageIdentity(\n'
            '                    ImageState.UNCOMPRESSED, raw,\n'
            '                    block(title, "$name is the unpacked ${raw.shortName} image. OneOS unpacks the image itself: $pick.", R_UNCOMPRESSED),\n'
            '                )',
            "Images.kt uncompressed block",
        )

    if IMAGE_ID_CONST not in text:
        text = replace_once(
            text,
            '    const val ID_FIND_X9U_IN_1105 = "coloros17-findx9u-in-1105"\n',
            '    const val ID_FIND_X9U_IN_1105 = "coloros17-findx9u-in-1105"\n'
            f'    const val {IMAGE_ID_CONST} = "{IMAGE_ID}"\n',
            "Images.kt ID const",
        )
        gms_bool = "true" if guest["google_services"] else "false"
        pres_bool = "true" if guest["presents_gms"] else "false"
        image_def = f"""
    /**
     * ColorOS 17 China ({guest["security_patch"]} security patch, OnePlus 13T 17.0.0.104, Twinity 4.0c port),
     * ported to a MysticGSI system image from `super.img.zst` with Poco F5 hardware partitions excluded.
     * Experimental and untested on the base Galaxy S23.
     */
    val {IMAGE_CONST} = ImageRef(
        id = {IMAGE_ID_CONST},
        label = "ColorOS 17, OnePlus 13T CN build (Twinity 4.0c)",
        shortName = "OnePlus 13T CN",
        name = "ColorOS 17",
        variant = "Twinity 4.0c",
        blurb = "OPPO's China system from the OnePlus 13T (17.0.0.104, Twinity 4.0c). Experimental and untested on the Galaxy S23.",
        downloadUrl = "{release_url}",
        sdk = {guest["sdk"]},
        buildId = "{guest["build_id"]}",
        gzName = "{guest["gz_name"]}",
        gzBytes = {format_kotlin_long(int(guest["gz_bytes"]))},
        gzSha256 = "{guest["gz_sha256"]}",
        rawBytes = {format_kotlin_long(int(guest["raw_bytes"]))},
        rawSha256 = "{guest["raw_sha256"]}",
        testedOn = null,
        cpuRequired = emptyList(),
        cpuUsedByApps = emptyList(),
        googleServices = {gms_bool},
        presentsGms = {pres_bool},
        family = SystemFamily.COLOROS,
        mirrorUrlOverride = "{release_url}",
    )
"""
        text = replace_once(
            text,
            "    val DATA_UPGRADES: Map<String, String> =",
            image_def + "\n    val DATA_UPGRADES: Map<String, String> =",
            "Images.kt COLOROS_TWINITY_4C definition",
        )
        text = replace_once(
            text,
            "val ALL: List<ImageRef> = listOf(FIND_N6, FIND_X9U_IN_1105,",
            f"val ALL: List<ImageRef> = listOf(FIND_N6, FIND_X9U_IN_1105, {IMAGE_CONST},",
            "Images.kt ALL list",
        )

    images_path.write_text(text, encoding="utf-8")


def wire_package_sets_and_pins(repo_root: Path, manifest: dict, metadata_dir: Path) -> None:
    payloads: dict[str, dict] = manifest["payloads"]
    guarded = set(manifest["guarded_files"])
    remote_src = manifest["remote_source"]

    sha_content = (metadata_dir / f"{SET_ID}.sha256").read_text(encoding="utf-8")
    guards_content = (metadata_dir / f"{SET_ID}.guards.txt").read_text(encoding="utf-8")
    for base in (
        repo_root / "app-android/packages",
        repo_root / "app-android/core/model/src/test/resources/packages",
    ):
        base.mkdir(parents=True, exist_ok=True)
        (base / f"{SET_ID}.sha256").write_text(sha_content, encoding="utf-8")
        (base / f"{SET_ID}.guards.txt").write_text(guards_content, encoding="utf-8")

    sizes_path = repo_root / "app-android/packages/sizes.txt"
    remotes_path = repo_root / "app-android/packages/remote.txt"
    sizes_lines = sizes_path.read_text(encoding="utf-8").splitlines(keepends=True)
    remotes_lines = remotes_path.read_text(encoding="utf-8").splitlines(keepends=True)

    existing_sizes: dict[str, int] = {}
    for ln in sizes_lines:
        s = ln.strip()
        if s and not s.startswith("#"):
            k, v = s.split(None, 1)
            existing_sizes[k] = int(v)

    existing_remotes: dict[str, str] = {}
    for ln in remotes_lines:
        s = ln.strip()
        if s and not s.startswith("#"):
            k, v = s.split(None, 1)
            existing_remotes[k] = v

    unique_shas = sorted({info["sha"]: int(info["size"]) for info in payloads.values()}.items())
    added_sizes = []
    added_remotes = []
    for sha, sz in unique_shas:
        if sha in existing_sizes:
            if existing_sizes[sha] != sz:
                raise ValueError(f"sizes.txt conflict for {sha}: {existing_sizes[sha]} vs {sz}")
        else:
            added_sizes.append(f"{sha} {sz}\n")
        if sha not in existing_remotes:
            added_remotes.append(f"{sha} {remote_src}\n")

    if added_sizes:
        if sizes_lines and not sizes_lines[-1].endswith("\n"):
            sizes_lines[-1] += "\n"
        sizes_lines.append(f"# ColorOS 17 China Twinity 4.0c ({SET_ID})\n")
        sizes_lines.extend(added_sizes)
        sizes_path.write_text("".join(sizes_lines), encoding="utf-8")

    if added_remotes:
        if remotes_lines and not remotes_lines[-1].endswith("\n"):
            remotes_lines[-1] += "\n"
        remotes_lines.append(f"# ColorOS 17 China Twinity 4.0c ({SET_ID})\n")
        remotes_lines.extend(added_remotes)
        remotes_path.write_text("".join(remotes_lines), encoding="utf-8")

    series_path = repo_root / "app-android/packages/series.txt"
    series_lines = series_path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_series_lines = []
    found_s25 = False
    for ln in series_lines:
        if ln.startswith("s25 "):
            parts = ln.strip().split()
            head, sets = parts[0], sorted(set(parts[1:]) | {SET_ID})
            new_series_lines.append(f"{head} {' '.join(sets)}\n")
            found_s25 = True
        else:
            new_series_lines.append(ln)
    if not found_s25:
        raise ValueError("series.txt: missing 's25 ' row")
    series_path.write_text("".join(new_series_lines), encoding="utf-8")

    ps_path = repo_root / "app-android/core/model/src/main/kotlin/com/fahadali/oneos/core/model/PackageSets.kt"
    ps_text = ps_path.read_text(encoding="utf-8")
    if SET_ID_CONST not in ps_text:
        ps_text = replace_once(
            ps_text,
            '    const val ID_X9U_IN_1105_SULTRA = "x9u-in-1105-sultra"\n',
            '    const val ID_X9U_IN_1105_SULTRA = "x9u-in-1105-sultra"\n'
            f'    const val {SET_ID_CONST} = "{SET_ID}"\n',
            "PackageSets.kt ID const",
        )
        total_bytes = sum(int(info["size"]) for info in payloads.values())
        file_entries = ",\n".join(
            f'            PackageFile("{name}", {classify_file_role(name, guarded)})'
            for name in sorted(payloads.keys())
        )
        set_def = f"""    /** Experimental ColorOS 17 China Twinity 4.0c overlay set for base Galaxy S23. */
    val {SET_CONST} = PackageSet(
        id = {SET_ID_CONST},
        imageId = Images.{IMAGE_ID_CONST},
        sourceDir = "{SOURCE_DIR_REL}",
        files = listOf(
{file_entries},
        ),
        omitted = listOf(TETHERING, WALLPAPER_GRID_RRO),
        approxBytes = {format_kotlin_long(total_bytes)},
        hasReturnApp = true,
    )

"""
        ps_text = replace_once(
            ps_text,
            "    val ALL: List<PackageSet> =",
            set_def + "    val ALL: List<PackageSet> =",
            "PackageSets.kt TWINITY_4C_S23 definition",
        )
        ps_text = replace_once(
            ps_text,
            "listOf(N6_GLOBAL_F8, N6_F8U, X9U_IN_1105_SULTRA,",
            f"listOf(N6_GLOBAL_F8, N6_F8U, X9U_IN_1105_SULTRA, {SET_CONST},",
            "PackageSets.kt ALL list",
        )
        ps_path.write_text(ps_text, encoding="utf-8")


def wire_profiles_and_stage_check(repo_root: Path) -> None:
    prof_path = repo_root / "app-android/core/model/src/main/kotlin/com/fahadali/oneos/core/model/Profiles.kt"
    prof = prof_path.read_text(encoding="utf-8")

    if "private val S23_CHOICES =" not in prof:
        s23_list_expr = "listOf(X9U_IN_1105_CHOICE, PIXEL9_CHOICE, INFINITYX_CHOICE, LINEAGE_CHOICE, HYPEROS_CHOICE, OXYGEN16_CHOICE, NOTHING_CHOICE, ORIGINOS_CHOICE)"
        m_s8550 = re.search(
            r'(val S_8550 = PhoneProfile\(.*?choices = )' + re.escape(s23_list_expr) + r'([^\n]*)',
            prof, flags=re.S,
        )
        if not m_s8550:
            raise ValueError("Profiles.kt: could not locate S_8550 choices")
        prof = (
            prof[:m_s8550.start()]
            + "private val S23_CHOICES = listOf(\n"
            + "        X9U_IN_1105_CHOICE, PIXEL9_CHOICE, INFINITYX_CHOICE, LINEAGE_CHOICE,\n"
            + "        HYPEROS_CHOICE, OXYGEN16_CHOICE, NOTHING_CHOICE, ORIGINOS_CHOICE,\n"
            + "    )\n\n    "
            + m_s8550.group(1)
            + f"S23_CHOICES + ImageChoice(Images.{IMAGE_ID_CONST}, PackageSets.{SET_ID_CONST}, tested = false),"
            + prof[m_s8550.end():]
        )
        m_splus = re.search(
            r'(val S_PLUS_8550 = PhoneProfile\(.*?choices = )' + re.escape(s23_list_expr) + r'([^\n]*)',
            prof, flags=re.S,
        )
        if not m_splus:
            raise ValueError("Profiles.kt: could not locate S_PLUS_8550 choices")
        prof = prof[:m_splus.start()] + m_splus.group(1) + "S23_CHOICES," + prof[m_splus.end():]

        prof = replace_once(
            prof,
            '    val S_8550_JP = S_8550.copy(id = ID_S_8550_JP, label = "Galaxy S23 (au)", modelPrefix = "SCG19", codename = "SCG19")',
            '    val S_8550_JP = S_8550.copy(\n'
            '        id = ID_S_8550_JP, label = "Galaxy S23 (au)", modelPrefix = "SCG19", codename = "SCG19",\n'
            '        choices = S23_CHOICES,\n'
            '    )',
            "Profiles.kt S_8550_JP",
        )

    if "fun forkPatchesTested(" not in prof:
        prof = replace_once(
            prof,
            "    fun byId(id: String, profiles: List<PhoneProfile> = BUILT_IN): PhoneProfile? = profiles.firstOrNull { it.id == id }\n",
            "    fun byId(id: String, profiles: List<PhoneProfile> = BUILT_IN): PhoneProfile? = profiles.firstOrNull { it.id == id }\n\n"
            '    fun forkPatchesTested(model: String): Boolean = Regex("^SM-(S911|S938)[A-Z0-9]*$").matches(model)\n',
            "Profiles.kt forkPatchesTested",
        )
    prof_path.write_text(prof, encoding="utf-8")

    sc_path = repo_root / "app-android/app/src/full/kotlin/com/fahadali/oneos/session/StageCheck.kt"
    sc = sc_path.read_text(encoding="utf-8")
    if "forkPatchesTested" not in sc:
        if "import com.fahadali.oneos.R\n" not in sc:
            sc = replace_once(
                sc,
                "import com.fahadali.oneos.BuildConfig\n",
                "import com.fahadali.oneos.BuildConfig\nimport com.fahadali.oneos.R\n",
                "StageCheck import R",
            )
        if "import com.fahadali.oneos.core.model.Gate\n" not in sc:
            sc = replace_once(
                sc,
                "import com.fahadali.oneos.core.model.GateInput\n",
                "import com.fahadali.oneos.core.model.Gate\nimport com.fahadali.oneos.core.model.GateInput\n",
                "StageCheck import Gate",
            )
        sc = replace_once(
            sc,
            "        val report = SwitchGates.evaluate(phase, input)",
            """        val report = SwitchGates.evaluate(phase, input).let { evaluated ->
            if (Profiles.forkPatchesTested(facts.model)) evaluated else evaluated.copy(
                gates = evaluated.gates + Gate(
                    Finding("fork-patch-compatibility", Level.WARN,
                        app.getString(R.string.fork_patch_warning_title), app.getString(R.string.fork_patch_warning_message)),
                    app.getString(R.string.fork_patch_warning_ack),
                ),
            )
        }""",
            "StageCheck fork warning",
        )
        sc_path.write_text(sc, encoding="utf-8")


def wire_ui_and_strings(repo_root: Path) -> None:
    en_path = repo_root / "app-android/app/src/full/res/values/strings_advanced.xml"
    en = en_path.read_text(encoding="utf-8")
    if "image_split_archive_instructions" not in en:
        en = replace_once(
            en,
            '    <string name="image_pick_summary">Opens your files. OneOS reads only the file you pick.</string>\n',
            '    <string name="image_pick_summary">Opens your files. OneOS reads only the file you pick.</string>\n'
            '    <string name="image_split_archive_instructions">Download all .img.gz.001+ parts from the release and join them once (cat %1$s.00* &gt; %1$s). Then pick %1$s here.</string>\n',
            "EN strings_advanced split instructions",
        )
    if "fork_patch_warning_title" not in en:
        en = replace_once(
            en,
            "</resources>",
            '    <string name="fork_patch_warning_title">Patches tested on two phone models</string>\n'
            '    <string name="fork_patch_warning_message">This fork’s patches have been tested only on the base Galaxy S23 and Galaxy S25 Ultra. They are likely to fail on other Galaxy S and A models and may also fail on other phones. Proceed with caution.</string>\n'
            '    <string name="fork_patch_warning_ack">I understand that these patches may fail on this phone.</string>\n'
            "</resources>",
            "EN strings_advanced fork warning",
        )
    en_path.write_text(en, encoding="utf-8")

    pt_path = repo_root / "app-android/app/src/full/res/values-pt-rBR/strings_advanced.xml"
    pt = pt_path.read_text(encoding="utf-8")
    if "image_split_archive_instructions" not in pt:
        pt = replace_once(
            pt,
            "</resources>",
            '    <string name="image_split_archive_instructions">Baixe todas as partes .img.gz.001+ do release e junte-as uma vez (cat %1$s.00* &gt; %1$s). Depois escolha o arquivo %1$s aqui.</string>\n'
            '    <string name="fork_patch_warning_title">Correções testadas em dois modelos</string>\n'
            '    <string name="fork_patch_warning_message">As correções deste fork foram testadas apenas no Galaxy S23 base e no Galaxy S25 Ultra. É provável que falhem em outros modelos Galaxy S e A, e também podem falhar em outros celulares. Prossiga com cuidado.</string>\n'
            '    <string name="fork_patch_warning_ack">Entendo que estas correções podem falhar neste celular.</string>\n'
            "</resources>",
            "pt-rBR strings_advanced",
        )
    pt_path.write_text(pt, encoding="utf-8")

    ia_path = repo_root / "app-android/app/src/full/kotlin/com/fahadali/oneos/ui/ImageActivity.kt"
    ia = ia_path.read_text(encoding="utf-8")
    if "ID_COLOROS_TWINITY_4C" not in ia:
        ia = replace_once(
            ia,
            '.putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/gzip", "application/x-gzip", "application/octet-stream", "*/*"))',
            '.putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/gzip", "application/x-gzip", "application/zip", "application/octet-stream", "*/*"))',
            "ImageActivity MIME types",
        )
        ia = replace_once(
            ia,
            '            Cards.row(systems, GLYPHS[i % GLYPHS.size], Images.systemName(image, p), notes.joinToString(" · "))\n',
            '            Cards.row(systems, GLYPHS[i % GLYPHS.size], Images.systemName(image, p), notes.joinToString(" · "))\n'
            f'            if (image.id == Images.{IMAGE_ID_CONST}) {{\n'
            '                CardRows.text(systems, getString(R.string.image_split_archive_instructions, image.gzName), R.style.OneOS_Text_Caption, inList = true)\n'
            '            }\n',
            "ImageActivity split caption",
        )
        ia_path.write_text(ia, encoding="utf-8")

    sa_path = repo_root / "app-android/app/src/full/kotlin/com/fahadali/oneos/ui/SystemsActivity.kt"
    sa = sa_path.read_text(encoding="utf-8")
    if "ID_COLOROS_TWINITY_4C" not in sa:
        sa = replace_once(
            sa,
            """    /** "I have the file" of [imageId] (null: whichever system it is). */
    fun choose(imageId: String?) {
        pickFor = imageId""",
            f"""    /** "I have the file" of [imageId] (null: whichever system it is). */
    fun choose(imageId: String?) {{
        if (imageId == Images.{IMAGE_ID_CONST}) {{
            val image = Images.{IMAGE_CONST}
            AlertDialog.Builder(this).setTitle(image.nameWithVariant)
                .setMessage(getString(R.string.image_split_archive_instructions, image.gzName))
                .setPositiveButton(R.string.image_pick_title) {{ _, _ -> pickArchive(imageId) }}
                .setNegativeButton(android.R.string.cancel, null).show()
        }} else {{
            pickArchive(imageId)
        }}
    }}

    private fun pickArchive(imageId: String?) {{
        pickFor = imageId""",
            "SystemsActivity choose dialog",
        )
        sa = replace_once(
            sa,
            '.putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/gzip", "application/x-gzip", "application/octet-stream", "*/*"))',
            '.putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/gzip", "application/x-gzip", "application/zip", "application/octet-stream", "*/*"))',
            "SystemsActivity MIME types",
        )
        sa = replace_once(
            sa,
            "        val color = image(com.fahadali.oneos.core.model.SystemFamily.COLOROS, Images.FIND_N6)\n",
            "        val color = image(com.fahadali.oneos.core.model.SystemFamily.COLOROS, Images.FIND_N6)\n"
            f"        val twinity = available.firstOrNull {{ it.id == Images.{IMAGE_ID_CONST} }}\n",
            "SystemsActivity twinity lookup",
        )
        sa = replace_once(
            sa,
            "            CatalogEntry(color.name, color, R.drawable.os_coloros, available.any { it.id == color.id }),\n",
            "            CatalogEntry(color.name, color, R.drawable.os_coloros, available.any { it.id == color.id }),\n"
            "            twinity?.let { CatalogEntry(it.nameWithVariant, it, R.drawable.os_coloros, true) },\n",
            "SystemsActivity CatalogEntry",
        )
        sa = replace_once(
            sa,
            "                        image.downloadUrl?.let { drive ->",
            "                        image.downloadUrl?.takeUnless { it == image.mirrorUrl }?.let { drive ->",
            "SystemsActivity dedup mirrorUrl",
        )
        sa_path.write_text(sa, encoding="utf-8")


def wire_root_and_switch_scripts(repo_root: Path, manifest: dict) -> None:
    guest = manifest["guest_image"]
    payload_names = " ".join(sorted(manifest["payloads"].keys()))

    root_sh_path = repo_root / "app-android/app/src/full/assets/root/oneos_root.sh"
    rsh = root_sh_path.read_text(encoding="utf-8")
    if "SET_TWINITY=" not in rsh:
        rsh = replace_once(
            rsh,
            'SET_COMMON="',
            f'SET_TWINITY="{payload_names}"\nSET_COMMON="',
            "oneos_root.sh SET_TWINITY",
        )
        rsh = replace_once(
            rsh,
            '        "s-8550 hyperos-lhasa") echo hyper-f8u ;;\n',
            '        "s-8550 hyperos-lhasa") echo hyper-f8u ;;\n'
            f'        "s-8550 {IMAGE_ID}") echo {SET_ID} ;;\n',
            "oneos_root.sh profile_set",
        )
        rsh = replace_once(
            rsh,
            '        coloros17-findx9u-in-1105) echo "',
            f'        {IMAGE_ID}) echo "{guest["raw_bytes"]} {guest["raw_sha256"]}" ;;\n'
            '        coloros17-findx9u-in-1105) echo "',
            "oneos_root.sh image_raw",
        )
        rsh = replace_once(
            rsh,
            '        x9u-in-1105-sultra) echo "$SET_COMMON',
            f'        {SET_ID}) echo "$SET_TWINITY" ;;\n'
            '        x9u-in-1105-sultra) echo "$SET_COMMON',
            "oneos_root.sh set_files",
        )
        rsh = replace_once(
            rsh,
            '        x9u-in-1105-sultra) echo coloros17-findx9u-in-1105 ;;\n',
            '        x9u-in-1105-sultra) echo coloros17-findx9u-in-1105 ;;\n'
            f'        {SET_ID}) echo {IMAGE_ID} ;;\n',
            "oneos_root.sh set_image",
        )
        rsh = replace_once(
            rsh,
            "coloros17-findx9u-in-1105) echo coloros ;;",
            f"coloros17-findx9u-in-1105|{IMAGE_ID}) echo coloros ;;",
            "oneos_root.sh image_family",
        )
        if guest["google_services"] and not guest["presents_gms"] and 'IMAGES_GMS="' in rsh:
            rsh = replace_once(
                rsh,
                "coloros17-findn6 coloros17-findx9u-in-1105 ",
                f"coloros17-findn6 coloros17-findx9u-in-1105 {IMAGE_ID} ",
                "oneos_root.sh IMAGES_GMS",
            )
        root_sh_path.write_text(rsh, encoding="utf-8")

    sw_path = repo_root / "switcher/assets/oneos_switch.sh"
    sw = sw_path.read_text(encoding="utf-8")
    if "same_restart()" not in sw:
        start_idx = sw.index("record_restart_home() {")
        end_idx = sw.index("\nGEN=", start_idx)
        sw = sw[:start_idx] + RESTART_HOME_SHELL_FUNC + sw[end_idx:]
        sw = replace_once(
            sw,
            "            echo '[UI Activator] WindowManager is ready! Dismissing bootanim and unlocking...'\n",
            "            echo '[UI Activator] WindowManager is ready! Dismissing bootanim and unlocking...'\n"
            "            (record_restart_home) </dev/null &\n",
            "oneos_switch.sh launch record_restart_home on WM ready",
        )
        sw = replace_once(
            sw,
            "            echo '[UI Activator] Home activated!'\n"
            "            (record_restart_home) </dev/null &\n",
            "            echo '[UI Activator] Home activated!'\n",
            "oneos_switch.sh remove late record_restart_home",
        )
        sw_path.write_text(sw, encoding="utf-8")
        trh = repo_root / "scripts/test-restart-home.py"
        trh.parent.mkdir(parents=True, exist_ok=True)
        trh.write_text(TEST_RESTART_HOME_PY, encoding="utf-8")


def wire_gradle_files(repo_root: Path) -> None:
    app_gradle = repo_root / "app-android/app/build.gradle.kts"
    ag = app_gradle.read_text(encoding="utf-8")
    if f'"{SET_ID}" to' not in ag:
        ag = replace_once(
            ag,
            '"x9u-in-1105-sultra" to',
            f'"{SET_ID}" to (File(repoRoot, "{SOURCE_DIR_REL}") to listOf("tethering_payload.img", "OneOSWallpapersFoldGrid.apk")),\n    "x9u-in-1105-sultra" to',
            "app/build.gradle.kts packageSets",
        )
    ag = re.sub(r"versionCode = \d+", f"versionCode = {VERSION_CODE}", ag, count=1)
    ag = re.sub(r'versionName = "[^"]+"', f'versionName = "{VERSION_NAME}"', ag, count=1)
    app_gradle.write_text(ag, encoding="utf-8")

    guest_gradle = repo_root / "app-android/guest/build.gradle.kts"
    gg = guest_gradle.read_text(encoding="utf-8")
    gg = re.sub(r"versionCode = \d+", f"versionCode = {VERSION_CODE}", gg, count=1)
    gg = re.sub(r'versionName = "[^"]+"', f'versionName = "{VERSION_NAME}"', gg, count=1)
    guest_gradle.write_text(gg, encoding="utf-8")


def wire_unit_tests(repo_root: Path, manifest: dict) -> None:
    guest = manifest["guest_image"]
    has_gms = bool(guest["google_services"])
    presents_gms = bool(guest["presents_gms"])
    gms_hideable = has_gms and not presents_gms

    ast_path = repo_root / "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/ApkSeriesTest.kt"
    ast = ast_path.read_text(encoding="utf-8")
    if "twinityShipsInTheSeriesThatIncludesGalaxyS23" not in ast:
        ast = replace_once(
            ast,
            "    @Test\n    fun theListBuildsUseIsTheModels() {",
            f"""    @Test
    fun twinityShipsInTheSeriesThatIncludesGalaxyS23() {{
        assertEquals(listOf(ApkSeries.S25), ApkSeries.entries.filter {{
            PackageSets.{SET_ID_CONST} in it.setIds()
        }})
    }}

    @Test
    fun theListBuildsUseIsTheModels() {{""",
            "ApkSeriesTest",
        )
        ast_path.write_text(ast, encoding="utf-8")

    it_path = repo_root / "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/ImagesTest.kt"
    it_text = it_path.read_text(encoding="utf-8")
    if "twinityImportVerifiesOnGalaxyS23" not in it_text:
        twinity_test = f"""    @Test
    fun twinityImportVerifiesOnGalaxyS23() {{
        val twinity = Images.{IMAGE_CONST}
        val archive = ImageFileCheck(twinity.gzName, twinity.gzBytes, twinity.gzSha256, twinity.gzBytes)
        val s23 = Profiles.S_8550
        assertNull(twinity.zipEntryName)
        assertEquals(twinity.downloadUrl, twinity.mirrorUrl)
        assertEquals(ImageState.VERIFIED, ImageIdentifier.identify(archive, s23).state)
        assertEquals(ImageState.WRONG_FOR_PHONE, ImageIdentifier.identify(archive, Profiles.S_PLUS_8550).state)
        val raw = ImageIdentifier.identify(ImageFileCheck("system.img", twinity.rawBytes), s23)
        assertEquals(ImageState.UNCOMPRESSED, raw.state)
        assertTrue(raw.finding.detail.contains(twinity.gzName))
        assertNull(twinity.testedOn)
    }}

"""
        it_text = replace_once(it_text, "    @Test\n    fun builtInTable() {", twinity_test + "    @Test\n    fun builtInTable() {", "ImagesTest method")
        if has_gms:
            it_text = replace_once(
                it_text,
                "listOf(n6, xin, pixel, Images.PIXEL_KODIAK, Images.PIXEL_KOMODO, Images.INFINITYX, Images.NOTHING_METROID, Images.NOTHING_ASTEROIDS, Images.OXYGEN16, Images.ORIGINOS7, Images.LINEAGE24),\n            Images.ALL.filter { it.googleServices }",
                f"listOf(n6, xin, Images.{IMAGE_CONST}, pixel, Images.PIXEL_KODIAK, Images.PIXEL_KOMODO, Images.INFINITYX, Images.NOTHING_METROID, Images.NOTHING_ASTEROIDS, Images.OXYGEN16, Images.ORIGINOS7, Images.LINEAGE24),\n            Images.ALL.filter {{ it.googleServices }}",
                "ImagesTest googleServices",
            )
        if presents_gms:
            it_text = replace_once(
                it_text,
                "assertEquals(listOf(pixel, Images.PIXEL_KODIAK, Images.PIXEL_KOMODO, Images.INFINITYX, Images.NOTHING_ASTEROIDS, Images.ORIGINOS7), Images.ALL.filter { it.presentsGms })",
                f"assertEquals(listOf(Images.{IMAGE_CONST}, pixel, Images.PIXEL_KODIAK, Images.PIXEL_KOMODO, Images.INFINITYX, Images.NOTHING_ASTEROIDS, Images.ORIGINOS7), Images.ALL.filter {{ it.presentsGms }})",
                "ImagesTest presentsGms",
            )
        if gms_hideable:
            it_text = replace_once(
                it_text,
                "assertEquals(listOf(n6, xin, Images.NOTHING_METROID, Images.OXYGEN16, Images.LINEAGE24), Images.ALL.filter { it.gmsHideable })",
                f"assertEquals(listOf(n6, xin, Images.{IMAGE_CONST}, Images.NOTHING_METROID, Images.OXYGEN16, Images.LINEAGE24), Images.ALL.filter {{ it.gmsHideable }})",
                "ImagesTest gmsHideable",
            )
        it_text = replace_once(
            it_text,
            "listOf(n6, xin, pixel, Images.PIXEL_KODIAK,",
            f"listOf(n6, xin, Images.{IMAGE_CONST}, pixel, Images.PIXEL_KODIAK,",
            "ImagesTest ALL list",
        )
        it_text = replace_once(
            it_text,
            "SystemFamily.COLOROS, SystemFamily.COLOROS,",
            "SystemFamily.COLOROS, SystemFamily.COLOROS, SystemFamily.COLOROS,",
            "ImagesTest families",
        )
        it_text = replace_once(
            it_text,
            "Profiles.S_8550 to listOf(xin, Images.PIXEL_KOMODO, ifx, lineage, hyper, Images.OXYGEN16, nothing, origin),",
            f"Profiles.S_8550 to listOf(xin, Images.PIXEL_KOMODO, ifx, lineage, hyper, Images.OXYGEN16, nothing, origin, Images.{IMAGE_CONST}),",
            "ImagesTest S_8550 takes",
        )
        if int(guest["sdk"]) == 36 and "Images.ALL.filter { it.sdk == 36 }" in it_text:
            it_text = it_text.replace(
                "assertEquals(listOf(Images.OXYGEN16), Images.ALL.filter { it.sdk == 36 })",
                f"assertEquals(listOf(Images.{IMAGE_CONST}, Images.OXYGEN16), Images.ALL.filter {{ it.sdk == 36 }})",
            )
            it_text = it_text.replace(
                "if (i.family == SystemFamily.OXYGENOS) 36 else 37",
                f"if (i.family == SystemFamily.OXYGENOS || i.id == Images.{IMAGE_ID_CONST}) 36 else 37",
            )
        it_path.write_text(it_text, encoding="utf-8")

    pst_path = repo_root / "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/PackageSetsTest.kt"
    pst = pst_path.read_text(encoding="utf-8")
    if "twinityStagingSetMatchesItsPayloadAndGuardPins" not in pst:
        num_files = len(manifest["payloads"])
        twinity_ps_test = f"""    @Test
    fun twinityStagingSetMatchesItsPayloadAndGuardPins() {{
        val set = PackageSets.{SET_CONST}
        assertEquals(Images.{IMAGE_ID_CONST}, set.imageId)
        assertEquals({num_files}, set.files.size)
        val manifest = shas(set)
        val guardsText = guards(set)
        assertEquals(emptyList<String>(), set.check(manifest))
        assertEquals(emptyList<String>(), set.checkGuards(guardsText, manifest))
        assertEquals(ShaList.parse(manifest).entries.getValue("guards.txt"), Hashes.sha256(guardsText.toByteArray()))
        assertEquals(listOf(PackageSets.TETHERING, PackageSets.WALLPAPER_GRID_RRO), set.omitted)
        assertTrue(set.hasReturnApp)
    }}

"""
        pst = replace_once(
            pst,
            "    @Test fun fold8StableInheritsEverySharedPayloadAndGuard() {",
            twinity_ps_test + "    @Test fun fold8StableInheritsEverySharedPayloadAndGuard() {",
            "PackageSetsTest method",
        )
        pst = replace_once(
            pst,
            "listOf(PackageSets.N6_GLOBAL_F8, n6, PackageSets.X9U_IN_1105_SULTRA,",
            f"listOf(PackageSets.N6_GLOBAL_F8, n6, PackageSets.X9U_IN_1105_SULTRA, PackageSets.{SET_CONST},",
            "PackageSetsTest ALL list",
        )
        pst_path.write_text(pst, encoding="utf-8")

    prt_path = repo_root / "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/ProfilesTest.kt"
    prt = prt_path.read_text(encoding="utf-8")
    if "twinityIsOfferedOnlyOnTheBaseGalaxyS23" not in prt:
        twinity_prof_test = f"""    @Test
    fun twinityIsOfferedOnlyOnTheBaseGalaxyS23() {{
        val image = Images.{IMAGE_ID_CONST}
        assertEquals(listOf(Profiles.S_8550), Profiles.BUILT_IN.filter {{ it.choice(image) != null }})
        assertEquals(ImageChoice(image, PackageSets.{SET_ID_CONST}, tested = false), Profiles.S_8550.choice(image))
        assertEquals(PackageSets.ID_X9U_IN_1105_SULTRA, Profiles.S_8550.choice(Images.ID_FIND_X9U_IN_1105)?.packageSetId)
    }}

"""
        prt = replace_once(
            prt,
            "    @Test\n    fun theFourBundlesResolveToTheirProfiles() {",
            twinity_prof_test + "    @Test\n    fun theFourBundlesResolveToTheirProfiles() {",
            "ProfilesTest method",
        )
        old_s23_assert = """            assertEquals(
                listOf(xin, ImageChoice(Images.ID_PIXEL_KOMODO, PackageSets.ID_PIXEL9_SULTRA, tested = false), ifx, lineage, hyper,
                    ImageChoice(Images.ID_OXYGEN16, PackageSets.ID_OXYGEN16, tested = false), nothing, origin), // 0.5.19: Nothing OS (Metroid), owner
                p.choices,
            )
            assertEquals(Profiles.S_ULTRA_8550.choices, p.choices)"""
        new_s23_assert = f"""            val original = listOf(xin, ImageChoice(Images.ID_PIXEL_KOMODO, PackageSets.ID_PIXEL9_SULTRA, tested = false), ifx, lineage, hyper,
                ImageChoice(Images.ID_OXYGEN16, PackageSets.ID_OXYGEN16, tested = false), nothing, origin)
            val twinity = ImageChoice(Images.{IMAGE_ID_CONST}, PackageSets.{SET_ID_CONST}, tested = false)
            assertEquals(if (p == s23) original + twinity else original, p.choices)
            assertEquals(Profiles.S_ULTRA_8550.choices, p.choices.filterNot {{ it == twinity }})"""
        prt = replace_once(prt, old_s23_assert, new_s23_assert, "ProfilesTest S23 choices")

        prt = replace_once(
            prt,
            "        for (p in offerOrigin) assertEquals(p.id, Images.ID_ORIGINOS7, p.choices.last().imageId)",
            f"        for (p in offerOrigin) {{\n"
            f"            val last = p.choices.last {{ it.imageId != Images.{IMAGE_ID_CONST} }}\n"
            f"            assertEquals(p.id, Images.ID_ORIGINOS7, last.imageId)\n"
            f"        }}",
            "ProfilesTest offerOrigin last",
        )
        prt = replace_once(
            prt,
            "            listOf(Profiles.S_8550.choices, Profiles.S_8550.notes),\n            listOf(Profiles.S_8550_JP.choices, Profiles.S_8550_JP.notes),",
            "            listOf(Profiles.S_PLUS_8550.choices, Profiles.S_8550.notes),\n            listOf(Profiles.S_8550_JP.choices, Profiles.S_8550_JP.notes),",
            "ProfilesTest S_8550_JP",
        )
        fork_test = """
    @Test
    fun forkPatchWarningExemptsOnlyTheTestedModels() {
        for (model in listOf("SM-S911B", "SM-S911U1", "SM-S938B", "SM-S938N")) {
            org.junit.Assert.assertTrue(model, Profiles.forkPatchesTested(model))
        }
        for (model in listOf("SM-S916B", "SM-S918B", "SM-S931B", "SM-A556B", "SM-F956B", "", "SM-S911-invalid")) {
            org.junit.Assert.assertFalse(model, Profiles.forkPatchesTested(model))
        }
    }
}
"""
        prt = prt[:prt.rfind("}")] + fork_test
        prt_path.write_text(prt, encoding="utf-8")

    sst_path = repo_root / "app-android/core/root/src/test/kotlin/com/fahadali/oneos/core/root/StageScriptTest.kt"
    sst = sst_path.read_text(encoding="utf-8")
    if "twinityStagesAndPassesLaunchChecksOnlyOnTheBaseGalaxyS23" not in sst:
        gms_check_line = (
            '        refused(s.launch(hideGms = "1"), "test root: every launch check passed")'
            if gms_hideable
            else '        refused(s.launch(hideGms = "1"), "Google services")'
        )
        twinity_stage_test = f"""    @Test
    fun twinityStagesAndPassesLaunchChecksOnlyOnTheBaseGalaxyS23() = eachShell {{ fr ->
        val profile = Profiles.S_8550
        val image = Images.{IMAGE_CONST}
        val set = PackageSets.{SET_CONST}
        fr.props["ro.product.model"] = "SM-S911B"
        fr.props["ro.product.device"] = profile.codename
        fr.props["ro.soc.model"] = profile.socModel
        fr.writeProps()
        val s = Stager(fr, profile, image.id)
        for (other in listOf(Profiles.S_PLUS_8550, Profiles.S_ULTRA_8550, Profiles.S_8550_JP)) {{
            refused(fr.run(RootScript.Verb.BEGIN, "new", other.id, image.id, set.id, s.code), "does not run")
        }}
        refused(s.begin(setId = PackageSets.ID_X9U_IN_1105_SULTRA), "is not the file set")
        s.prepare()
        val marker = StageMarker.parse(File(fr.stage, StageMarker.FILE_NAME).readText())
        assertEquals(image.id, marker?.imageId)
        assertEquals(set.id, marker?.setId)
        assertEquals((RootScript.SCRIPTS + set.names).toSet(), ShaList.parse(File(fr.stage, "files.sha256").readText()).entries.keys)
{gms_check_line}
        refused(s.launch(), "test root: every launch check passed")
    }}

"""
        sst = replace_once(
            sst,
            "    @Test\n    fun verboseBootChoiceIsWrittenOnlyAfterLaunchChecks()",
            twinity_stage_test + "    @Test\n    fun verboseBootChoiceIsWrittenOnlyAfterLaunchChecks()",
            "StageScriptTest method",
        )
        sst_path.write_text(sst, encoding="utf-8")

    set_path = repo_root / "app-android/core/model/src/test/kotlin/com/fahadali/oneos/core/model/SessionEnvTest.kt"
    if gms_hideable and set_path.is_file():
        set_text = set_path.read_text(encoding="utf-8")
        if f"Images.{IMAGE_CONST}" not in set_text:
            set_text = replace_once(
                set_text,
                "assertEquals(listOf(Images.FIND_N6, Images.FIND_X9U_IN_1105, Images.NOTHING_METROID, Images.OXYGEN16, Images.LINEAGE24), Images.ALL.filter { it.gmsHideable })",
                f"assertEquals(listOf(Images.FIND_N6, Images.FIND_X9U_IN_1105, Images.{IMAGE_CONST}, Images.NOTHING_METROID, Images.OXYGEN16, Images.LINEAGE24), Images.ALL.filter {{ it.gmsHideable }})",
                "SessionEnvTest gmsHideable",
            )
            set_path.write_text(set_text, encoding="utf-8")


def wire_repository(repo_root: Path, manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata_dir = manifest_path.parent
    wire_zip_support(repo_root)
    wire_images_model(repo_root, manifest)
    wire_package_sets_and_pins(repo_root, manifest, metadata_dir)
    wire_profiles_and_stage_check(repo_root)
    wire_ui_and_strings(repo_root)
    wire_root_and_switch_scripts(repo_root, manifest)
    wire_gradle_files(repo_root)
    wire_unit_tests(repo_root, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "out/twinity-metadata/twinity-4c-manifest.json")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        wire_repository(args.repo_root.resolve(), args.manifest.resolve())
        print(f"Successfully wired {IMAGE_ID} ({SET_ID}) into {args.repo_root}", flush=True)
        return 0
    except Exception as exc:
        print(f"Wiring failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
