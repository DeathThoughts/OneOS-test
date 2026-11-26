# CI fast path (experiment)

Baseline: debug build #249. Media safety alone took 308 seconds; final APK assembly 9 seconds. This change runs all 56 existing media tests across four standard public-repository runners while the normal build proceeds. Both publishing jobs depend on the full media workflow and APK build. An uploaded APK artifact is a test candidate until the workflow as a whole succeeds.

The model and root Gradle test invocations are consolidated, preserving every existing test filter. The debug branch can write its own Gradle cache; arbitrary PR branches remain read-only. No signing keys or ROM images are added to caches.

Validation runs on this branch without compiling an APK. Integrate after the debloat inventory work, then validate the full combined build. Future optimization: reuse verified per-image inventories and payload artifacts keyed by raw image hash, tooling revision and policy digest. Do not put multi-gigabyte raw ROMs in the general Gradle cache, skip a signature/hash check, or reuse a changed image's inventory.
