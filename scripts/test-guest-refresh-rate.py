#!/usr/bin/env python3
"""The switch pins the guest only to a refresh rate its host panel supports."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "switcher/assets/oneos_switch.sh").read_text()
HELPER = ROOT / "switcher/assets/guest_partitions.sh"
BLOCK = "GUEST_REFRESH=120.0\n" + SOURCE.split("GUEST_REFRESH=120.0\n", 1)[1].split('GUEST_HIGH_ORR=3', 1)[0]
SH = shutil.which("sh")
if not SH and os.name == "nt":
    candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/usr/bin/sh.exe"
    if candidate.is_file():
        SH = str(candidate)


class GuestRefreshRate(unittest.TestCase):
    def rate(self, text):
        if not SH:
            self.skipTest("a POSIX shell is not installed")
        with tempfile.TemporaryDirectory() as td:
            stage = Path(td)
            (stage / "display_host.txt").write_text(text)
            script = f'. "{HELPER}"\n' + BLOCK + "printf '%s' \"$GUEST_REFRESH\"\n"
            run = subprocess.run(
                [SH, "-c", script], env={"STAGE": str(stage)},
                capture_output=True, text=True, check=True,
            )
            return run.stdout.strip().splitlines()[-1]

    def test_panel_maximum_is_used(self):
        for rates, expected in [
            ([60], "60.0"),
            ([90, 60], "90.0"),
            ([120, 60, 90], "120.0"),
            ([144, 120, 60], "120.0"),
        ]:
            modes = ", ".join(f"{{id={i}, fps={rate}.0, vsync={rate}.0}}" for i, rate in enumerate(rates))
            with self.subTest(rates=rates):
                self.assertEqual(expected, self.rate("supportedModes [" + modes + "]\n"))

    def test_bad_or_missing_dump_keeps_safe_default(self):
        for dump in ["", "fps=0.0 fps=29.0 fps=240.0\n", "not a display dump\n"]:
            with self.subTest(dump=dump):
                self.assertEqual("120.0", self.rate(dump))

    def test_external_and_virtual_displays_do_not_set_phone_ceiling(self):
        base = 'mBaseDisplayInfo=DisplayInfo{"Built-in", displayId 0, supportedModes [{id=2, fps=89.99, vsync=180}], type INTERNAL}'
        other = base.replace('displayId 0', 'displayId 3').replace('89.99', '120').replace('INTERNAL', 'EXTERNAL')
        self.assertEqual('90.0', self.rate(other + '\n' + base + '\n' + other))

    def test_mode_at_a_different_resolution_does_not_raise_the_ceiling(self):
        dump = ('mBaseDisplayInfo=DisplayInfo{"panel", displayId 0, mode 2, supportedModes '
                '[{id=1, width=720, height=1280, fps=120.0}, '
                '{id=2, width=1080, height=2340, fps=60.0}, '
                '{id=3, width=1080, height=2340, fps=90.0}], type INTERNAL}')
        self.assertEqual('90.0', self.rate(dump))

    def test_restore_accepts_both_surfaceflinger_rate_formats(self):
        start = (ROOT / 'switcher/assets/guest_start.sh').read_text()
        line = next(l for l in start.splitlines() if 'pr=$(echo "$pol"' in l)
        for gap in ('', ' '):
            pol = f'displayManagerPolicy={{primaryRanges={{physical=[0, 120], render=[30.00{gap}Hz, 90.00{gap}Hz]}}}}'
            got = subprocess.run(['sh', '-c', line + '\nprintf "%s" "$pr"'], env={**os.environ, 'pol': pol},
                                 check=True, text=True, capture_output=True)
            self.assertEqual('30.00 90.00', got.stdout)

    def test_sf_uses_present_rate_for_both_modes(self):
        start = (ROOT / 'switcher/assets/guest_start.sh').read_text()
        awk = start.split('sel=$(echo "$D" | awk -v d="$id" ', 1)[1].split("}')", 1)[0] + "}'"
        rows = ['Display 42', ' activeMode={id=1, resolution=1080x2340, other=0}']
        for i, clock, limit in ((1, 240, 'minFrameIntervalNs=8333333'), (2, 180, 'peakRefreshRate=90.0'), (3, 240, 'minFrameIntervalNs=16666667')):
            rows.append(f' {{id={i}, hwcId={i}, resolution=1080x2340, vsyncRate={clock}.0 Hz, {limit}}}')
        got = subprocess.run(['sh', '-c', 'awk -v d=42 ' + awk], input='\n'.join(rows),
                             text=True, check=True, capture_output=True)
        self.assertEqual('1 120 2 90', got.stdout.strip())

    def test_activator_uses_detected_rate_for_both_bounds(self):
        self.assertNotIn("put system peak_refresh_rate 120.0", SOURCE)
        self.assertRegex(SOURCE, r"put system peak_refresh_rate \$GUEST_REFRESH")
        self.assertRegex(SOURCE, r"put system min_refresh_rate \$GUEST_REFRESH")


if __name__ == "__main__":
    unittest.main()
