package com.fahadali.oneos.core.model

import org.junit.Assert.*
import org.junit.Test

class GoogleAccountQueueTest {
    @Test fun allSelectedAddressesAreDeduplicatedAndPreserved() {
        val names = listOf("student+test@example.invalid", "second@example.invalid", "student+test@example.invalid")
        assertEquals(names.distinct().sorted(), GoogleAccountQueue.decode(GoogleAccountQueue.encode(names)))
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
