#!/usr/bin/env python3
"""Embed the device-validated helper in the existing signed startup-patch format.

Use the exact installed host's bundled script as base, never the PR's
modified script. The envelope authenticates both the shell and embedded DEX.
"""
from pathlib import Path
import base64
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[1]
MARKER = '# MediaTek Bluetooth audio HALs close the whole A2DP session when AudioFlinger'
START = '# Oplus classic audio (S23 Android 17):'
LIMIT = 512 * 1024
APM_MARKER = "# Guest audioserver (oneos_switch.sh stopped Samsung's):"
APM_EXPERIMENT = r'''# Optional one-boot experiment: route output-mix effects to active flagless music.
# Exact S23 guest library from the device report; no other binary is modified.
# No persistent image writes, and no experiment on subsequent restarts unless rearmed.
oneos_apm_test() (
    D=/data/oneos-apm-test
    F=/system/lib64/libaudiopolicymanagerdefault.so
    [ -d "$D" ] && [ ! -L "$D" ] &&
        [ "$("$BB" stat -c '%u:%g:%a' "$D")" = 0:0:700 ] || exit 0
    [ -f "$D/once" ] && [ ! -L "$D/once" ] &&
        [ "$("$BB" stat -c '%u:%g:%a' "$D/once")" = 0:0:600 ] || exit 0
    rm -f "$D/once" || exit 0
    log() { echo "[OneOS patch] APM test: $*"; }
    ns=$("$BB" readlink /proc/self/ns/mnt)
    host=$("$BB" readlink /proc/1/ns/mnt)
    [ -n "$ns" ] && [ -n "$host" ] && [ "$ns" != "$host" ] || { log "not isolated"; exit 0; }
    [ -f "$F" ] && [ ! -L "$F" ] && [ "$("$BB" readlink -f "$F")" = "$F" ] || { log "unexpected library path"; exit 0; }
    # A bind must not propagate to the host or cover an existing file mount.
    "$BB" awk -v f="$F" '
        $5 == f { bad=1 }
        $5 == "/" || $5 == "/system" || $5 == "/system/lib64" {
            for(i=7;i<=NF && $i!="-";i++) if($i ~ /^shared:/) bad=1
        }
        END { exit bad }
    ' /proc/self/mountinfo || { log "shared or pre-existing mount"; exit 0; }
    old=996b9075d0963cf29ac8522801eac180056412dc331962a19909ce1c84e12fdb
    new=891d2ec4bc49e20c0d9b12e7a77e97bb7be086efc7d91ec837020bcc52594198
    [ "$("$BB" sha256sum "$F" | "$BB" cut -d ' ' -f 1)" = "$old" ] || { log "different library; skipped"; exit 0; }
    t=$("$BB" mktemp "$D/.apm.XXXXXX") || exit 0
    trap 'rm -f "$t"' EXIT
    # AArch64: preserve original primary test; accept active NONE only as fallback.
    # 0x5758c -> 0xc4cf4; original condition and control flow restored on return.
    cp "$F" "$t" &&
        "$BB" printf '\332\265\001\024' | "$BB" dd of="$t" bs=1 seek=357772 conv=notrunc 2>/dev/null &&
        "$BB" printf '\211\000\000\065\165\000\000\064\132\000\000\065\372\003\010\052\077\001\037\162\042\112\376\027' | "$BB" dd of="$t" bs=1 seek=806132 conv=notrunc 2>/dev/null &&
        [ "$("$BB" sha256sum "$t" | "$BB" cut -d ' ' -f 1)" = "$new" ] &&
        chmod 644 "$t" && "$BB" chcon u:object_r:system_lib_file:s0 "$t" ||
        { log "candidate validation failed"; exit 0; }
    if "$BB" mount --bind "$t" "$F"; then
        if "$BB" mount -o remount,bind,ro "$F"; then
            log "one-boot active flagless output experiment bound ($new)"
        else
            "$BB" umount "$F" || "$BB" umount -l "$F"
            log "read-only bind failed; removed"
        fi
    else
        log "bind failed"
    fi
)
oneos_apm_test

'''

