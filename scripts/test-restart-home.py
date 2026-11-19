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
        *resolve-activity*)
            if [ "$elapsed" -ge "$TEST_RESOLVE_AFTER" ]; then
                printf '%s\n' 'com.example.launcher/.Home'
            fi
            ;;
        *dumpsys*)
            if [ "$elapsed" -ge "$TEST_RESUME_AFTER" ]; then
                printf '%s\n' "topResumedActivity=ActivityRecord{1 $TEST_USER $TEST_COMPONENT t2}"
            else
                printf '%s\n' 'topResumedActivity=ActivityRecord{1 u0 com.example.setup/.Finishing t2}'
            fi
            ;;
    esac
}
elapsed=0
read() {
    case "$2" in
        started) started=1000 ;;
        now) now="$((1000 + elapsed)).0" ;;
        *) return 1 ;;
    esac
}
sleep() {
    elapsed=$((elapsed + $1))
    if [ "$elapsed" -eq "$TEST_CHANGE_AFTER" ]; then
        printf '9\n' > "$STATE/softreboot.gen"
    fi
}
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
    for name, generation, namespace, root, component, user, resolve_after, resume_after, change_after, expected in (
        ("late Home", 8, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u0", 0, 18, -1, True),
        ("package service warming up", 8, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u0", 4, 0, -1, True),
        ("stale generation", 7, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u0", 0, 0, -1, False),
        ("host namespace", 8, "mnt:[1]", "12:33", "com.example.launcher/.Home", "u0", 0, 0, -1, False),
        ("wrong root", 8, "mnt:[45]", "12:99", "com.example.launcher/.Home", "u0", 0, 0, -1, False),
        ("wrong activity", 8, "mnt:[45]", "12:33", "com.example.launcher/.HomeOther", "u0", 0, 0, -1, False),
        ("wrong user", 8, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u10", 0, 0, -1, False),
        ("new restart while waiting", 8, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u0", 0, 18, 6, False),
        ("Home after deadline", 8, "mnt:[45]", "12:33", "com.example.launcher/.Home", "u0", 0, 122, -1, False),
        ("descendant namespace", 8, "mnt:[46]", "12:33", "com.example.launcher/com.example.launcher.Home", "u0", 0, 0, -1, True),
    ):
        (state / "softreboot.gen").write_text("8\n")
        subprocess.run(
            ["sh", "-c", mock + script + "\nGEN=$1; TEST_NS=$2; TEST_ROOT=$3; TEST_COMPONENT=$4; TEST_USER=$5; "
             "TEST_RESOLVE_AFTER=$6; TEST_RESUME_AFTER=$7; TEST_CHANGE_AFTER=$8; STATE=$9; PID=123\nrecord_restart_home || :",
             "sh", str(generation), namespace, root, component, user, str(resolve_after), str(resume_after),
             str(change_after), str(state)],
            check=True,
            timeout=10,
        )
        record = state / f"restart-home.{generation}"
        assert record.exists() == expected, name
        if expected:
            assert record.read_text() == "8 mnt:[45]\n"
            record.unlink()
