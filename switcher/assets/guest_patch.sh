#!/system/bin/sh
# The host's restart watcher is the only caller that can consume or promote a patch.
# The guest can write a candidate and read target settings, but it cannot select a script.
set -eu
S=$1
command=$2
session="$S/tmp/guest-patch-session"
base="$S/guest_start.sh"
apk="$S/guest_manager.apk"
tool=com.fahadali.oneos.guest.GuestPatchTool

private_dir() {
    [ ! -L "$1" ] || return 1
    (umask 077; mkdir -p "$1") &&
        [ -d "$1" ] && [ "$(stat -c '%u:%g:%a' "$1")" = 0:0:700 ]
}

run_tool() {
    expected=$(cat "$session/apk.sha256")
    [ -f "$apk" ] && [ ! -L "$apk" ] && [ "$(stat -c '%u:%g' "$apk")" = 0:0 ] &&
        [ "$(sha256sum "$apk" | cut -d ' ' -f 1)" = "$expected" ] || return 1
    BOOTCLASSPATH=${ONEOS_HOST_BOOTCLASSPATH:-${BOOTCLASSPATH:-}} \
        DEX2OATBOOTCLASSPATH=${ONEOS_HOST_DEX2OATBOOTCLASSPATH:-${DEX2OATBOOTCLASSPATH:-}} \
        CLASSPATH="$apk" timeout 12 /system/bin/app_process /system/bin "$tool" "$@"
}

publish_target() {
    auto=$(cat "$session/config")
    floor=0
    if [ -f "$state/floor" ]; then floor=$(cat "$state/floor"); fi
    case "$floor" in ''|*[!0-9]*) return 1 ;; esac
    dst="$S/data/oneos_bootui"
    [ -d "$dst" ] && [ ! -L "$dst" ] || return 1
    temp=$(mktemp "$dst/.signed-patch-XXXXXX") || return 1
    if cat "$state/target" > "$temp" && printf '%s %s\n' "$auto" "$floor" >> "$temp" &&
        chmod 644 "$temp" && mv -f "$temp" "$dst/signed_patch"; then
        return 0
    fi
    rm -f "$temp"
    return 1
}

case "$command" in
configure)
    [ "$#" = 3 ] || exit 1
    auto=$3
    case "$auto" in 0|1) ;; *) exit 1 ;; esac
    private_dir "$session" || exit 1
    rm -f "$session/scope" "$session/config"
    expected=$(awk '$2 == "guest_manager.apk" {print $1}' "$S/files.sha256")
    [ "${#expected}" = 64 ] || exit 1
    printf '%s\n' "$expected" > "$session/apk.sha256"
    channel=$(run_tool channel) || exit 1
    case "$channel" in debug|release) ;; *) exit 1 ;; esac
    code=$(sed -n 's/^  "versionCode": \([0-9][0-9]*\),$/\1/p' "$S/.oneos_stage")
    image=$(sed -n 's/^  "image": "\([a-z0-9._-]*\)",$/\1/p' "$S/.oneos_stage")
    image_hash=$(awk 'NR == 1 {print $2}' "$S/system.raw.id")
    base_hash=$(awk '$2 == "guest_start.sh" {print $1}' "$S/files.sha256")
    [ -n "$code" ] && [ -n "$image" ] && [ "${#image_hash}" = 64 ] &&
        [ "${#base_hash}" = 64 ] && [ "$(sha256sum "$base" | cut -d ' ' -f 1)" = "$base_hash" ] || exit 1
    target="$channel $code $image $image_hash $base_hash"
    scope=$(printf '%s\n' "$target" | sha256sum | cut -d ' ' -f 1)
    # The floor survives replacing or resetting a stage. Debug/release and each
    # exact target have separate state. One UI's root manager owns this parent.
    store=/data/adb/oneos-signed-patches
    state="$store/$scope"
    private_dir "$store" && private_dir "$state" || exit 1
    if [ ! -e "$state/target" ]; then
        (umask 077; printf '%s\n' "$target" > "$state/target")
    fi
    [ "$(cat "$state/target")" = "$target" ] || exit 1
    run_tool floor "$state" >/dev/null || exit 1
    printf '%s\n' "$scope" > "$session/scope"
    printf '%s\n' "$auto" > "$session/config"
    ;;
publish|select|accept)
    private_dir "$session" && [ -f "$session/scope" ] || exit 1
    scope=$(cat "$session/scope")
    [ "${#scope}" = 64 ] || exit 1
    case "$scope" in *[!0-9a-f]*) exit 1 ;; esac
    private_dir /data/adb/oneos-signed-patches || exit 1
    state="/data/adb/oneos-signed-patches/$scope"
    private_dir "$state" || exit 1
    case "$command" in
        publish)
            publish_target
            ;;
        select)
            # Only an explicit app restart may consume a new candidate. A
            # KernelSU restart or rollback can select only an accepted script.
            if [ "${3:-0}" = 1 ]; then
                input="$S/data/user/0/com.fahadali.oneos.guest/files/oneos-patch.pending"
                selection=$(run_tool select "$state" "$input") || exit 1
            else
                selection=$(run_tool select "$state") || exit 1
            fi
            if [ "$selection" = trial ] || [ "$selection" = known ]; then
                part=$(mktemp "$session/.run-XXXXXX")
                if ! cp "$state/run.sh" "$part" || ! /system/bin/sh -n "$part" || ! mv -f "$part" "$session/run.sh"; then
                    rm -f "$part"
                    exit 1
                fi
            fi
            if [ "$selection" = trial ]; then publish_target || true; fi
            # The sequence floor is consumption, not acceptance. Do not change the
            # stdout contract used by the restart watcher.
            echo "OneOS patch: restart selected $selection (floor=$(cat "$state/floor" 2>/dev/null || echo 0))" >&2
            printf '%s\n' "$selection"
            ;;
        accept)
            run_tool accept "$state"
            ;;
    esac
    ;;
*) exit 1 ;;
esac
