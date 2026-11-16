#!/usr/bin/env python3
"""guest_props.sh places the ColorOS fingerprint icon from the panel's own dpi.

Galaxy S25 Ultra with ColorOS 17, session c2d30e95cde9775b (2026-11-16): the
icon was drawn below the sensor and touches on it did not unlock ("483px from
the bottom (33.16mm + 8.25mm/2 at 329.0 dpi)"). The first "N x N dpi" of
display_modes_host.log was a virtual display made by the shell; every earlier
session got 735 px from the 501.0411 fallback, as the "Built-in Screen" line has
no "x ... dpi". The dpi now comes from One UI's SurfaceFlinger mode with the
largest width. Runs the real panel_dpi and the real fp_dpi/fp_loc lines of the
block, in each shell and awk present.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROPS = (ROOT / 'switcher/assets/guest_props.sh').read_text()
FUNC = PROPS[PROPS.index('panel_dpi() {'):]
FUNC = FUNC[:FUNC.index('\n}\n') + 3]
# The block's own lines from fp_dpi to fp_loc (FP_POS is a fixed /sys path).
BLOCK = PROPS[PROPS.index('        fp_dpi=$(panel_dpi'):]
BLOCK = BLOCK[:BLOCK.index('\n', BLOCK.index('        fp_loc=')) + 1]

# S25 Ultra: /sys/class/fingerprint/fingerprint/position gives 33.16 mm and 8.25 mm.
MB, AH = '33.16', '8.25'


def mode(i, res, hz, dpi, active=False):
    head = '    activeMode=' if active else '        '
    return (f'{head}{{id={i}, hwcId={i}, resolution={res}, vsyncRate={hz}.00 Hz, '
            f'dpi={dpi}, group=0, vrrConfig=N/A, hdrOutputType=INVALID}}\n')


SF_HEAD = 'Display 4630946545580055170 (HWC display 0): port=130 pnpId=SDC displayName=""\n'
S25U_MODES = (mode(2, '1080x2340', 120, '375.78x376.18', True) +
              mode(0, '1440x3120', 120, '501.04x501.57') + mode(1, '1440x3120', 60, '501.04x501.57') +
              mode(2, '1080x2340', 120, '375.78x376.18') + mode(3, '1080x2340', 60, '375.78x376.18') +
              mode(4, '720x1560', 120, '250.52x250.78'))
BUILTIN = ('  DisplayDeviceInfo{"Built-in Screen": uniqueId="local:4630946545580055170", 1080 x 2340, '
           'modeId 3, renderFrameRate 120.0, defaultModeId 3, type INTERNAL, '
           'FLAG_ALLOWED_TO_BE_DEFAULT_DISPLAY, port=130, brightnessMaximum 1.0}\n')
VIRTUAL = ('  DisplayDeviceInfo{"scrcpy": uniqueId="virtual:com.android.shell,2000,scrcpy,0", '
           '1120 x 1792, modeId 1, renderFrameRate 60.0, density 329, 329.0 x 329.0 dpi, '
           'touch NONE, rotation 0, type VIRTUAL, owner com.android.shell (uid 2000)}\n'
           '  DisplayModeRecord{mMode={id=1, width=1120, height=1792, fps=60.0}}\n')
OLD_GOOD = SF_HEAD + S25U_MODES + 'mSupportedModes=[...]\n' + BUILTIN

SHELLS = [s for s in ('mksh', 'busybox', 'bash', 'dash', 'sh') if shutil.which(s)]


def awks(d):
    """PATH dirs whose awk is each awk present (busybox awk among them)."""
    out = [('system', None)]
    for name, cmd in (('busybox', ['busybox', 'awk']), ('mawk', ['mawk']), ('gawk', ['gawk']), ('original-awk', ['original-awk'])):
        if not shutil.which(cmd[0]):
            continue
        b = Path(d) / f'awk-{name}'
        b.mkdir()
        (b / 'awk').write_text('#!/bin/sh\nexec ' + ' '.join(cmd) + ' "$@"\n')
        (b / 'awk').chmod(0o755)
        out.append((name, str(b)))
    return out


def run(shell, code, env):
    argv = [shell, 'sh'] if shell == 'busybox' else [shell]
    return subprocess.run(argv + ['-euc', code], capture_output=True, text=True, check=True, env=env).stdout.strip()


class FpIconDpiTest(unittest.TestCase):
    def check(self, log, want_dpi, want_loc):
        with tempfile.TemporaryDirectory() as d:
            stage = Path(d) / 'stage'
            stage.mkdir()
            if log is not None:
                (stage / 'display_modes_host.log').write_text(log)
            code = FUNC + BLOCK + 'echo "$fp_dpi $fp_loc"'
            found = awks(d)
            for shell in SHELLS:
                for awk, path in found:
                    with self.subTest(shell=shell, awk=awk):
                        env = {**os.environ, 'STAGE': str(stage), 'fp_mb': MB, 'fp_ah': AH}
                        if path:
                            env['PATH'] = path + os.pathsep + env['PATH']
                        self.assertEqual(run(shell, code, env), f'{want_dpi} {want_loc}')

    def test_shell_virtual_display_first_is_ignored(self):
        # (a) c2d30e95cde9775b's shape: the virtual display's 329.0 dpi first.
        self.check(VIRTUAL + SF_HEAD + S25U_MODES + BUILTIN, '501.04', '735')

    def test_virtual_display_after_modes_is_ignored(self):
        self.check(OLD_GOOD + VIRTUAL, '501.04', '735')

    def test_no_mode_lines_falls_back(self):
        # (b) Only DisplayDeviceInfo lines, or no file at all: the S25 Ultra's 501.0411.
        self.check(SF_HEAD + BUILTIN + VIRTUAL, '501.0411', '735')
        self.check(None, '501.0411', '735')

    def test_only_1080_modes_uses_the_largest_present(self):
        # (c) A 1080x2340 panel (Galaxy S25): its own dpi, and the icon in its own
        # physical pixels, 552 px for the S25 Ultra's sensor position.
        log = SF_HEAD + mode(0, '1080x2340', 120, '375.78x376.18', True) + mode(1, '1080x2340', 60, '375.78x376.18') + BUILTIN
        self.check(log, '375.78', '552')

    def test_old_good_log(self):
        # (d) Sessions 3d9814984612c525 ... bceaef29fb128e94: 735 px.
        self.check(OLD_GOOD, '501.04', '735')

    def test_absurd_dpi_falls_back(self):
        for dpi in ('150.00x150.00', '900.10x900.10', '0.00x0.00'):
            with self.subTest(dpi=dpi):
                self.check(SF_HEAD + mode(0, '1440x3120', 120, dpi) + mode(1, '1080x2340', 120, '375.78x376.18'), '501.0411', '735')

    def test_largest_mode_without_dpi_falls_back(self):
        log = SF_HEAD + '        {id=0, hwcId=0, resolution=1440x3120, vsyncRate=120.00 Hz}\n' + mode(1, '1080x2340', 120, '375.78x376.18')
        self.check(log, '501.0411', '735')

    def test_log_line_unchanged(self):
        self.assertIn('echo "guest_props: ColorOS fingerprint icon ${fp_loc}px from the bottom '
                      '(${fp_mb}mm + ${fp_ah}mm/2 at ${fp_dpi} dpi)"', PROPS)
        self.assertNotIn(" x [0-9][0-9.]* dpi", PROPS[PROPS.index('FP_POS='):PROPS.index('ColorOS fingerprint icon ${fp_loc}')])


if __name__ == '__main__':
    unittest.main()
