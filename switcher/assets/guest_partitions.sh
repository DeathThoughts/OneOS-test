#!/system/bin/sh
# Optional raw partition images. No flash, init service start, or kernel/module load.
# Hardware partitions are inspection-only until a Samsung-specific service design exists.
oneos_parts_error() { echo "ERROR: guest partitions: $*" >&2; return 1; }
oneos_parts_file() {
    [ -f "$1" ] && [ ! -L "$1" ] &&
        [ "$(stat -c %u "$1")" = 0 ] || return 1
    case "$(stat -c %a "$1")" in 400|444|600|644) ;; *) return 1 ;; esac
}
oneos_parts_validate() {
    OP_DIR="$STAGE/partition_images"
    [ ! -e "$OP_DIR" ] && [ ! -L "$OP_DIR" ] && return 0
    [ "$(readlink -f "$STAGE")" = "$STAGE" ] &&
        [ -d "$OP_DIR" ] && [ ! -L "$OP_DIR" ] &&
        [ "$(stat -c %u "$OP_DIR")" = 0 ] || { oneos_parts_error "unsafe image directory"; return 1; }
    case "$(stat -c %a "$OP_DIR")" in 500|555|700|755) ;; *) oneos_parts_error "image directory is writable by others"; return 1 ;; esac
    oneos_parts_file "$OP_DIR/manifest" || { oneos_parts_error "unsafe manifest"; return 1; }
    # Parse data only: never source a manifest or allow a manifest-supplied path.
    awk '
        NR == 1 { if (NF != 2 || $1 != "oneos-partitions-v1" || length($2) != 64 || $2 ~ /[^0-9a-f]/) bad=1; next }
        NF != 5 { bad=1; next }
        $1 !~ /^(product|system_ext|vendor|odm|vendor_dlkm|odm_dlkm|system_dlkm)$/ { bad=1 }
        seen[$1]++ { bad=1 }
        $2 !~ /^(ext4|erofs)$/ || length($3) != 64 || $3 ~ /[^0-9a-f]/ || $4 !~ /^[1-9][0-9]*$/ { bad=1 }
        $5 !~ /^(activate|inspect)$/ { bad=1 }
        $5 == "activate" && $1 !~ /^(product|system_ext)$/ { bad=1 }
        END { if (NR < 2) bad=1; exit bad }
    ' "$OP_DIR/manifest" || { oneos_parts_error "invalid manifest or hardware partition activation requested"; return 1; }
    oneos_parts_file "$STAGE/system.raw" || { oneos_parts_error "unsafe system.raw"; return 1; }
    OP_BASE=$(awk 'NR == 1 {print $2}' "$OP_DIR/manifest")
    [ "$(sha256sum "$STAGE/system.raw" | cut -d ' ' -f1)" = "$OP_BASE" ] || { oneos_parts_error "bundle belongs to another system.raw"; return 1; }
    while read -r OP_NAME OP_FS OP_HASH OP_SIZE OP_MODE; do
        [ "$OP_NAME" = oneos-partitions-v1 ] && continue
        OP_FILE="$OP_DIR/$OP_NAME.img"
        oneos_parts_file "$OP_FILE" &&
            [ "$(stat -c %s "$OP_FILE")" = "$OP_SIZE" ] &&
            [ "$(sha256sum "$OP_FILE" | cut -d ' ' -f1)" = "$OP_HASH" ] || {
                oneos_parts_error "$OP_NAME image failed ownership, size or SHA-256 verification"; return 1;
            }
        # Probe, never auto-detect a filesystem at mount time (no sparse/super/boot).
        [ "$("$BB" blkid "$OP_FILE" 2>/dev/null | sed -n 's/.* TYPE="\([^" ]*\)".*/\1/p')" = "$OP_FS" ] || {
            oneos_parts_error "$OP_NAME is not a raw $OP_FS filesystem"; return 1;
        }
    done < "$OP_DIR/manifest"
}
oneos_parts_target() {
    # Resolve only the two known GSI layouts. Never follow an absolute image
    # link in init's namespace: /product there is One UI's real partition.
    OP_TARGET="$ROOTFS/$1"
    if [ -L "$OP_TARGET" ]; then
        [ "$(readlink "$OP_TARGET")" = "/system/$1" ] || return 1
        OP_TARGET="$ROOTFS/system/$1"
    elif [ -L "$ROOTFS/system/$1" ]; then
        [ "$(readlink "$ROOTFS/system/$1")" = "/$1" ] || return 1
    elif [ -d "$ROOTFS/system/$1" ]; then
        OP_TARGET="$ROOTFS/system/$1"
    fi
    [ -d "$OP_TARGET" ] && [ ! -L "$OP_TARGET" ] &&
        [ "$(readlink -f "$OP_TARGET")" = "$OP_TARGET" ]
}
oneos_parts_rollback() {
    for OP_T in $OP_MOUNTED; do "$BB" umount "$OP_T" || return 1; done
    for OP_L in $OP_LOOPS; do "$BB" losetup -d "$OP_L" || return 1; done
    OP_MOUNTED=; OP_LOOPS=
}
oneos_parts_mount() {
    oneos_parts_validate || return 1
    [ -f "$OP_DIR/manifest" ] || return 0
    OP_MOUNTED=; OP_LOOPS=
    [ "$(readlink -f "$ROOTFS")" = "$ROOTFS" ] || return 1
    # This is a mountpoint created for system.raw, never the host root.
    awk -v p="$ROOTFS" '$5 == p {found=1} END {exit !found}' /proc/self/mountinfo || return 1
    "$BB" mount --make-rprivate "$ROOTFS" || return 1
    while read -r OP_NAME OP_FS OP_HASH OP_SIZE OP_MODE; do
        [ "$OP_MODE" = activate ] || continue
        oneos_parts_target "$OP_NAME" || { oneos_parts_rollback; oneos_parts_error "unsafe $OP_NAME target"; return 1; }
        if awk -v p="$OP_TARGET" '$5 == p || index($5,p "/") == 1 {found=1} END {exit !found}' /proc/self/mountinfo; then
            oneos_parts_rollback; oneos_parts_error "$OP_NAME already has mounts"; return 1
        fi
        OP_LOOP=$("$BB" losetup -f) &&
            "$BB" losetup -r "$OP_LOOP" "$OP_DIR/$OP_NAME.img" || {
                oneos_parts_rollback; oneos_parts_error "cannot attach $OP_NAME read-only"; return 1;
            }
        OP_LOOPS="$OP_LOOP $OP_LOOPS"
        OP_DEV=$(stat -L -c '%t %T' "$OP_LOOP")
        set -- $OP_DEV
        [ "$#" = 2 ] && [ "$(cat "/sys/dev/block/$(printf '%d:%d' "0x$1" "0x$2")/ro" 2>/dev/null)" = 1 ] || {
            oneos_parts_rollback; oneos_parts_error "loop read-only flag not confirmed"; return 1;
        }
        OP_OPTIONS=ro,nodev,nosuid
        [ "$OP_FS" = ext4 ] && OP_OPTIONS="$OP_OPTIONS,noload"
        "$BB" mount -t "$OP_FS" -o "$OP_OPTIONS" "$OP_LOOP" "$OP_TARGET" || {
            oneos_parts_rollback; oneos_parts_error "cannot mount $OP_NAME"; return 1;
        }
        OP_MOUNTED="$OP_TARGET $OP_MOUNTED"
        "$BB" mount --make-private "$OP_TARGET" || { oneos_parts_rollback; return 1; }
        echo "Guest partition: $OP_NAME at ${OP_TARGET#"$ROOTFS"} (read-only)"
    done < "$OP_DIR/manifest"
}