def render(base, watcher, dex):
    if base.count(MARKER) != 1 or START in base:
        raise ValueError('Unexpected base script; refusing to patch')
    if not dex.startswith(b'dex\n') or b'LOneOSBtActive;' not in dex or b'Main looper initialized (v3)' not in dex:
        raise ValueError('Not the device-validated v3 helper')
    block = watcher[watcher.index(START):watcher.index(MARKER)]
    old = '''if [ "${ONEOS_GUEST_BT_ACTIVE_RECOVERY:-1}" = 1 ] &&
   [ -f /data/oneos_edm_stub.dex ] && "$BB" grep -aq "LOneOSBtActive;" /data/oneos_edm_stub.dex; then
    cat > /data/oneos_bt_active.sh <<'BT_ACTIVEEOF'
'''
    if block.count(old) != 1:
        raise ValueError('Watcher gate changed; review before signing')
    digest = hashlib.sha256(dex).hexdigest()
    encoded = base64.encodebytes(dex).decode('ascii')
    setup = '''if [ "${ONEOS_GUEST_BT_ACTIVE_RECOVERY:-1}" = 1 ]; then
    # Root-private guest storage; never replace the bundled EDM helper.
    BT_ACTIVE_DIR=/data/oneos-bt-active
    if [ ! -L "$BT_ACTIVE_DIR" ] &&
       (umask 077; mkdir -p "$BT_ACTIVE_DIR") &&
       [ "$(stat -c '%u:%g:%a' "$BT_ACTIVE_DIR")" = 0:0:700 ]; then
        BT_ACTIVE_TMP=$(mktemp "$BT_ACTIVE_DIR/.dex.XXXXXX")
        if [ -n "$BT_ACTIVE_TMP" ] && "$BB" base64 -d > "$BT_ACTIVE_TMP" <<'BT_ACTIVE_DEX'
''' + encoded + '''BT_ACTIVE_DEX
        then
            if [ "$(sha256sum "$BT_ACTIVE_TMP" | cut -d ' ' -f 1)" = "''' + digest + '''" ] &&
               chmod 400 "$BT_ACTIVE_TMP" && mv -f "$BT_ACTIVE_TMP" "$BT_ACTIVE_DIR/helper.dex"; then
                echo "[OneOS patch] signed A2DP recovery v3 armed"
                BT_ACTIVE_WATCH=$(mktemp "$BT_ACTIVE_DIR/.watch.XXXXXX")
                if [ -n "$BT_ACTIVE_WATCH" ]; then
                    cat > "$BT_ACTIVE_WATCH" <<'BT_ACTIVEEOF'
'''
    block = block.replace(old, setup)
    block = block.replace('LOG=/data/oneos_bt_active.log','LOG=/data/oneos-bt-active/recovery.log')
    block = block.replace('CLASSPATH=/data/oneos_edm_stub.dex','CLASSPATH=/data/oneos-bt-active/helper.dex')
    tail = '''    chmod 700 /data/oneos_bt_active.sh
    /system/bin/sh /data/oneos_bt_active.sh "$BB" </dev/null >/dev/null 2>&1 &
fi
'''
    if block.count(tail) != 1:
        raise ValueError('Watcher tail changed; review before signing')
    block = block.replace(tail, '''                    chmod 700 "$BT_ACTIVE_WATCH"
                    /system/bin/sh "$BT_ACTIVE_WATCH" "$BB" </dev/null >/dev/null 2>&1 &
                fi
            else
                echo "[OneOS patch] A2DP helper verification failed; skipped"
            fi
        fi
        [ -z "$BT_ACTIVE_TMP" ] || rm -f "$BT_ACTIVE_TMP"
    fi
fi
''')
    if base.count(APM_MARKER) != 1:
        raise ValueError('Missing exact guest audioserver anchor')
    payload = base.replace(APM_MARKER, APM_EXPERIMENT + APM_MARKER).replace(MARKER, block + MARKER).encode()
    if len(payload) > LIMIT:
        raise ValueError('Signed patch exceeds installed protocol limit')
    return payload

if __name__ == '__main__':
    base, dex, output = map(Path, sys.argv[1:])
    output.write_bytes(render(base.read_text(), (ROOT / 'switcher/assets/guest_start.sh').read_text(), dex.read_bytes()))
