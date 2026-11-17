package com.fahadali.oneos.ui

class OneOsApplication : android.app.Application() {
    override fun onCreate() {
        super.onCreate()
        // Owner's decision, 2026-11-17: the build type sets the battery limits; first, before any gate reads them.
        com.fahadali.oneos.core.model.SwitchGates.setBatteryLimits(
            com.fahadali.oneos.BuildConfig.GATE_BATTERY_MIN_PERCENT, com.fahadali.oneos.BuildConfig.GATE_BATTERY_MAX_TENTHS_C,
        )
        androidx.appcompat.app.AppCompatDelegate.setDefaultNightMode(androidx.appcompat.app.AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM)
    }
}
