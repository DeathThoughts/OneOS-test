package com.fahadali.oneos.core.model

import org.junit.Assert.*
import org.junit.Test

class GoogleAccountQueueTest {
    @Test fun allSelectedAddressesAreDeduplicatedAndPreserved() {
        val names = listOf("student+test@example.invalid", "second@example.invalid", "student+test@example.invalid")
        assertEquals(names.distinct().sorted(), GoogleAccountQueue.decode(GoogleAccountQueue.encode(names)))
    }

    @Test fun sequentialLoginSkipsWithoutDeletingAndNeverLoops() {
        val a = "one@example.invalid"
        val b = "two@example.invalid"
        val c = "three@example.invalid"
        val pending = listOf(a, b, c)
        assertEquals(a, GoogleAccountQueue.next(pending, emptySet()))
        assertEquals(b, GoogleAccountQueue.next(pending, setOf(a)))
        assertEquals(c, GoogleAccountQueue.next(pending - b, setOf(a, b)))
        assertNull(GoogleAccountQueue.next(pending, pending.toSet()))
        assertEquals(a, GoogleAccountQueue.next(pending - b, emptySet()))
        assertTrue(GoogleAccountQueue.confirmed(a, a, GoogleAccountQueue.TYPE))
        assertFalse(GoogleAccountQueue.confirmed(a, b, GoogleAccountQueue.TYPE))
        assertFalse(GoogleAccountQueue.confirmed(a, a, "other"))
        assertFalse(GoogleAccountQueue.confirmed(a, null, null))
    }

    @Test fun refusesAnythingExceptAccountAddressesAndCompletePayloads() {
        for (bad in listOf("secret token", "user@example.invalid\npassword", "../file", "a@b", "x@y..invalid")) {
            assertFalse(GoogleAccountQueue.valid(bad))
            assertTrue(runCatching { GoogleAccountQueue.encode(listOf(bad)) }.isFailure)
        }
        val bytes = GoogleAccountQueue.encode(listOf("user@example.invalid"))
        assertTrue(runCatching { GoogleAccountQueue.decode(bytes.copyOf(bytes.size - 1)) }.isFailure)
        assertTrue(runCatching { GoogleAccountQueue.decode(ByteArray(GoogleAccountQueue.MAX_BYTES + 1)) }.isFailure)
        assertEquals(emptyList<String>(), GoogleAccountQueue.decode(GoogleAccountQueue.encode(emptyList())))
    }
}
