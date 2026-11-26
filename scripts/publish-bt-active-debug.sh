#!/bin/bash
# Publish only exact build-188 debug targets, using the existing debug signing key.
set -euo pipefail
: "${ONEOS_PATCH_DEBUG_PRIVATE_KEY_B64:?missing debug patch signing key}"
: "${ANDROID_HOME:?missing Android SDK}"
base_commit=69b9689c92079f96bf822f7783277eaa5969c04b
work=$(mktemp -d)
chmod 700 "$work"
trap 'git worktree remove --force "$work/feed" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT
git show "$base_commit:switcher/assets/guest_start.sh" > "$work/base.sh"
git show "$base_commit:app-android/app/build.gradle.kts" > "$work/gradle"
git show "$base_commit:app-android/app/src/full/assets/root/oneos_root.sh" > "$work/root.sh"
build=$(sed -n 's/^ *versionCode = \([0-9][0-9]*\) *$/\1/p' "$work/gradle")
[ "$build" = 188 ]
mkdir "$work/keys" "$work/classes" "$work/dex"
umask 077
printf '%s' "$ONEOS_PATCH_DEBUG_PRIVATE_KEY_B64" | base64 -d > "$work/keys/private.der"
unset ONEOS_PATCH_DEBUG_PRIVATE_KEY_B64
openssl pkey -inform DER -in "$work/keys/private.der" -pubout -outform DER > "$work/keys/public.der"
pin=$(base64 -w0 "$work/keys/public.der")
[ -z "${ONEOS_PATCH_DEBUG_PUBLIC_KEY:-}" ] || [ "$pin" = "$ONEOS_PATCH_DEBUG_PUBLIC_KEY" ]
[ "$pin" != "${ONEOS_PATCH_RELEASE_PUBLIC_KEY:-}" ]
aj=$(find "$ANDROID_HOME/platforms" -name android.jar | sort -V | tail -1)
d8=$(find "$ANDROID_HOME/build-tools" -name d8 | sort -V | tail -1)
javac --release 17 -classpath "$aj" -d "$work/classes" switcher/patches/OneOSBtActive.java
"$d8" --release --min-api 31 --lib "$aj" --output "$work/dex" "$work/classes"/OneOSBtActive*.class
python3 scripts/embed-bt-active-patch.py "$work/base.sh" "$work/dex/classes.dex" "$work/replacement.sh"
sh -n "$work/replacement.sh"
javac -d "$work/classes" app-android/patch-protocol/src/main/java/com/fahadali/oneos/patch/GuestStartPatch.java scripts/SignGuestStartPatch.java
git fetch origin refs/heads/oneos-patch-feed-debug
git worktree add --detach "$work/feed" FETCH_HEAD
basehash=$(sha256sum "$work/base.sh" | cut -d ' ' -f 1)
for image in coloros17-pyb110 coloros17-twinity4c coloros17-findx9u-in-1105 coloros17-findx9u-in coloros17-findn6; do
    raw=$(sed -n "s/^ *$image) echo \"[0-9][0-9]* \([0-9a-f]*\)\" ;;$/\1/p" "$work/root.sh")
    [ "${#raw}" = 64 ]
    dest="$work/feed/$build/$image/guest-start.patch"
    seq=2
    if [ -f "$dest" ]; then
        oldbase=$(sed -n '6p' "$dest")
        [ "$oldbase" = "$basehash" ] || { echo "Different base for $image, refusing overwrite"; exit 1; }
        oldseq=$(sed -n '7p' "$dest")
        [[ "$oldseq" =~ ^[0-9]{1,15}$ ]]
        seq=$((oldseq + 1))
    fi
    mkdir -p "$(dirname "$dest")"
    java -cp "$work/classes" SignGuestStartPatch debug "$build" "$image" "$raw" "$seq" "$work/base.sh" "$work/replacement.sh" "$work/keys" "$dest"
    echo "Signed $build/$image sequence $seq"
done
git -C "$work/feed" add "$build"
git -C "$work/feed" -c user.name='oneos-patch-ci' -c user.email='41898282+github-actions[bot]@users.noreply.github.com' commit -m 'Publish signed build 188 Bluetooth active-route recovery'
git -C "$work/feed" push origin HEAD:refs/heads/oneos-patch-feed-debug
