import java.util.Properties

// OneOS inside the guest (Pixel, ColorOS): "Back to One UI" in one tap, from
// its screen or its Quick Settings tile. It only writes a request file in its
// own data folder (files/oneos_return); the session's watcher on the host
// (switcher/assets/oneos_switch.sh) sees it and returns. guest_prepare.sh
// installs it in every guest as a system app (/oem/app/OneOSGuest), from
// $STAGE/OneOSGuest.apk (scripts/make-overlays.sh copies the release build).
// SESL9 supplies the same native components on Pixel and ColorOS.
// Package metadata and module update checks need package visibility and network access.
plugins {
    alias(libs.plugins.android.application)
}

// The OneOS release key, as :app signs with it (never in the repo; /srv/projects is shared).
val releaseKeys: Properties? =
    File(System.getProperty("user.home"), ".oneos-release/keystore.properties").takeIf { it.isFile }?.let { f ->
        Properties().apply { f.inputStream().use(::load) }
    }

fun patchKey(channel: String): String {
    val value = providers.environmentVariable("ONEOS_PATCH_${channel.uppercase()}_PUBLIC_KEY").orNull.orEmpty()
    require(value.isEmpty() || value.matches(Regex("[A-Za-z0-9+/]{59}="))) {
        "OneOS patch key must be a base64 Ed25519 X.509 public key"
    }
    return "\"$value\""
}

val debugPatchKey = patchKey("debug")
val releasePatchKey = patchKey("release")
require(debugPatchKey == "\"\"" || debugPatchKey != releasePatchKey) { "Patch channels require distinct keys" }

android {
    sourceSets.getByName("main").java.srcDir(rootProject.file("patch-protocol/src/main/java"))
    sourceSets.getByName("main").kotlin.srcDir(rootProject.file("shared-guest-ui"))
    namespace = "com.fahadali.oneos.guest"
    compileSdk { version = release(37) }

    defaultConfig {
        applicationId = "com.fahadali.oneos.guest"
        minSdk = 31
        targetSdk = 36
        // 0.2 (OneOS 0.4.30): How's it going?, the checklist for Send to the OneOS team.
        versionCode = 185
        versionName = "0.5.37"
    }

    signingConfigs {
        // CI (.github/workflows/build-apk.yml) decodes the DEBUG_KEYSTORE_B64 secret
        // to ONEOS_DEBUG_KEYSTORE. Point debug signing at it explicitly: leaving it to
        // AGP's default ~/.android/debug.keystore still gave every run its own signer
        // (runs 43736112838 and before), so CI builds could not update each other.
        // Without the variable local builds keep AGP's default debug key.
        System.getenv("ONEOS_DEBUG_KEYSTORE")?.let(::File)?.takeIf { it.isFile }?.let { ks ->
            getByName("debug") {
                storeFile = ks
                storePassword = "android"
                keyAlias = "androiddebugkey"
                keyPassword = "android"
            }
        }
        if (releaseKeys != null) {
            create("release") {
                storeFile = file(releaseKeys.getProperty("storeFile"))
                storePassword = releaseKeys.getProperty("storePassword")
                keyAlias = releaseKeys.getProperty("keyAlias")
                keyPassword = releaseKeys.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        debug {
            buildConfigField("String", "SIGNED_PATCH_PUBLIC_KEY", debugPatchKey)
            signingConfig = signingConfigs.getByName("debug")
        }
        release {
            buildConfigField("String", "SIGNED_PATCH_PUBLIC_KEY", releasePatchKey)
            // R8 drops the unused Kotlin stdlib: the APK sits in the guest's /oem.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release")
        }
    }

    buildFeatures { buildConfig = true }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }

    lint {
        abortOnError = true
    }
}

kotlin {
    jvmToolchain(libs.versions.jvmToolchain.get().toInt())
}

// A release without the key would be an unsigned APK: refuse instead, as :app does.
tasks.matching { it.name == "packageRelease" }.configureEach {
    val hasKey = releaseKeys != null
    doFirst {
        check(hasKey) { "No release key: ~/.oneos-release/keystore.properties is missing (see app-android/README.md, section Build)." }
    }
}

configurations.configureEach {
    exclude(group = "androidx.core", module = "core")
    exclude(group = "androidx.core", module = "core-ktx")
    exclude(group = "androidx.customview", module = "customview")
    exclude(group = "androidx.coordinatorlayout", module = "coordinatorlayout")
    exclude(group = "androidx.drawerlayout", module = "drawerlayout")
    exclude(group = "androidx.viewpager2", module = "viewpager2")
    exclude(group = "androidx.viewpager", module = "viewpager")
    exclude(group = "androidx.appcompat", module = "appcompat")
    // Play Services pulls stock Fragment, but One UI Design supplies the SESL
    // implementation under the same AndroidX package names.
    exclude(group = "androidx.fragment", module = "fragment")
    // SESL9 Core and Fragment include their Kotlin extensions.
    exclude(group = "sesl.androidx.core", module = "core-ktx")
    exclude(group = "androidx.fragment", module = "fragment-ktx")
    exclude(group = "androidx.preference", module = "preference")
    exclude(group = "androidx.recyclerview", module = "recyclerview")
    exclude(group = "androidx.swiperefreshlayout", module = "swiperefreshlayout")
    exclude(group = "androidx.slidingpanelayout", module = "slidingpanelayout")
    exclude(group = "com.google.android.material", module = "material")
}

dependencies {
    implementation(project(":core:model"))
    implementation(libs.oneui.icons)
    implementation(libs.oneui.design) {
        // The design library's optional barcode scanner is unused; keep OneOS offline.
        exclude(group = "com.google.android.gms", module = "play-services-mlkit-barcode-scanning")
        exclude(group = "com.google.mlkit", module = "barcode-scanning-common")
    }
    implementation(libs.bundles.sesl9)
}
