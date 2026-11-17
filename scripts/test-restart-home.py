"""Test the Home acknowledgement using the generated activator's shell code."""
import pathlib
import subprocess
import tempfile

source = pathlib.Path("switcher/assets/oneos_switch.sh").read_text()
start = source.index("record_restart_home() {")
end = source.index("\nGEN=", start)
template = source[start:end]
mock = r"""
readlink() { printf '%s\n' "$TEST_NS"; }
timeout() {
    case "$*" in
        *resolve-activity*) printf '%s\n' 'com.example.launcher/.Home' ;;
        *dumpsys*) printf '%s\n' "topResumedActivity=ActivityRecord{1 u0 $TEST_COMPONENT t2}" ;;
    esac
}
sleep() { :; }
"""
with tempfile.TemporaryDirectory() as directory:
    stage = pathlib.Path(directory)
    state = stage / "tmp"
    state.mkdir()
    (stage / "guest_mntns").write_text("mnt:[45]\n")
    (state / "softreboot.gen").write_text("8\n")
    script = subprocess.check_output(
        ["sh", "-c", f'STAGE=$1; BB=busybox; printf "%s" "{template}"', "sh", directory],
        text=True,
    )
    for generation, namespace, component, expected in (
        (7, "mnt:[45]", "com.example.launcher/.Home", False),
        (8, "mnt:[12]", "com.example.launcher/.Home", False),
        (8, "mnt:[45]", "com.example.launcher/.HomeOther", False),
        (8, "mnt:[45]", "com.example.launcher/.Home", True),
        (8, "mnt:[45]", "com.example.launcher/com.example.launcher.Home", True),
    ):
        subprocess.run(
            ["sh", "-c", mock + script + "\nGEN=$1; TEST_NS=$2; TEST_COMPONENT=$3; PID=123\nrecord_restart_home || :",
             "sh", str(generation), namespace, component],
            check=True,
        )
        record = state / f"restart-home.{generation}"
        assert record.exists() == expected, (generation, namespace, component)
        if expected:
            assert record.read_text() == "8 mnt:[45]\n"
            record.unlink()
