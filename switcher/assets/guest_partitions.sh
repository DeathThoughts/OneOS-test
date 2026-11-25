#!/system/bin/sh
# Optional raw partition images. No flash, init service start, or kernel/module load.
# Donor hardware activation stays blocked. Stock mode is pinned to the audited S23 dump.
oneos_parts_error() { echo "ERROR: guest partitions: $*" >&2; return 1; }
# Probe bytes explicitly: some Android BusyBox builds do not recognize EROFS in blkid.
oneos_magic() { od -An -tx1 -j "$2" -N "$3" "$1" 2>/dev/null | tr -d '[:space:]'; }
oneos_image_fs() {
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    case "$(oneos_magic "$1" 0 4)" in ''|3aff26ed|414e4452|564e4452) return 1 ;; esac
    case "$(oneos_magic "$1" 1024 4):$(oneos_magic "$1" 1080 2)" in
        e2e1f5e0:53ef) return 1 ;;
        e2e1f5e0:????) echo erofs ;;
        *:53ef) echo ext4 ;;
        *) return 1 ;;
    esac
}
# Sorted render rates for the built-in display driving display 0. Prefer
# DisplayManager's base record. Other logical displays (DeX, casts) can list
# valid modes for an entirely different display. Read fps, never the vsync clock.
oneos_panel_rates() {
    awk '
        /mBaseDisplayInfo=DisplayInfo\{/ && /displayId 0,/ && /type INTERNAL[,}]/ { base=$0 }
        /DisplayDeviceInfo\{/ && /type INTERNAL[,}]/ && /state ON[,}]/ {
            if (!seen[$0]++) { devices++; device=$0 }
        }
        /^ *supportedModes \[/ { legacy=$0; legacy_count++ }
        END {
            line=base
            if (!line && devices==1) line=device
            if (!line && !devices && legacy_count==1) line=legacy
            if (!sub(/^.*supportedModes[= ]+\[/, "", line)) exit
            # Child arrays can contain brackets. The list ends after the final mode object.
            if (match(line, /\}[[:space:]]*\]/)) line=substr(line,1,RSTART)
            else exit
            n=split(line, modes, /\}[[:space:]]*,[[:space:]]*\{/)
            for (i=1;i<=n;i++) {
                if (!match(modes[i], /(^|[ ,{])fps=[0-9]+([.][0-9]+)?([,} ]|$)/)) continue
                s=substr(modes[i],RSTART,RLENGTH); sub(/^.*fps=/,"",s)
                hz=int(s+0.5); if (hz>=30 && hz<=120) rates[hz]=1
            }
            for (hz=30;hz<=120;hz++) if (hz in rates) print hz
        }' "$1"
}
oneos_parts_file() {
    [ -f "$1" ] && [ ! -L "$1" ] &&
        [ "$(stat -c %u "$1")" = 0 ] || return 1
    case "$(stat -c %a "$1")" in 400|444|600|644) ;; *) return 1 ;; esac
}
# A first live vendor-image path: the owner's audited S23 image, byte-identical
# to the running host partition. This does not introduce OPPO services/drivers.
oneos_parts_vendor_profile() {
    [ "$1:$2:$3" = "erofs:3ace2530806e1eeba86007eab46a4fbebe607caf04ea73ce6cbb2ec6a17c63e7:1907937280" ]
}
oneos_parts_vendor_host() {
    oneos_parts_vendor_profile "$1" "$2" "$3" || { oneos_parts_error "unknown stock vendor profile"; return 1; }
    [ "$(getprop ro.vendor.build.fingerprint)" = 'samsung/dm1qxxx/dm1q:13/TP1A.220624.014/S911BXXUAGZIF:user/release-keys' ] &&
        [ "$(getprop ro.product.vendor.model)" = SM-S911B ] &&
        [ "$(getprop ro.board.platform)" = kalama ] || { oneos_parts_error "stock vendor device/build mismatch"; return 1; }
    # Read the device actually mounted at /vendor, never a guessed slot/name.
    # Refuse overlays or multiple mounts at that root rather than guessing.
    OP_HOST_VENDOR=$(awk '
        $5 == "/vendor" {
            count++
            for (i=7; i<=NF; i++) if ($i == "-") {
                if ($4 != "/" || $(i+1) != "erofs") bad=1
                dev=$(i+2); mm=$3
            }
        }
        END { if (count != 1 || bad || dev == "") exit 1; print mm, dev }
    ' /proc/self/mountinfo) || { oneos_parts_error "cannot identify plain live EROFS vendor"; return 1; }
    set -- $OP_HOST_VENDOR
    [ "$#" = 2 ] || return 1
    OP_HOST_MM=$1; OP_HOST_DEV=$2
    case "$OP_HOST_DEV" in /dev/block/*) ;; *) oneos_parts_error "vendor is not a block-device source"; return 1 ;; esac
    "$BB" test -b "$OP_HOST_DEV" || return 1
    OP_HOST_HEX=$(stat -L -c '%t %T' "$OP_HOST_DEV")
    set -- $OP_HOST_HEX
    [ "$#" = 2 ] && [ "$(printf '%d:%d' "0x$1" "0x$2")" = "$OP_HOST_MM" ] || return 1
    [ "$("$BB" blockdev --getsize64 "$OP_HOST_DEV")" = "$OP_SIZE" ] &&
        [ "$(sha256sum "$OP_HOST_DEV" | cut -d ' ' -f1)" = "$OP_HASH" ] || {
            oneos_parts_error "live vendor bytes differ from the supplied stock image"; return 1;
        }
    # /vendor includes firmware and sometimes module mounts. Preserve every
    # live submount, not just the EROFS bytes, with canonical guest targets.
    OP_VENDOR_CHILDREN=$(awk '$5 ~ /^\/vendor\// {print $5}' /proc/self/mountinfo | LC_ALL=C sort -u)
    OP_COUNT=0
    for OP_CHILD in $OP_VENDOR_CHILDREN; do
        OP_COUNT=$((OP_COUNT + 1))
        case "$OP_CHILD" in *[!A-Za-z0-9_./+-]*|*/../*|*/./*|*/..) oneos_parts_error "unsafe live vendor mount path"; return 1 ;; esac
        [ "$OP_COUNT" -le 256 ] || { oneos_parts_error "too many vendor submounts"; return 1; }
    done
}
oneos_parts_vendor_children() {
    for OP_CHILD in $OP_VENDOR_CHILDREN; do
        OP_DEST="$ROOTFS$OP_CHILD"
        [ -e "$OP_DEST" ] && [ ! -L "$OP_DEST" ] &&
            [ "$(readlink -f "$OP_DEST")" = "$OP_DEST" ] || {
                oneos_parts_error "vendor submount has no safe guest target: $OP_CHILD"; return 1;
            }
        # An rbind of an ancestor already carries descendants. Mount each top
        # subtree once, and isolate it immediately before any failure cleanup.
        OP_COVERED=0
        for OP_PRIOR in $OP_VENDOR_BOUND; do
            case "$OP_CHILD/" in "$OP_PRIOR/"*) OP_COVERED=1 ;; esac
        done
        [ "$OP_COVERED" = 0 ] || continue
        if "$BB" test -d "$OP_CHILD" && [ -d "$OP_DEST" ]; then OP_BIND=--rbind
        elif "$BB" test -f "$OP_CHILD" && [ -f "$OP_DEST" ]; then OP_BIND=--bind
        else oneos_parts_error "vendor submount type changed: $OP_CHILD"; return 1; fi
        "$BB" mount "$OP_BIND" "$OP_CHILD" "$OP_DEST" || return 1
        OP_MOUNTED="$OP_DEST $OP_MOUNTED"
        "$BB" mount --make-rslave "$OP_DEST" || return 1
        "$BB" mount --make-rprivate "$OP_DEST" || return 1
        OP_VENDOR_BOUND="$OP_CHILD $OP_VENDOR_BOUND"
    done
}
oneos_parts_odm_profile() {
    [ "$1:$2:$3" = "erofs:1c1e9e3f95fa3e635dcfbb63e37a76fc40f60014b6a6bc8c8ed7e1457d29f4de:778240" ]
}
oneos_parts_odm_host() {
    oneos_parts_odm_profile "$1" "$2" "$3" || { oneos_parts_error "unknown stock odm profile"; return 1; }
    [ "$(getprop ro.odm.build.fingerprint)" = 'samsung/dm1qxxx/dm1q:13/TP1A.220624.014/S911BXXUAGZIF:user/release-keys' ] &&
        [ "$(getprop ro.product.odm.model)" = SM-S911B ] &&
        [ "$(getprop ro.board.platform)" = kalama ] || { oneos_parts_error "stock odm device/build mismatch"; return 1; }
    # Read the device actually mounted at /odm, never a guessed slot/name.
    # Refuse overlays or multiple mounts at that root rather than guessing.
    OP_HOST_ODM=$(awk '
        $5 == "/odm" {
            count++
            for (i=7; i<=NF; i++) if ($i == "-") {
                if ($4 != "/" || $(i+1) != "erofs") bad=1
                dev=$(i+2); mm=$3
            }
        }
        END { if (count != 1 || bad || dev == "") exit 1; print mm, dev }
    ' /proc/self/mountinfo) || { oneos_parts_error "cannot identify plain live EROFS odm"; return 1; }
    set -- $OP_HOST_ODM
    [ "$#" = 2 ] || return 1
    OP_HOST_MM=$1; OP_HOST_DEV=$2
    case "$OP_HOST_DEV" in /dev/block/*) ;; *) oneos_parts_error "odm is not a block-device source"; return 1 ;; esac
    "$BB" test -b "$OP_HOST_DEV" || return 1
    OP_HOST_HEX=$(stat -L -c '%t %T' "$OP_HOST_DEV")
    set -- $OP_HOST_HEX
    [ "$#" = 2 ] && [ "$(printf '%d:%d' "0x$1" "0x$2")" = "$OP_HOST_MM" ] || return 1
    [ "$("$BB" blockdev --getsize64 "$OP_HOST_DEV")" = "$OP_SIZE" ] &&
        [ "$(sha256sum "$OP_HOST_DEV" | cut -d ' ' -f1)" = "$OP_HASH" ] || {
            oneos_parts_error "live odm bytes differ from the supplied stock image"; return 1;
        }
    # /odm includes firmware and sometimes module mounts. Preserve every
    # live submount, not just the EROFS bytes, with canonical guest targets.
    OP_ODM_CHILDREN=$(awk '$5 ~ /^\/odm\// {print $5}' /proc/self/mountinfo | LC_ALL=C sort -u)
    OP_COUNT=0
    for OP_CHILD in $OP_ODM_CHILDREN; do
        OP_COUNT=$((OP_COUNT + 1))
        case "$OP_CHILD" in *[!A-Za-z0-9_./+-]*|*/../*|*/./*|*/..) oneos_parts_error "unsafe live odm mount path"; return 1 ;; esac
        [ "$OP_COUNT" -le 256 ] || { oneos_parts_error "too many odm submounts"; return 1; }
    done
}
oneos_parts_odm_children() {
    for OP_CHILD in $OP_ODM_CHILDREN; do
        OP_DEST="$ROOTFS$OP_CHILD"
        [ -e "$OP_DEST" ] && [ ! -L "$OP_DEST" ] &&
            [ "$(readlink -f "$OP_DEST")" = "$OP_DEST" ] || {
                oneos_parts_error "odm submount has no safe guest target: $OP_CHILD"; return 1;
            }
        # An rbind of an ancestor already carries descendants. Mount each top
        # subtree once, and isolate it immediately before any failure cleanup.
        OP_COVERED=0
        for OP_PRIOR in $OP_ODM_BOUND; do
            case "$OP_CHILD/" in "$OP_PRIOR/"*) OP_COVERED=1 ;; esac
        done
        [ "$OP_COVERED" = 0 ] || continue
        if "$BB" test -d "$OP_CHILD" && [ -d "$OP_DEST" ]; then OP_BIND=--rbind
        elif "$BB" test -f "$OP_CHILD" && [ -f "$OP_DEST" ]; then OP_BIND=--bind
        else oneos_parts_error "odm submount type changed: $OP_CHILD"; return 1; fi
        "$BB" mount "$OP_BIND" "$OP_CHILD" "$OP_DEST" || return 1
        OP_MOUNTED="$OP_DEST $OP_MOUNTED"
        "$BB" mount --make-rslave "$OP_DEST" || return 1
        "$BB" mount --make-rprivate "$OP_DEST" || return 1
        OP_ODM_BOUND="$OP_CHILD $OP_ODM_BOUND"
    done
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
        $5 !~ /^(activate|inspect|stock)$/ { bad=1 }
        $5 == "activate" && $1 !~ /^(product|system_ext)$/ { bad=1 }
        $5 == "stock" && $1 !~ /^(vendor|odm)$/ { bad=1 }
        END { if (NR < 2) bad=1; exit bad }
    ' "$OP_DIR/manifest" || { oneos_parts_error "invalid manifest or hardware partition activation requested"; return 1; }
    oneos_parts_file "$STAGE/system.raw" || { oneos_parts_error "unsafe system.raw"; return 1; }
    OP_BASE=$(awk 'NR == 1 {print $2}' "$OP_DIR/manifest")
    [ "$(sha256sum "$STAGE/system.raw" | cut -d ' ' -f1)" = "$OP_BASE" ] || { oneos_parts_error "bundle belongs to another system.raw"; return 1; }
    while read -r OP_NAME OP_FS OP_HASH OP_SIZE OP_MODE || [ -n "$OP_NAME" ]; do
        [ "$OP_NAME" = oneos-partitions-v1 ] && continue
        OP_FILE="$OP_DIR/$OP_NAME.img"
        oneos_parts_file "$OP_FILE" &&
            [ "$(stat -c %s "$OP_FILE")" = "$OP_SIZE" ] &&
            [ "$(sha256sum "$OP_FILE" | cut -d ' ' -f1)" = "$OP_HASH" ] || {
                oneos_parts_error "$OP_NAME image failed ownership, size or SHA-256 verification"; return 1;
            }
        # Probe, never auto-detect a filesystem at mount time (no sparse/super/boot).
        OP_TYPE=$(oneos_image_fs "$OP_FILE")
        OP_BLKTYPE=$("$BB" blkid "$OP_FILE" 2>/dev/null | sed -n 's/.* TYPE="\([^" ]*\)".*/\1/p')
        [ "$OP_TYPE" = "$OP_FS" ] && { [ -z "$OP_BLKTYPE" ] || [ "$OP_BLKTYPE" = "$OP_FS" ]; } || {
            oneos_parts_error "$OP_NAME is not a raw $OP_FS filesystem"; return 1;
        }
        if [ "$OP_MODE" = stock ]; then
            oneos_parts_${OP_NAME}_host "$OP_FS" "$OP_HASH" "$OP_SIZE" || return 1
        fi
    done < "$OP_DIR/manifest"
}
oneos_parts_target() {
    # Resolve only the two known GSI layouts. Never follow an absolute image
    # link in init's namespace: /product there is One UI's real partition.
    OP_TARGET="$ROOTFS/$1"
    if [ "$1" = vendor ] || [ "$1" = odm ]; then
        # The Samsung vendor view must be /vendor, never an unrelated second
        # real directory at /system/vendor in a donor image.
        [ -d "$OP_TARGET" ] && [ ! -L "$OP_TARGET" ] &&
            [ "$(readlink -f "$OP_TARGET")" = "$OP_TARGET" ]
        return $?
    fi
    case "$1" in product|system_ext) ;; *) return 1 ;; esac
    [ ! -L "$ROOTFS/system" ] || return 1
    OP_NESTED="$ROOTFS/system/$1"
    if [ -L "$OP_TARGET" ]; then
        case "$(readlink "$OP_TARGET")" in "/system/$1"|"system/$1") ;; *) return 1 ;; esac
        OP_TARGET="$OP_NESTED"
    elif [ -L "$OP_NESTED" ]; then
        case "$(readlink "$OP_NESTED")" in "/$1"|"../$1") ;; *) return 1 ;; esac
    elif [ -d "$OP_NESTED" ]; then
        # OriginOS overlays an empty top-level system_ext before binding the
        # completed nested tree there. Otherwise retain each runtime default.
        if [ ! -d "$OP_TARGET" ] || [ "$1" = product ] || [ "${SEXT_MOUNT:-0}" = 1 ]; then
            OP_TARGET="$OP_NESTED"
        fi
    fi
    [ -d "$OP_TARGET" ] && [ ! -L "$OP_TARGET" ] &&
        [ "$(readlink -f "$OP_TARGET")" = "$OP_TARGET" ]
}
oneos_parts_rollback() {
    for OP_T in $OP_MOUNTED; do
        # A just-created rbind can still be a peer of a host shared mount.
        # Never unmount it after a failed propagation change unless isolation succeeds.
        "$BB" mount --make-rprivate "$OP_T" || return 1
        OP_DESCENDANTS=$(awk -v p="$OP_T/" 'index($5,p) == 1 {print $5}' /proc/self/mountinfo | LC_ALL=C sort -ru)
        for OP_SUB in $OP_DESCENDANTS; do
            case "$OP_SUB" in "$OP_T"/*) "$BB" umount "$OP_SUB" || return 1 ;; *) return 1 ;; esac
        done
        "$BB" umount "$OP_T" || return 1
    done
    for OP_L in $OP_LOOPS; do "$BB" losetup -d "$OP_L" || return 1; done
    OP_MOUNTED=; OP_LOOPS=
}
oneos_parts_mount() {
    OP_VENDOR_ACTIVE=0
    OP_ODM_ACTIVE=0
    oneos_parts_validate || return 1
    [ -f "$OP_DIR/manifest" ] || return 0
    OP_MOUNTED=; OP_LOOPS=; OP_VENDOR_BOUND=; OP_ODM_BOUND=
    [ "$(readlink -f "$ROOTFS")" = "$ROOTFS" ] || return 1
    # This is a mountpoint created for system.raw, never the host root.
    awk -v p="$ROOTFS" '$5 == p {found=1} END {exit !found}' /proc/self/mountinfo || return 1
    "$BB" mount --make-rprivate "$ROOTFS" || return 1
    while read -r OP_NAME OP_FS OP_HASH OP_SIZE OP_MODE || [ -n "$OP_NAME" ]; do
        case "$OP_MODE" in activate|stock) ;; *) continue ;; esac
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
        if [ "$OP_MODE" = stock ]; then
            oneos_parts_${OP_NAME}_children || { oneos_parts_rollback; return 1; }
            case "$OP_NAME" in vendor) OP_VENDOR_ACTIVE=1 ;; odm) OP_ODM_ACTIVE=1 ;; esac
        fi
        echo "Guest partition: $OP_NAME at ${OP_TARGET#"$ROOTFS"} (read-only; mode $OP_MODE)"
    done < "$OP_DIR/manifest"
}

# Developer-only vendor inspection. Run from a freshly unshared mount namespace,
# never from switch/prepare. It does not replace the guest or host /vendor.
oneos_parts_inspect_vendor() (
    OP_SELF_NS=$(readlink /proc/self/ns/mnt 2>/dev/null)
    OP_INIT_NS=$(readlink /proc/1/ns/mnt 2>/dev/null)
    [ -n "$OP_SELF_NS" ] && [ -n "$OP_INIT_NS" ] && [ "$OP_SELF_NS" != "$OP_INIT_NS" ] || {
        oneos_parts_error "vendor inspection requires a separate mount namespace"; exit 1;
    }
    oneos_parts_validate || exit 1
    OP_RECORD=$(awk '$1 == "vendor" && $5 == "inspect" {print $2}' "$OP_DIR/manifest" 2>/dev/null)
    case "$OP_RECORD" in ext4|erofs) ;; *) oneos_parts_error "no inspection-only vendor image"; exit 1 ;; esac
    # All following mounts disappear with this namespace. Only our loop is
    # detached, never an enumerated host loop, service, driver or real partition.
    "$BB" mount --make-rprivate / || exit 1
    OP_INSPECT="$STAGE/vendor_inspection"
    [ ! -L "$OP_INSPECT" ] && [ ! -e "$OP_INSPECT" ] || {
        oneos_parts_error "inspection path already exists; inspect and remove it while idle"; exit 1;
    }
    mkdir -m 700 "$OP_INSPECT" || exit 1
    OP_INSPECT_LOOP=; OP_INSPECT_TOP=0; OP_INSPECT_IMAGE=0
    oneos_parts_inspect_cleanup() {
        OP_CLEAN_OK=1
        cd "$STAGE" || return 1
        if [ "$OP_INSPECT_IMAGE" = 1 ]; then
            "$BB" umount "$OP_INSPECT/root" || OP_CLEAN_OK=0
        fi
        if [ "$OP_CLEAN_OK" = 1 ] && [ -n "$OP_INSPECT_LOOP" ]; then
            "$BB" losetup -d "$OP_INSPECT_LOOP" || OP_CLEAN_OK=0
        fi
        if [ "$OP_INSPECT_TOP" = 1 ]; then
            "$BB" umount "$OP_INSPECT" || OP_CLEAN_OK=0
        fi
        rmdir "$OP_INSPECT" 2>/dev/null || OP_CLEAN_OK=0
        [ "$OP_CLEAN_OK" = 1 ] || { oneos_parts_error "inspection cleanup incomplete; do not switch until reboot"; return 1; }
    }
    trap 'OP_EXIT=$?; trap - EXIT; oneos_parts_inspect_cleanup || OP_EXIT=1; exit "$OP_EXIT"' EXIT
    trap 'exit 1' HUP INT TERM
    "$BB" mount -t tmpfs -o size=1m,mode=700,nodev,nosuid,noexec tmpfs "$OP_INSPECT" || exit 1
    OP_INSPECT_TOP=1
    mkdir -m 700 "$OP_INSPECT/root" || exit 1
    OP_INSPECT_LOOP=$("$BB" losetup -f) || exit 1
    # Record only a successfully attached loop, so a failed attach never detaches
    # some other process's device after a losetup -f race.
    OP_PENDING_LOOP="$OP_INSPECT_LOOP"; OP_INSPECT_LOOP=
    "$BB" losetup -r "$OP_PENDING_LOOP" "$OP_DIR/vendor.img" || exit 1
    OP_INSPECT_LOOP="$OP_PENDING_LOOP"
    OP_DEV=$(stat -L -c '%t %T' "$OP_INSPECT_LOOP")
    set -- $OP_DEV
    [ "$#" = 2 ] && [ "$(cat "/sys/dev/block/$(printf '%d:%d' "0x$1" "0x$2")/ro" 2>/dev/null)" = 1 ] || exit 1
    OP_OPTIONS=ro,nodev,nosuid,noexec
    [ "$OP_RECORD" = ext4 ] && OP_OPTIONS="$OP_OPTIONS,noload"
    "$BB" mount -t "$OP_RECORD" -o "$OP_OPTIONS" "$OP_INSPECT_LOOP" "$OP_INSPECT/root" || exit 1
    OP_INSPECT_IMAGE=1
    "$BB" mount --make-private "$OP_INSPECT/root" || exit 1
    # Enumerate regular files without following donor directory/file symlinks.
    # Emit an archive only; no extracted binary, rc, property or module is run.
    # Relative paths in the archive are rooted in the image's private mount.
    cd "$OP_INSPECT/root" || exit 1
    find . -type f | LC_ALL=C sort | while IFS= read -r OP_PATH; do
        case "$OP_PATH" in
            ./build.prop|./etc/build.prop|./etc/vintf/*.xml|./etc/init/*.rc|./etc/selinux/*|./etc/ld.config*.txt)
                # Keep tar's input list unambiguous (no whitespace, traversal or options).
                case "$OP_PATH" in *[!A-Za-z0-9_./+-]*|*/../*|*/./*) continue ;; esac
                OP_FULL="$OP_INSPECT/root/${OP_PATH#./}"
                [ -f "$OP_FULL" ] && [ ! -L "$OP_FULL" ] &&
                    [ "$(readlink -f "$OP_FULL")" = "$OP_FULL" ] || continue
                printf '%s\n' "$OP_PATH" ;;
        esac
    done > "$OP_INSPECT/files"
    "$BB" tar -cf - -T "$OP_INSPECT/files" || exit 1
    cd "$STAGE" || exit 1
)
