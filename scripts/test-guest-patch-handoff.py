#!/usr/bin/env python3
"""Drive the staged host adapter and verifier with a throwaway debug key."""
import base64
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
PROTOCOL = REPO / "app-android/patch-protocol/src/main/java/com/fahadali/oneos/patch"
GUEST = REPO / "app-android/guest/src/main/java/com/fahadali/oneos/guest"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def main():
    compiler = sys.argv[1:] or ["javac"]
    with tempfile.TemporaryDirectory() as location:
        work = pathlib.Path(location)
        keys = work / "keys"
        keys.mkdir()
        run("openssl", "genpkey", "-algorithm", "ED25519", "-outform", "DER", "-out", keys / "private.der")
        run("openssl", "pkey", "-inform", "DER", "-in", keys / "private.der", "-pubout", "-outform", "DER",
            "-out", keys / "public.der")
        public = base64.b64encode((keys / "public.der").read_bytes()).decode("ascii")
        config = work / "BuildConfig.java"
        config.write_text(
            "package com.fahadali.oneos.guest;\n"
            "final class BuildConfig {\n"
            "    static final boolean DEBUG = true;\n"
            f'    static final String SIGNED_PATCH_PUBLIC_KEY = "{public}";\n'
            "}\n"
        )
        classes = work / "classes"
        run(*compiler, "-d", classes, *sorted(PROTOCOL.glob("*.java")), config, GUEST / "GuestPatchTool.java",
            REPO / "scripts/SignGuestStartPatch.java")
        launcher = work / "app_process"
        launcher.write_text('#!/bin/sh\nshift\nexec java -cp "$ONEOS_TEST_CLASSES" "$@"\n')
        launcher.chmod(0o700)
        shell = work / "guest_patch.sh"
        shell.write_text(
            (REPO / "switcher/assets/guest_patch.sh").read_text()
            .replace("/system/bin/app_process /system/bin", f"{launcher} /system/bin")
            .replace("/system/bin/sh -n", "sh -n")
            .replace("/data/adb/oneos-signed-patches", str(work / "store"))
            .replace("0:0", f"{os.getuid()}:{os.getgid()}")
        )
        stage = work / "stage"
        (stage / "tmp").mkdir(parents=True)
        (stage / "data/oneos_bootui").mkdir(parents=True)
        inbox = stage / "data/user/0/com.fahadali.oneos.guest/files/oneos-patch.pending"
        inbox.parent.mkdir(parents=True)
        base = stage / "guest_start.sh"
        base.write_bytes((REPO / "switcher/assets/guest_start.sh").read_bytes())
        apk = stage / "guest_manager.apk"
        apk.write_text("App process is mocked; this file exercises the staged APK hash check.\n")
        (stage / "files.sha256").write_text(
            "".join(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}\n" for file in (base, apk))
        )
        (stage / ".oneos_stage").write_text('  "versionCode": 159,\n  "image": "coloros-smoke",\n')
        image_hash = "1" * 64
        (stage / "system.raw.id").write_text(f"1 {image_hash} mock\n")
        descriptor = stage / "data/oneos_bootui/signed_patch"
        env = os.environ | {"ONEOS_TEST_CLASSES": str(classes)}

        def host(*args):
            return run("sh", shell, stage, *args, env=env, capture_output=True).stdout.strip()

        def sign(sequence, marker):
            script = work / "replacement.sh"
            parts = base.read_bytes().split(b"\n", 1)
            script.write_bytes(parts[0] + f"\necho '{marker}'\n".encode("ascii") + parts[1])
            run("java", "-cp", classes, "SignGuestStartPatch", "debug", "159", "coloros-smoke",
                image_hash, str(sequence), base, script, keys, inbox)

        host("configure", "0")
        host("publish")
        assert descriptor.read_text().splitlines()[1] == "0 0"
        sign(1, "[OneOS patch] signed restart smoke test")
        assert host("select", "0") == "base", "a non-app restart must not consume a new patch"
        assert host("select", "1") == "trial"
        selected = stage / "tmp/guest-patch-session/run.sh"
        assert "[OneOS patch] signed restart smoke test" in selected.read_text()
        assert descriptor.read_text().splitlines()[1] == "0 1"
        host("accept")  # The watcher invokes this only for its own generation's verified Home.
        sign(2, "failed trial")
        assert host("select", "1") == "trial"
        assert host("select", "0") == "known"
        assert "failed trial" not in selected.read_text()
        assert host("select", "1") == "known", "the failed trial must not be replayed"
        host("configure", "1")
        host("publish")
        assert descriptor.read_text().splitlines()[1] == "1 2"
        print("Verified signed marker handoff, app-only trial, opt-in metadata, accepted fallback and replay")

if __name__ == "__main__":
    main()
