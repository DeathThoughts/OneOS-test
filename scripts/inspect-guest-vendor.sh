#!/system/bin/sh
# Developer entry point: produce a config archive on stdout, not a guest switch.
# Root shell only; the parent mounts nothing. The child always unshares first.
STAGE=/data/local/tmp/oneos
[ "$(id -u)" = 0 ] || { echo "Run as root" >&2; exit 1; }
[ "$(readlink -f "$STAGE")" = "$STAGE" ] || exit 1
for c in /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox /data/adb/magisk/busybox; do
    [ -x "$c" ] && { BB=$c; break; }
done
[ -n "${BB:-}" ] || exit 1
# Leave mounted/running guest sessions alone. Inspection is an idle-only tool.
if awk -v p="$STAGE" '$5 == p || index($5,p "/") == 1 {found=1} END {exit !found}' /proc/self/mountinfo; then
    echo "Stage still mounted: return to One UI first" >&2; exit 1
fi
[ "$(stat -c %u "$STAGE")" = 0 ] || exit 1
case "$(stat -c %a "$STAGE")" in 500|555|700|755) ;; *) exit 1 ;; esac
[ -f "$STAGE/guest_partitions.sh" ] && [ ! -L "$STAGE/guest_partitions.sh" ] &&
    [ "$(stat -c %u "$STAGE/guest_partitions.sh")" = 0 ] || exit 1
case "$(stat -c %a "$STAGE/guest_partitions.sh")" in 400|444|600|644|500|555|700|755) ;; *) exit 1 ;; esac
# Root-manager BusyBox is passed as a positional parameter, not interpolated
# code; neither the manifest nor the donor image supplies commands.
exec "$BB" unshare -m "$BB" sh -c '
    STAGE=/data/local/tmp/oneos; BB=$1
    . "$STAGE/guest_partitions.sh" || exit 1
    oneos_parts_inspect_vendor
' oneos-vendor-inspection "$BB"
