# Experimental Twinity ColorOS 17 China (`TWNTY 4.0c`) Port for Base Galaxy S23 (`SM-S911B`)

> **Status**: **Build-pipeline & unit-test validated — NOT YET TESTED ON A PHYSICAL `SM-S911B` PHONE.**
> Keep `tested = false` and leave [PR #38](https://github.com/DeathThoughts/OneOS-test/pull/38) in **Draft** until physical boot, lock/biometric isolation, storage isolation, and return-to-One-UI behavior are verified on hardware.

## 1. Scope & Safety Invariants

- **Branch**: [`experiment/gemini-twinity-4c`](https://github.com/DeathThoughts/OneOS-test/tree/experiment/gemini-twinity-4c) (branched off `debug`, independent of [PR #30](https://github.com/DeathThoughts/OneOS-test/pull/30) `test/hyperos-popsicle`).
- **Source ROM ([`images` release](https://github.com/DeathThoughts/OneOS-test/releases/tag/images))**:
  - `OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0.zip.t0.001` (`2,000,000,000` B, SHA-256 `afcac9a46ade06dfe5b7b6a735df872b2b9c02e29c277b7a10cff3761da58a21`)
  - `OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0.zip.t0.002` (`2,000,000,000` B, SHA-256 `8bf350f7e99e7483ea812f29571f53b4effccd2677886cb7949e06213aaceffb`)
  - `OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0.zip.t0.003` (`2,000,000,000` B, SHA-256 `b305e4ad647c99eb63f66e8f3eb15d44b7d338d41d186bbe098b1fc2ff295bd1`)
  - `OnePlus13T_ColorOS_CN_17.0.0.104_MARBLE_TWNTY4.0.zip.t0.004` (`702,545,504` B, SHA-256 `6a6325b368f640521199b9e2c6f5c2237efe1d0744cfcba2492a57f45f793559`)
  - Joined outer ZIP: `6,702,545,504` bytes -> inner ROM ZIP -> `images/super.img.zst` -> `9,126,805,504`-byte uncompressed `super.img` (OnePlus 13T China, ColorOS `17.0.0.104`, security patch `2026-09-01`).
- **Target Device**: Base Samsung Galaxy S23 (`SM-S911*`, `dm1q`, `SM8550`, `Profiles.S_8550` only). `S_PLUS_8550`, `S_ULTRA_8550`, and `S_8550_JP` keep `S23_CHOICES` without Twinity, and non-`SM-S911*` / non-`SM-S938*` devices get an explicit compatibility warning gate in `StageCheck.kt`.
- **Zero Poco (`marble`) Firmware Contamination**:
  - `scripts/build-twinity-port.py` unpacks only ColorOS userspace partitions (`system`, `system_ext`, `product`, `my_product`, `my_engineering`, `my_stock`, `my_heytap`, `my_company`, `my_carrier`, `my_region`, `my_preload`, `my_bigball`, `my_manifest`).
  - `vendor`, `odm`, `vendor_dlkm`, `odm_dlkm`, `system_dlkm`, and `mi_ext` are **never** merged into the guest rootfs and **never** flashed. The guest runs strictly atop Samsung's One UI 9.0 / Android 17 kernel and vendor HALs.
  - `odm` is inspected in a disposable scratch directory solely to check whether full regular-ELF64 `lib64/libaiboost.so` delegates exist; if Poco's `marble` ODM lacks them, the verified ColorOS 17 `oplus_odm_ai.tar` (`eb62212a42e25207e5a75b20ca04610ee321781982bdf665b980595ef1ceaabe`) from [`fork-payloads-v1`](https://github.com/DeathThoughts/OneOS-test/releases/tag/fork-payloads-v1) is staged.
- **Preserved OneOS Isolation & Protections**:
  - **Storage isolation**: `scripts/twinity_elf_sites.py` disassembles Twinity's own ARM64 `vold` and `service.incremental.so` to patch `"\0vold\0"` -> `"\0gvld\0"` and `try_start` `tbz` -> `b` (`guest_own_vold.py`, `guest_storage_guards.py`), keeping all writes under `/data/local/tmp/oneos` with `no_symlink` checks intact.
  - **Credential & Weaver/Gatekeeper/FRP isolation**: `make-overlays.sh` rebuilds `services_patched.jar` and `oplus-services_patched.jar` from Twinity's own jars with `guest_local_lock.py`, `guest_no_gatekeeper.py`, `guest_no_weaver.py`, `guest_no_wipe_secrets.py`, `guest_pdb_readonly.py`, `guest_pdb_init_survives.py`, and `guest_pdb_frp_inactive.py`.
  - **Biometric enrollment restrictions**: `guest_no_biometric_writes.py`, `guest_fingerprint_readonly.py`, `guest_no_biometric_cleanup.py`, and `guest_force_aidl_fingerprint.py` block guest enroll/remove before Samsung's HAL while routing lockscreen auth to Samsung's AIDL fingerprint HAL; `facehal` stays disabled (`docs/dead-ends.md`).
  - **Display & SurfaceFlinger**: `twinity_elf_sites.py` locates the `cbz x8` guard between `bl AIBinder_getExtension` and `bl IQtiComposer3Client::fromBinder` in Twinity's `surfaceflinger` so Samsung's HWC3 composer runs without QTI composer crashes; `ColorPanoramicSystemUI.apk` (+ `SystemUI_wave.dex` when zero-fuzz clean, else fallback `make-coloros-panoramic-aod.py`) is built from Twinity's own `SystemUI.apk`.
  - **Return to One UI**: `OneOSGuest.apk` (`hasReturnApp = true`) and `return_to_oneui.sh` (`ONEOS_RETURN_MODE=inplace` with reboot failsafe) remain intact.

## 2. Architecture Overview

| Script / File | Purpose |
| --- | --- |
| `scripts/twinity_elf_sites.py` | Pure-Python ARM64 disassembler & ELF site resolver for Twinity's `vold`, `service.incremental.so`, `crashrecovery` APEX, and `surfaceflinger`; idempotently updates `scripts/make-overlays.sh`. |
| `scripts/build-twinity-port.py` | End-to-end reproducible builder: verifies `.001`–`.004` SHA-256s, joins outer ZIP, extracts `images/super.img.zst` -> `super.img` (`9,126,805,504` B), runs `lpunpack` (excluding Poco hardware partitions), inspects actual framework SDK (`ro.build.version.sdk` & `framework-res.apk`), bridges MysticGSI SDK 37 patches, runs `mysticgsi-from-images.py --drop --no-debloat --type=coloros`, builds all OneOS overlays (`SOURCE_DATE_EPOCH=1794750818`), compresses/splits `OneOS_ColorOS17_OnePlus13T_CN_Twinity4c.img.gz`, and emits `out/twinity-metadata/twinity-4c-manifest.json`. |
| `scripts/wire-twinity-port.py` | Idempotently wires `Images.COLOROS_TWINITY_4C`, `PackageSets.TWINITY_4C_S23`, `Profiles.S_8550` (`tested = false`), `SingleImageZipInputStream.kt` + `PrepareJob.kt`, `SystemsActivity.kt` + `ImageActivity.kt` (separate `ColorOS 17 · Twinity 4.0c` card + split-part join instructions in EN & pt-BR), `oneos_root.sh`, `packages/*`, and unit tests. |
| `scripts/test-twinity-port.py` | Hermetic unit test suite covering ARM64 `vold`/`surfaceflinger` ELF resolution, `lpunpack` vendor/ODM exclusion, `oplus_odm_ai.tar` safety rules, and idempotent repo wiring. |
| `docs/workflows/build-twinity-port.yml` | Optional standalone GitHub Actions workflow (`contents: write`) to publish hash-named payloads, split `.img.gz.001+` guest image parts, and `OneOS-twinity-4c-debug.apk` to prerelease tag `twinity-4c-v1`. |

## 3. Reproducible Build Steps

### Option A — Automated CI Build on Draft PR #38 (Active Now)
Every pull-request run of `.github/workflows/build-apk.yml` executes the full ROM port, SDK inspection, overlay rebuild, repository wiring, unit tests (`:core:model:test`, `:core:root:testDebugUnitTest`), and signed debug APK assembly (`OneOS-debug.apk` signed with `DEBUG_KEYSTORE_B64`), uploading both `OneOS-debug.apk` and `twinity-4c-update-manifest.json` in the `oneos-debug` workflow artifact and printing the full manifest in the Actions job summary.

### Option B — Phone-Only 1-Tap Release Publishing (`twinity-4c-v1`)
Because GitHub's OAuth app restriction blocks third-party integrations from writing `.github/workflows/*` directly without the `workflow` token scope, the standalone release-publishing workflow is staged at `docs/workflows/build-twinity-port.yml`. From your phone browser:
1. Open `docs/workflows/build-twinity-port.yml` on branch `experiment/gemini-twinity-4c`, tap the pencil (**Edit**) icon, rename the path to `.github/workflows/build-twinity-port.yml`, and tap **Commit changes** directly to `experiment/gemini-twinity-4c`.
2. That push automatically runs **Build Twinity ColorOS 17 S23 Port**, publishing all hash-named overlay payloads, split `<1.9 GB` `OneOS_ColorOS17_OnePlus13T_CN_Twinity4c.img.gz.00*` parts, `SHA256SUMS.txt`, and `OneOS-twinity-4c-debug.apk` to prerelease `twinity-4c-v1`, and committing the static pins back to `experiment/gemini-twinity-4c`.

### Option C — Local Linux Reproduction
```sh
git checkout experiment/gemini-twinity-4c
git clone https://github.com/MysticGSI/mysticgsi.git mysticgsi
git -C mysticgsi checkout 825b850dd8ddff11fdb8ed024034fc00e94b1451
python3 -m venv mysticgsi/.venv
mysticgsi/.venv/bin/pip install -r mysticgsi/requirements.txt

python3 scripts/test-twinity-port.py -v
MYSTIC_PYTHON="$PWD/mysticgsi/.venv/bin/python" python3 scripts/build-twinity-port.py
python3 scripts/wire-twinity-port.py --manifest out/twinity-metadata/twinity-4c-manifest.json
cd app-android
./gradlew :guest:stageGuestManagerDebug :core:model:test :core:root:testDebugUnitTest :app:assembleFullDebug
```

## 4. Phone-Only Installation & First-Boot Checklist (`SM-S911B`)

1. Download `OneOS-debug.apk` (from the PR #38 Actions `oneos-debug` artifact or `twinity-4c-v1` release) and all `OneOS_ColorOS17_OnePlus13T_CN_Twinity4c.img.gz.00*` parts into `/sdcard/Download/`.
2. Join and verify the guest image in Termux on your S23:
   ```sh
   cd /sdcard/Download
   cat OneOS_ColorOS17_OnePlus13T_CN_Twinity4c.img.gz.00* > OneOS_ColorOS17_OnePlus13T_CN_Twinity4c.img.gz
   sha256sum OneOS_ColorOS17_OnePlus13T_CN_Twinity4c.img.gz
   ```
3. Install the debug APK on One UI 9.0, open **Choose a system**, tap the separate **ColorOS 17 · Twinity 4.0c** card, and pick `OneOS_ColorOS17_OnePlus13T_CN_Twinity4c.img.gz`.
4. Before switching, enable **Full session logs** in **Advanced** for the first test session, verify One UI fingerprint unlock + return-to-One-UI (`Back to One UI` app and `Vol Up + Vol Down` 3s failsafe), and export the session bundle if anything misbehaves.
