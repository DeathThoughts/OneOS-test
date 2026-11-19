#!/usr/bin/env python3
"""Exercise the staged telemetry script with user-0 package and settings stubs."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PREPARE = Path(__file__).resolve().parents[1] / "switcher/assets/guest_prepare.sh"
START = 'rm -f "$STAGE/data/oneos_guest_telemetry.sh"\n'
END = "\n# The guest's /oem"
BLOCK = START + PREPARE.read_text().split(START, 1)[1].split(END, 1)[0]
STUB = """#!/bin/sh
case "${0##*/}:$*" in
    'readlink:/proc/self/ns/mnt') echo guest ;;
    'readlink:/proc/1/ns/mnt') echo host ;;
    'readlink:/proc/123/ns/mnt') echo "${SERVER_NS:-guest}" ;;
    'cat:/proc/123/comm') echo system_server ;;
    'cat:'*) exec /bin/cat "$@" ;;
    'pm:list packages --user 0 -s -e '*) [ "$(cat "$VALUE")" -le 1 ] && echo "package:$7" ;;
    'pm:list packages --user 0 -s '*) [ "${PRESENT:-1}" = 1 ] && echo "package:$6" ;;
    'dumpsys:package '*) printf '  User 0: installed=true enabled=%s\n' "$(cat "$VALUE")" ;;
    'settings:--user 0 get system samsung_errorlog_agree') cat "$VALUE" ;;
    'content:call --user 0 --uri content://lineagesettings/secure --method GET_secure --arg stats_collection')
        printf 'Result: Bundle[{value=%s}]\n' "$(cat "$VALUE")" ;;
    'pm:disable-user --user 0 '*) value=3 ;;
    'pm:default-state --user 0 '*) value=0 ;;
    'pm:enable --user 0 '*) value=1 ;;
    'settings:--user 0 put system samsung_errorlog_agree '*) value=$6 ;;
    'content:call --user 0 --uri content://lineagesettings/secure --method PUT_secure --arg stats_collection --extra value:s:'*)
        value=${11##*:} ;;
    *) exit 4 ;;
esac
if [ -n "${value:-}" ]; then
    echo "$*" >> "$WRITES"
    [ "${FAIL_WRITE:-0}" = 0 ] || exit 1
    echo "$value" > "$VALUE"
fi
"""


class GuestTelemetryTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.state = self.data / "oneos_guest_telemetry"
        self.value = self.root / "value"
        self.writes = self.root / "writes"
        self.generated = self.data / "oneos_guest_telemetry.sh"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name in ("readlink", "cat", "pm", "dumpsys", "content", "settings"):
            stub = bin_dir / name
            stub.write_text(STUB)
            stub.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                        STAGE=str(self.root), BB="true",
                        VALUE=str(self.value), WRITES=str(self.writes))

    def stage(self, image, keep=False):
        (self.root / ".oneos_stage").write_text(f'  "image": "{image}",\n')
        subprocess.run(["sh", "-c", BLOCK], check=True,
                       env=dict(self.env, ONEOS_GUEST_TELEMETRY=str(int(keep))))

    def boot(self, **env):
        script = self.generated.read_text().replace("state=/data/", f"state={self.root}/data/")
        return subprocess.run(["sh", "-c", script, "guest_telemetry", "123"],
                              check=False, capture_output=True, text=True,
                              env=dict(self.env, **env))

    def check_value(self, value):
        self.assertEqual(str(value), self.value.read_text().strip())

    def test_image_policies_and_stale_script_removal(self):
        for image, kind, target in [
            ("pixel11fold-yogi", "package", "com.google.android.feedback"),
            ("pixel11pro-kodiak", "package", "com.google.android.feedback"),
            ("pixel9proxl-komodo", "package", "com.google.android.feedback"),
            ("nothingos5-metroid", "package", "com.nothing.logkit"),
            ("nothingos5-asteroids", "package", "com.nothing.logkit"),
            ("originos7-pd2609ma", "package", "com.bbk.iqoo.logsystem"),
            ("oxygenos16-cph2745", "package", "com.oplus.onetrace com.oplus.olc"),
            ("lineage24-infiniti", "lineage", "stats_collection"),
            ("infinityx41-findx9u", "lineage", "stats_collection"),
            ("oneui95-s948b", "samsung", "samsung_errorlog_agree"),
        ]:
            with self.subTest(image=image):
                self.stage(image)
                self.assertTrue(self.generated.read_text().startswith(f"kind={kind}\ntargets='{target}'\n"))
        for image in ("hyperos-lhasa", "coloros17-findn6", "unrecognised"):
            self.stage(image)
            self.assertFalse(self.generated.exists())
        (self.data / "oneos_oplus_telemetry.sh").touch()
        self.stage("oxygenos16-cph2745")
        self.assertFalse(self.generated.exists())

    def test_package_restores_original_default_state(self):
        self.stage("nothingos5-metroid")
        self.value.write_text("0")
        self.assertIn("turned off", self.boot().stdout)
        self.check_value(3)
        self.boot()
        self.assertEqual(1, len(self.writes.read_text().splitlines()))
        self.stage("nothingos5-metroid", keep=True)
        self.assertIn("restored", self.boot().stdout)
        self.check_value(0)
        self.assertFalse(list(self.state.iterdir()))

    def test_package_already_disabled_is_not_restored(self):
        self.stage("pixel11pro-kodiak")
        self.value.write_text("3")
        self.boot()
        self.stage("pixel11pro-kodiak", keep=True)
        self.boot()
        self.check_value(3)
        self.assertFalse(self.writes.exists())

    def test_uninstalled_package_and_wrong_namespace_do_not_write(self):
        self.stage("originos7-pd2609ma")
        self.value.write_text("1")
        self.boot(PRESENT="0")
        self.boot(SERVER_NS="host")
        self.assertFalse(self.writes.exists())

    def test_settings_round_trip_and_unavailable_provider(self):
        self.stage("lineage24-infiniti")
        self.value.write_text("null")
        self.boot()
        self.assertFalse(self.writes.exists())
        for image in ("lineage24-infiniti", "oneui95-s948b"):
            with self.subTest(image=image):
                self.value.write_text("1")
                self.stage(image)
                self.assertIn("turned off", self.boot().stdout)
                self.check_value(0)
                self.stage(image, keep=True)
                self.assertIn("restored", self.boot().stdout)
                self.check_value(1)

    def test_failed_restore_keeps_record_for_retry(self):
        self.stage("oneui95-s948b")
        self.value.write_text("1")
        self.boot()
        self.stage("oneui95-s948b", keep=True)
        self.assertIn("will retry", self.boot(FAIL_WRITE="1").stdout)
        self.check_value(0)
        self.assertTrue(list(self.state.iterdir()))
        self.boot()
        self.check_value(1)


if __name__ == "__main__":
    unittest.main()
