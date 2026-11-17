#!/bin/sh
# Sign a marker for this checkout. The signing key never enters the workspace.
set -eu
[ "$#" = 1 ] || { echo "usage: sign-debug-patch-smoke.sh OUTPUT_DIRECTORY" >&2; exit 2; }
out=$1
image=${ONEOS_PATCH_SMOKE_IMAGE:-coloros17-findx9u-in-1105}
sequence=${ONEOS_PATCH_SMOKE_SEQUENCE:-1}
case "$image" in ''|*[!a-z0-9-]*) echo "invalid smoke image ID" >&2; exit 1 ;; esac
: "${ONEOS_PATCH_DEBUG_PRIVATE_KEY_B64:?No debug patch signing key}"
umask 077
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 1' HUP INT TERM
printf '%s' "$ONEOS_PATCH_DEBUG_PRIVATE_KEY_B64" | base64 -d > "$work/private.der"
unset ONEOS_PATCH_DEBUG_PRIVATE_KEY_B64
openssl pkey -inform DER -in "$work/private.der" -pubout -outform DER > "$work/public.der"
pin=$(base64 -w0 "$work/public.der")
if [ -n "${ONEOS_PATCH_DEBUG_PUBLIC_KEY:-}" ] && [ "$pin" != "$ONEOS_PATCH_DEBUG_PUBLIC_KEY" ]; then
    echo "Debug patch signing key does not match the configured debug public key" >&2
    exit 1
fi
if [ "$pin" = "${ONEOS_PATCH_RELEASE_PUBLIC_KEY:-}" ]; then
    echo "Debug and release patches require distinct signing keys" >&2
    exit 1
fi
build=$(sed -n 's/^ *versionCode = \([0-9][0-9]*\) *$/\1/p' app-android/app/build.gradle.kts)
raw_hash=$(sed -n "s/^ *$image) echo \"[0-9][0-9]* \([0-9a-f]*\)\" ;;$/\1/p" \
    app-android/app/src/full/assets/root/oneos_root.sh)
[ "${#raw_hash}" = 64 ] || { echo "No exact raw image hash for $image" >&2; exit 1; }
base=switcher/assets/guest_start.sh
{
    head -n 1 "$base"
    printf '%s\n' "echo '[OneOS patch] signed restart smoke test'"
    tail -n +2 "$base"
} > "$work/guest_start.sh"
sh -n "$work/guest_start.sh"
javac -d "$work/classes" \
    app-android/patch-protocol/src/main/java/com/fahadali/oneos/patch/GuestStartPatch.java \
    scripts/SignGuestStartPatch.java
mkdir -p "$out"
java -cp "$work/classes" SignGuestStartPatch debug "$build" "$image" "$raw_hash" \
    "$sequence" "$base" "$work/guest_start.sh" "$work" "$out/guest-start.patch"
printf '%s\n' "$pin" > "$out/public-key.b64"
printf 'Debug marker signed for build %s, image %s, sequence %s\n' "$build" "$image" "$sequence"
