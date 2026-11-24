#!/usr/bin/env python3
"""ColorOS min_refresh_rate handling in UI_ACTIVATOR (oneos_switch.sh) and wave comment (guest_start.sh).

S25 Ultra, coloros17-findx9u-in-1105 (sessions db19aee410bd79ff and
75e538ac0ed725df, diag_90_display.txt): forcing `settings put system
min_refresh_rate 120.0` on ColorOS held
PRIORITY_USER_SETTING_MIN_RENDER_FRAME_RATE at 120 Hz all session over
OplusRefreshRateService, while panel_keeper and /data/oneos_sf_kick.sh already
cure the 24 Hz start without pinning. On ColorOS (`GUEST_OPLUS=1`), once per
stage (`$STAGE/data/oneos_min_refresh_reset`), a forced `120` or `120.0` floor
is deleted; any other value (including `null` or a user choice like `60.0`)
stays, and other guests (`GUEST_OPLUS=0`) keep `min_refresh_rate 120.0`.

This extracts the real refresh block and `[UI Activator] refresh:` summary line
from `UI_ACTIVATOR="..."` in switcher/assets/oneos_switch.sh, expands them with
`/bin/sh` just like oneos_switch.sh does at switch-build time, verifies `sh -n`,
and runs the generated activator snippet against a stubbed nsenter/settings
environment.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWITCH = (ROOT / 'switcher/assets/oneos_switch.sh').read_text()
GUEST_START = (ROOT / 'switcher/assets/guest_start.sh').read_text()

ACT_START = '\nUI_ACTIVATOR="\n'


def dq_end(text, i):
    while True:
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if c == '"':
            return i
        i += 1


def extract_activator_refresh_snippet():
    a = SWITCH.index(ACT_START) + len(ACT_START)
    act = SWITCH[a:dq_end(SWITCH, a)]
    r_start = act.index('            if [ $GUEST_OPLUS = 1 ] && [ $GUEST_ORR_DYN = 1 ]')
    oti_marker = "            $BB nsenter -t \\$PID -m /system/bin/sh /data/oneos_genv.sh /system/bin/dumpsys oplus_vrr_service oti false >/dev/null 2>&1 && echo '[UI Activator] refresh rate: ColorOS touch idle off'\n"
    r_end = act.index(oti_marker, r_start) + len(oti_marker)
    refresh_block = act[r_start:r_end]

    summary_lines = [
        line for line in act.splitlines()
        if '[UI Activator] refresh: min ' in line
    ]
    assert len(summary_lines) == 1, summary_lines
    return refresh_block + summary_lines[0] + '\n'


RAW_SNIPPET = extract_activator_refresh_snippet()

FAKE_BB = """#!/bin/sh
# Stand-in for `$BB nsenter -t $PID -m /system/bin/sh /data/oneos_genv.sh /system/bin/<tool> ...`
[ "$1" = nsenter ] || exit 1
shift 6
tool=${1##*/}
shift
case "$tool" in
    settings)
        op=$1; ns=$2; key=$3
        f="$SETTINGS_DIR/${ns}.${key}"
        case "$op" in
            get)
                if [ -e "$SETTINGS_DIR/unreadable.$key" ]; then
                    exit 1
                fi
                if [ -f "$f" ]; then
                    cat "$f"
                else
                    echo null
                fi
                ;;
            put)
                printf '%s\\n' "$4" > "$f"
                printf 'put %s %s %s\\n' "$ns" "$key" "$4" >> "$SETTINGS_DIR/ops.log"
                ;;
            delete)
                rm -f "$f"
                printf 'delete %s %s\\n' "$ns" "$key" >> "$SETTINGS_DIR/ops.log"
                ;;
            *)
                exit 1
                ;;
        esac
        ;;
    dumpsys)
        exit 0
        ;;
    *)
        exit 1
        ;;
