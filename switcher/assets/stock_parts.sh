#!/system/bin/sh
# Only called by the verified root verb while its ordinary stage lock is held.
STAGE=$1
BB=$2
[ "$(id -u)" = 0 ] && [ "$STAGE" = /data/local/tmp/oneos ] || exit 1
[ "$(readlink -f "$STAGE")" = "$STAGE" ] || exit 1
[ ! -e "$STAGE/partition_images" ] && [ ! -L "$STAGE/partition_images" ] || exit 1
refuse() { echo "ERROR: stock partitions: $*" >&2; exit 1; }
die() { refuse "$@"; }
say() { echo "$*"; }
sha() { sha256sum "$1" | cut -d ' ' -f1; }
is_hex64() { [ ${#1} = 64 ] && case "$1" in *[!0-9a-f]*) return 1 ;; *) return 0 ;; esac; }
. "$STAGE/guest_partitions.sh" || die "partition helper failed"
oneos_parts_file "$STAGE/system.raw" || refuse "system.raw is not a safe regular file"
# Prove both live block devices before creating or copying any snapshot.
OP_HASH=3ace2530806e1eeba86007eab46a4fbebe607caf04ea73ce6cbb2ec6a17c63e7
OP_SIZE=1907937280
oneos_parts_vendor_host erofs "$OP_HASH" "$OP_SIZE" || refuse "stock vendor does not match this phone"
SP_VENDOR=$OP_HOST_DEV
OP_HASH=1c1e9e3f95fa3e635dcfbb63e37a76fc40f60014b6a6bc8c8ed7e1457d29f4de
OP_SIZE=778240
oneos_parts_odm_host erofs "$OP_HASH" "$OP_SIZE" || refuse "stock ODM does not match this phone"
SP_ODM=$OP_HOST_DEV
SP_NEW=$(umask 077; mktemp -d "$STAGE/.stock-parts.XXXXXX") || die "cannot create snapshot directory"
# Exit cleanup removes only this operation's unpublished regular files.
trap 'rm -f "$SP_NEW/vendor.img" "$SP_NEW/odm.img" "$SP_NEW/manifest"; rmdir "$SP_NEW" 2>/dev/null' EXIT
(umask 077; cat "$SP_VENDOR" > "$SP_NEW/vendor.img" && cat "$SP_ODM" > "$SP_NEW/odm.img") || die "cannot copy stock partitions (free about 2 GB)"
for SP_NAME in vendor odm; do
    case "$SP_NAME" in
        vendor) SP_HASH=3ace2530806e1eeba86007eab46a4fbebe607caf04ea73ce6cbb2ec6a17c63e7; SP_SIZE=1907937280 ;;
        odm) SP_HASH=1c1e9e3f95fa3e635dcfbb63e37a76fc40f60014b6a6bc8c8ed7e1457d29f4de; SP_SIZE=778240 ;;
    esac
    [ "$(stat -c %s "$SP_NEW/$SP_NAME.img")" = "$SP_SIZE" ] &&
        [ "$(sha256sum "$SP_NEW/$SP_NAME.img" | cut -d ' ' -f1)" = "$SP_HASH" ] || die "copied $SP_NAME failed verification"
done
SP_BASE=$(sha "$STAGE/system.raw")
is_hex64 "$SP_BASE" || die "cannot hash system.raw"
(umask 077; printf 'oneos-partitions-v1 %s\n' "$SP_BASE" > "$SP_NEW/manifest" &&
    printf '%s\n' 'vendor erofs 3ace2530806e1eeba86007eab46a4fbebe607caf04ea73ce6cbb2ec6a17c63e7 1907937280 stock' \
        'odm erofs 1c1e9e3f95fa3e635dcfbb63e37a76fc40f60014b6a6bc8c8ed7e1457d29f4de 778240 stock' >> "$SP_NEW/manifest") || die "cannot write manifest"
mv "$SP_NEW" "$STAGE/partition_images" || die "cannot publish stock snapshots"
trap - EXIT
say "@@OK stock-parts on; verified vendor and ODM snapshots paired to this system.raw"
