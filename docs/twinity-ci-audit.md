# Twinity ColorOS 17 CI audit (initial fixes)

This document records confirmed blockers in the experimental Twinity port. **It is not a boot or compatibility certification.**

## Reproduced from GitHub Actions

- Build APK run [44222432919](https://github.com/DeathThoughts/OneOS-test/actions/runs/44222432919) failed during **Verify update handling**, before a full port, because `scripts/twinity_elf_sites.py` is a docstring-only stub.
- `scripts/test-twinity-port.py` currently expects `resolve_vold_sites`, `resolve_surfaceflinger_cbz_site`, and `update_make_overlays_text`; the build additionally expects `resolve_and_update_for_image`. These implementations must be added and tested against the actual Twinity binaries before considering the build ready.
- The dedicated workflow currently resides at `docs/workflows/build-twinity-port.yml`. GitHub Actions does not run files from this directory. Moving it into `.github/workflows/` requires repository permission with workflow-edit capability.

## Changes in the isolated fix branch

- Guest-manager update tests no longer implicitly download and build a multi-gigabyte ROM simply because `GITHUB_ACTIONS=true`. A separate dedicated job must explicitly set `ONEOS_BOOTSTRAP_TWINITY_PORT=1` to opt in to this behavior.
- The Twinity builder refuses ambiguous MysticGSI output (zero or multiple images) instead of selecting the largest `.img`, which could silently choose the wrong file.

## Remaining gates

1. Implement missing ARM64 ELF site resolvers, verify patch bytes and fail closed on changed binary layouts.
2. Run hermetic port tests and existing OneOS unit tests.
3. Enable a dedicated workflow with properly scoped permissions; ensure no automatic publication before success.
4. Verify the generated payload is a **single raw `system.img` guest filesystem**, even if the transport filename is `.img.gz`; do not use `super.img` as the final guest.
5. Verify manifest checksums, repeatable output, full APK signing and installation behavior.
6. Only then physically test boot, host storage and credentials, biometrics, hardware, and the return path on the base S23. Keep `tested=false` until that happens.

This first change is deliberately small. It **does not fix the missing ELF implementation** or claim a successful image/APK build.