esac
"""


class Harness:
    def __init__(self, d, guest_oplus='1', guest_refresh='120.0'):
        self.root = Path(d)
        self.stage = self.root / 'stage'
        (self.stage / 'data').mkdir(parents=True)
        self.settings = self.root / 'settings'
        self.settings.mkdir()
        (self.settings / 'system.screen_brightness').write_text('128\n')
        self.bb = self.root / 'busybox'
        self.bb.write_text(FAKE_BB)
        self.bb.chmod(0o755)
        self.guest_oplus = str(guest_oplus)
        # oneos_switch.sh sets it from One UI's physical modes (0.5.20 upstream); 120.0 unless the panel tops out lower.
        self.guest_refresh = guest_refresh

    def expand(self):
        gen = self.root / 'expand.sh'
        gen.write_text('S="\n' + RAW_SNIPPET + '"\nprintf "%s" "$S"\n')
        env = {
            **os.environ,
            'GUEST_OPLUS': self.guest_oplus,
            'GUEST_ORR_DYN': '0',
            'GUEST_HIGH_ORR': '3',
            'GUEST_REFRESH': self.guest_refresh,
            'STAGE': str(self.stage),
            'BB': str(self.bb),
        }
        r = subprocess.run(['/bin/sh', str(gen)], env=env, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise AssertionError(f'expand failed: {r.stderr}')
        chk = subprocess.run(['/bin/sh', '-n'], input=r.stdout, capture_output=True, text=True, timeout=30)
        if chk.returncode != 0:
            raise AssertionError(f'sh -n failed:\n{chk.stderr}\n---\n{r.stdout}')
        return r.stdout

    def set_min(self, val):
        f = self.settings / 'system.min_refresh_rate'
        if val is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(f'{val}\n')

    def get_setting(self, key):
        f = self.settings / f'system.{key}'
        return f.read_text().strip() if f.exists() else 'null'

    def ops(self):
        f = self.settings / 'ops.log'
        return f.read_text().splitlines() if f.exists() else []

    def clear_ops(self):
        (self.settings / 'ops.log').unlink(missing_ok=True)

    @property
    def marker(self):
        return self.stage / 'data/oneos_min_refresh_reset'

    def boot(self):
        expanded = self.expand()
        run_sh = self.root / 'run_activator.sh'
        run_sh.write_text('PID=4242\n' + expanded)
        env = {**os.environ, 'SETTINGS_DIR': str(self.settings)}
        r = subprocess.run(['/bin/sh', str(run_sh)], env=env, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise AssertionError(f'activator failed ({r.returncode}): {r.stderr}')
        return r.stdout


class ColorOsMinRefreshTest(unittest.TestCase):
    def test_expanded_script_parses_under_both_modes(self):
        for oplus in ('1', '0'):
            with tempfile.TemporaryDirectory() as d:
                h = Harness(d, guest_oplus=oplus)
                expanded = h.expand()
                self.assertIn('peak_refresh_rate 120.0', expanded)

    def test_a_90_hz_panel_gets_its_own_peak_and_min_not_120(self):
        # 0.5.20 upstream: [120, 120] on a 90 Hz panel is an impossible vote and Android falls back to 60 Hz.
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, guest_oplus='0', guest_refresh='90.0')
            h.boot()
            self.assertEqual('90.0', h.get_setting('peak_refresh_rate'))
            self.assertEqual('90.0', h.get_setting('min_refresh_rate'))
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, guest_oplus='1', guest_refresh='90.0')
            h.boot()
            self.assertEqual('90.0', h.get_setting('peak_refresh_rate'))
            self.assertEqual('null', h.get_setting('min_refresh_rate'))

    def test_coloros_fresh_stage_keeps_null_and_sets_marker(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, guest_oplus='1')
            out = h.boot()
            self.assertEqual('null', h.get_setting('min_refresh_rate'))
            self.assertEqual('120.0', h.get_setting('peak_refresh_rate'))
            self.assertTrue(h.marker.is_dir())
            self.assertFalse(h.marker.is_symlink())
            self.assertNotIn('delete system min_refresh_rate', h.ops())
            self.assertNotIn('put system min_refresh_rate 120.0', h.ops())
            self.assertIn('[UI Activator] min refresh rate: was min_refresh_rate=null; left alone (once per stage)', out)
            self.assertIn('[UI Activator] refresh: min null peak 120.0; brightness 128', out)

    def test_coloros_upgraded_stage_resets_120_and_120_0_once(self):
        for legacy in ('120.0', '120'):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as d:
                h = Harness(d, guest_oplus='1')
                h.set_min(legacy)
                out = h.boot()
                self.assertEqual('null', h.get_setting('min_refresh_rate'))
                self.assertEqual('120.0', h.get_setting('peak_refresh_rate'))
                self.assertTrue(h.marker.is_dir())
                self.assertIn('delete system min_refresh_rate', h.ops())
                self.assertIn(
                    f'[UI Activator] min refresh rate: was min_refresh_rate={legacy}; the forced floor reset once',
                    out,
                )
                self.assertIn('[UI Activator] refresh: min null peak 120.0; brightness 128', out)

    def test_second_boot_with_marker_leaves_120_0_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, guest_oplus='1')
            h.boot()
            self.assertTrue(h.marker.is_dir())
            h.clear_ops()
            h.set_min('120.0')
            out = h.boot()
            self.assertEqual('120.0', h.get_setting('min_refresh_rate'))
            self.assertNotIn('delete system min_refresh_rate', h.ops())
            self.assertNotIn('[UI Activator] min refresh rate:', out)
            self.assertIn('[UI Activator] refresh: min 120.0 peak 120.0; brightness 128', out)

    def test_custom_60_0_on_first_boot_is_kept_and_marked(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, guest_oplus='1')
            h.set_min('60.0')
            out = h.boot()
            self.assertEqual('60.0', h.get_setting('min_refresh_rate'))
            self.assertTrue(h.marker.is_dir())
            self.assertNotIn('delete system min_refresh_rate', h.ops())
            self.assertIn('[UI Activator] min refresh rate: was min_refresh_rate=60.0; left alone (once per stage)', out)
            self.assertIn('[UI Activator] refresh: min 60.0 peak 120.0; brightness 128', out)

    def test_unreadable_setting_waits_for_next_boot_without_marker(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, guest_oplus='1')
            h.set_min('120.0')
            (h.settings / 'unreadable.min_refresh_rate').write_text('1')
            out = h.boot()
            self.assertFalse(h.marker.exists())
            self.assertNotIn('delete system min_refresh_rate', h.ops())
            self.assertIn('[UI Activator] min refresh rate: not read; will check next boot', out)

    def test_symlink_marker_is_refused_and_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, guest_oplus='1')
            victim = h.root / 'victim'
            victim.mkdir()
            os.symlink(str(victim), str(h.marker))
            h.set_min('120.0')
            out = h.boot()
            self.assertTrue(h.marker.is_symlink())
            self.assertEqual('120.0', h.get_setting('min_refresh_rate'))
            self.assertNotIn('delete system min_refresh_rate', h.ops())
            self.assertIn(
                '[UI Activator] min refresh rate: data/oneos_min_refresh_reset is a link, not made by OneOS; left alone',
                out,
            )

    def test_non_coloros_guest_still_writes_120_0_every_boot(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, guest_oplus='0')
            for _ in range(2):
                h.clear_ops()
                out = h.boot()
                self.assertEqual('120.0', h.get_setting('min_refresh_rate'))
                self.assertEqual('120.0', h.get_setting('peak_refresh_rate'))
                self.assertFalse(h.marker.exists())
                self.assertFalse(h.marker.is_symlink())
                self.assertIn('put system min_refresh_rate 120.0', h.ops())
                self.assertIn('[UI Activator] refresh: min 120.0 peak 120.0; brightness 128', out)

    def test_guest_start_wave_comment_updated_and_behaviour_preserved(self):
        anchor = 'echo "[Pivoted Root] SystemUI: fingerprint wave APK bound"\n'
        tail = '    else\n        echo "[Pivoted Root] SystemUI: fingerprint wave bind failed; stock SystemUI"\n'
        wave_section = GUEST_START.split(anchor, 1)[1].split(tail, 1)[0]
        self.assertNotIn('resets on boot', wave_section)
        self.assertIn('separate wave APK is bound', wave_section)
        self.assertIn('persists across', wave_section)
        self.assertIn('db19aee410bd79ff', wave_section)
        self.assertIn('75e538ac0ed725df', wave_section)
        self.assertIn('settings put system min_refresh_rate 120 >/dev/null 2>&1 || true', wave_section)
        self.assertIn('panel_keeper', SWITCH)


if __name__ == '__main__':
    unittest.main()
