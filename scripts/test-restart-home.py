"""Test the Home acknowledgement using the generated activator's shell code."""
import pathlib
import subprocess
import tempfile

source = pathlib.Path("switcher/assets/oneos_switch.sh").read_text()
start = source.index("record_restart_home() {")
end = source.index("\nGEN=", start)
template = source[start:end]
mock = r"""
readlink() {
    case "$1" in
        /proc/1/ns/mnt) printf '%s\n' 'mnt:[1]' ;;
        *) printf '%s\n' "$TEST_NS" ;;
    esac
}
stat() {
    case "$*" in
        */rootfs) printf '%s\n' '12:33' ;;
        *) printf '%s\n' "$TEST_ROOT" ;;
    esac
}
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
    for generation, namespace, root, component, expected in (
        (7, "mnt:[45]", "12:33", "com.example.launcher/.Home", False),
        (8, "mnt:[1]", "12:33", "com.example.launcher/.Home", False),
        (8, "mnt:[45]", "12:99", "com.example.launcher/.Home", False),
        (8, "mnt:[45]", "12:33", "com.example.launcher/.HomeOther", False),
        (8, "mnt:[45]", "12:33", "com.example.launcher/.Home", True),
        (8, "mnt:[46]", "12:33", "com.example.launcher/com.example.launcher.Home", True),
    ):
        subprocess.run(
            ["sh", "-c", mock + script + "\nGEN=$1; TEST_NS=$2; TEST_ROOT=$3; TEST_COMPONENT=$4; PID=123\nrecord_restart_home || :",
             "sh", str(generation), namespace, root, component],
            check=True,
        )
        record = state / f"restart-home.{generation}"
        assert record.exists() == expected, (generation, namespace, component)
        if expected:
            assert record.read_text() == "8 mnt:[45]\n"
            record.unlink()
