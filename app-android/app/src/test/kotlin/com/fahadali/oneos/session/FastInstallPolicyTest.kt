package com.fahadali.oneos.session

import org.junit.Assert.*
import org.junit.Test

class FastInstallPolicyTest {
    @Test fun networkAndThermalLimits() {
        assertEquals(8, FastInstallPolicy.parallelism(30, false, 0))
        assertEquals(4, FastInstallPolicy.parallelism(30, true, 0))
        assertEquals(3, FastInstallPolicy.parallelism(30, false, 2))
        assertEquals(2, FastInstallPolicy.parallelism(30, false, 3))
        assertEquals(1, FastInstallPolicy.parallelism(1, false, 0))
    }
    @Test fun resumeRequiresExactRangeAndTotal() {
        assertTrue(FastInstallPolicy.validResume("bytes 100-199/200", 100, 200))
        assertFalse(FastInstallPolicy.validResume("bytes 99-199/200", 100, 200))
        assertFalse(FastInstallPolicy.validResume("bytes 100-199/300", 100, 200))
        assertFalse(FastInstallPolicy.validResume(null, 100, 200))
    }
    @Test fun rootModeHintsNeverTreatExecutablesAsData() {
        assertEquals("755", FastInstallPolicy.mode("guest_prepare.sh"))
        assertEquals("755", FastInstallPolicy.mode("surfaceflinger_patched"))
        assertEquals("644", FastInstallPolicy.mode("services_patched.jar"))
    }
}
