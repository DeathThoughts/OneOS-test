# Signed guest startup patches

OneOS v1 patches replace `guest_start.sh` for one compatible host build and
guest image. The bundled script is the default on a full switch from One UI.
Accepted patches remain available for subsequent guest restarts. Only
**Restart guest** in the OneOS guest app can start a new trial. KernelSU's
soft reboot can use an already accepted patch.

A new trial must produce a Home acknowledgement for the new restart
generation and namespace. The watcher attempts to restart with the previous
accepted script, or the bundled script, if the trial fails. If it cannot
safely restart the guest it uses the existing return to One UI. The host
records the sequence before starting the trial, so a failed or interrupted
trial cannot be replayed.

## Bootstrap and keys

The host bundles and hash-checks the guest APK. Both the guest UI and the
host verifier use the patch public key built into that APK. Builds without a
pin have no active patch feed. Use distinct Ed25519 key pairs for debug and
release. Keep private keys offline or in a scoped signing environment.
Set the base64 X.509 DER public keys in `ONEOS_PATCH_DEBUG_PUBLIC_KEY` and
`ONEOS_PATCH_RELEASE_PUBLIC_KEY` when building the guest APK. The build
workflow reads these from repository variables. The debug test uses only the
debug key.

Install the new host APK in One UI and finish setup. The host installs the
bundled guest app. **Settings → Advanced → Download signed guest patches
automatically** defaults off. When enabled, the guest checks automatically
when its OneOS app opens. Manual checks under **Guest app updates → Signed
system patches** work with that toggle off. Downloads never restart the guest.

## Debug marker patch

For a CI-built smoke test, put the base64 PKCS#8 DER **debug** signing key in
the `ONEOS_PATCH_DEBUG_PRIVATE_KEY_B64` secret in the `debug-build` GitHub
Actions environment. Keep the key in your private signing storage and use
the environment's existing branch restrictions. Actions derives the pin for
the debug APK and creates the `oneos-debug-signed-patch` artifact. It leaves
feed publication to the maintainer. Set repository variable
`ONEOS_PATCH_SMOKE_IMAGE` if the test phone uses a different image and
`ONEOS_PATCH_SMOKE_SEQUENCE` if that exact target has already seen a patch
with sequence 1. This step does not load the debug signing key into a release
build.

For offline signing, the build can also use a public pin alone.

Use the exact `guest_start.sh` and host build from the bootstrap source. Read the
image ID and raw SHA-256 from the booted stage or from the source
`image_raw` table. On this S23, verify which ColorOS image was prepared
before using the example. For `coloros17-findx9u-in-1105` the raw image hash
in the raw-image table is
`6c089f2d043d60f34b40dd58ad44f88621163163c1664121753311a115da8d7c`.

From the bootstrap checkout with a JDK, use a private signing directory
containing `private.der` (PKCS#8) and the matching `public.der` (X.509).
Generate a fresh sequence above the highest value already published for this
exact build, image and base script.

```sh
set -eu
work=$(mktemp -d)
base=switcher/assets/guest_start.sh
image=coloros17-findx9u-in-1105
image_sha=6c089f2d043d60f34b40dd58ad44f88621163163c1664121753311a115da8d7c
sequence=1
build=$(sed -n 's/^ *versionCode = \([0-9][0-9]*\) *$/\1/p' app-android/app/build.gradle.kts)
# Set PATCH_KEYS to the private directory for the pinned debug key.
{
  head -n 1 "$base"
  printf '%s\n' "echo '[OneOS patch] signed restart smoke test'"
  tail -n +2 "$base"
} > "$work/guest_start.sh"
javac -d "$work/classes" \
  app-android/patch-protocol/src/main/java/com/fahadali/oneos/patch/GuestStartPatch.java \
  scripts/SignGuestStartPatch.java
java -cp "$work/classes" SignGuestStartPatch debug "$build" "$image" "$image_sha" \
  "$sequence" "$base" "$work/guest_start.sh" "$PATCH_KEYS" "$work/guest-start.patch"
```

For debug build 165 and later, publish the signed file as
`<host build>/<image ID>/guest-start.patch` on the `oneos-patch-feed-debug`
branch. Earlier debug builds read `guest-start.patch` at the branch root.
Release reads `guest-start.patch` at the root of `oneos-patch-feed-release`.
Keep the debug marker separate from release and leave Gallery and audio
changes out of this test.

In the guest, check for a patch and queue it. Choose **Restart guest** and
wait for Home. After returning to One UI, inspect the startup marker and the
host acceptance log:

```sh
su -c 'grep -F "[OneOS patch] signed restart smoke test" /data/local/tmp/oneos/data/takeover.log'
su -c 'grep -F "OneOS patch:" /data/local/tmp/oneos/auto_return.log | tail -n 10'
```

A marker proves the script started. The separately logged acceptance proves
that Home was confirmed for that trial. Test rollback with a separate debug
fixture and leave the release feed unmodified.
