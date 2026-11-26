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
APM_START = '# OneOS APM effect routing v3 (begin)'
APM_END = '# OneOS APM effect routing v3 (end)'

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
    if watcher.count(APM_START) != 1 or watcher.count(APM_END) != 1:
        raise ValueError('Missing unique APM routing block')
    apm = watcher[watcher.index(APM_START):watcher.index(APM_END) + len(APM_END)] + '\n\n'
    if APM_START in base or not base.startswith('#!/system/bin/sh\n'):
        raise ValueError('Unexpected APM base')
    # Selection/startup diagnostic is independent of APM marker and boot success.
    payload = base.replace(APM_MARKER, apm + APM_MARKER).replace(MARKER, block + MARKER)
    payload = payload.replace('#!/system/bin/sh\n', "#!/system/bin/sh\necho '[OneOS patch] signed audio routing v3 startup reached'\n", 1).encode()
    if len(payload) > LIMIT:
        raise ValueError('Signed patch exceeds installed protocol limit')
    return payload

if __name__ == '__main__':
    base, dex, output = map(Path, sys.argv[1:])
    output.write_bytes(render(base.read_text(), (ROOT / 'switcher/assets/guest_start.sh').read_text(), dex.read_bytes()))
