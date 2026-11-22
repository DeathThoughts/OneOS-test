#!/system/bin/sh
# Manual A/B for ColorOS 17 PYB110 on SM-S911B. Run as root in init's mount
# namespace while the guest is active. Keep both arms at animation level 1.
# Return to One UI normally if the trial is interrupted by SIGKILL. Returning
# ends the guest task, and the change was only to that task's nice value.
PATH=/system/bin
export PATH

STAGE=/data/local/tmp/oneos
process_name=com.android.systemui
executable=
TRIAL_NICE=-10

fail() {
    echo "PYB110 scheduler trial: $*" >&2
    exit 1
}

case "$#:$1" in
    1:--check|2:--check) target=${2-render} ;;
    2:--run|3:--run)
        case "$2" in
            [1-9]|[1-9][0-9]|1[01][0-9]|120) duration=$2 ;;
            *) fail 'duration must be 1 through 120 seconds' ;;
        esac
        target=${3-render} ;;
    *) fail 'usage: pyb110-scheduler-trial.sh --check [TARGET] | --run SECONDS [TARGET]' ;;
esac
case "$target" in
    render)
        thread_name=RenderThread
        thread_label='SystemUI RenderThread'
        BASELINE_NICE=-4 ;;
    main)
        thread_name=
        thread_label='SystemUI main thread'
        BASELINE_NICE=0 ;;
    unirender-engine)
        process_name=surfaceflinger
        executable=/system/bin/surfaceflinger
        thread_name=UniRenderEngine
        thread_label='SurfaceFlinger UniRenderEngine'
        BASELINE_NICE=-10
        TRIAL_NICE=-12 ;;
    *) fail 'target must be render, main or unirender-engine' ;;
esac

[ "$(id -u)" = 0 ] || fail 'run as root'
host_ns=$(readlink /proc/1/ns/mnt) || fail 'cannot read init mount namespace'
[ -n "$host_ns" ] && [ "$(readlink /proc/self/ns/mnt)" = "$host_ns" ] ||
    fail "run in init's mount namespace"
[ "$(getprop ro.product.model)" = SM-S911B ] ||
    fail 'this trial targets the SM-S911B'

[ -d "$STAGE" ] && [ ! -L "$STAGE" ] && [ "$(stat -c %u "$STAGE")" = 0 ] ||
    fail 'missing root-owned OneOS stage'
marker=$STAGE/.oneos_stage
record=$STAGE/guest_mntns
[ -f "$marker" ] && [ ! -L "$marker" ] &&
    grep -qx '  "image": "coloros17-pyb110",' "$marker" ||
    fail 'PYB110 is not staged'
[ -f "$record" ] && [ ! -L "$record" ] || fail 'missing active guest namespace'
guest_ns=$(cat "$record") || fail 'cannot read active guest namespace'
case "$guest_ns" in
    'mnt:['[0-9]*']') ;;
    *) fail 'invalid active guest namespace' ;;
esac
[ "$guest_ns" != "$host_ns" ] || fail 'guest namespace matches the host'
guest_root=$(stat -Lc '%d:%i' "$STAGE/rootfs") || fail 'cannot read staged guest root'
[ -n "$guest_root" ] || fail 'empty staged guest root identity'

guest_process_namespace() {
    ns=$(readlink "$1/ns/mnt" 2>/dev/null) || return 1
    # Zygote gives apps descendant mount namespaces. All use the staged root.
    [ -n "$ns" ] && [ "$ns" != "$host_ns" ] &&
        [ "$(stat -Lc '%d:%i' "$1/root" 2>/dev/null)" = "$guest_root" ] || return 1
    if [ -n "$executable" ]; then
        # guest_start.sh launches this exact path with no arguments.
        [ "$(tr '\000' '\n' < "$1/cmdline" 2>/dev/null)" = "$executable" ] || return 1
        guest_exe=$(stat -Lc '%d:%i' "$1/root$executable" 2>/dev/null) || return 1
        [ -n "$guest_exe" ] && [ "$(stat -Lc '%d:%i' "$1/exe" 2>/dev/null)" = "$guest_exe" ] || return 1
    else
        [ "$(tr '\000' '\n' < "$1/cmdline" 2>/dev/null | head -n 1)" = "$process_name" ] || return 1
    fi
    echo "$ns"
}

task_info() {
    # Remove PID and parenthesized comm before reading renumbered tail fields.
    # Fields 19 (nice), 22 (starttime), and 41 (policy) then become $17, $20, $39.
    awk -v tid="$2" '
        $1 != tid { exit 1 }
        sub(/^.*\) /, "") && NF >= 39 &&
            $17 ~ /^-?[0-9]+$/ && $20 ~ /^[0-9]+$/ && $39 ~ /^[0-9]+$/ {
            print $20 ":" $39 ":" $17
            found = 1
        }
        END { if (!found) exit 1 }
    ' "$1/stat"
}

process=
for pid in $(pidof "$process_name" 2>/dev/null); do
    case "$pid" in ''|0|1|*[!0-9]*) continue ;; esac
    candidate=/proc/$pid
    candidate_ns=$(guest_process_namespace "$candidate") || continue
    [ -z "$process" ] || fail "multiple guest $process_name processes"
    process=$candidate
    process_ns=$candidate_ns
done
[ -n "$process" ] || fail "no $process_name in the active guest namespace"
pid=${process##*/}
process_info=$(task_info "$process" "$pid") || fail "cannot identify $process_name"
process_start=${process_info%%:*}

task=$process/task/$pid
if [ -n "$thread_name" ]; then
    task=
    for candidate in "$process"/task/*; do
        [ -r "$candidate/comm" ] || continue
        [ "$(cat "$candidate/comm" 2>/dev/null)" = "$thread_name" ] || continue
        [ -z "$task" ] || fail "more than one $thread_label"
        task=$candidate
    done
    [ -n "$task" ] || fail "no $thread_label"
fi
tid=${task##*/}
case "$tid" in ''|0|1|*[!0-9]*) fail "invalid $thread_label TID" ;; esac
thread_info=$(task_info "$task" "$tid") || fail "cannot identify $thread_label"
thread_start=${thread_info%%:*}

same_target_thread() {
    if [ -n "$thread_name" ]; then
        [ "$tid" != "$pid" ] && [ "$(cat "$task/comm" 2>/dev/null)" = "$thread_name" ]
    else
        [ "$tid" = "$pid" ] && [ "$thread_start" = "$process_start" ]
    fi
}

current_info() {
    [ -f "$record" ] && [ ! -L "$record" ] &&
        [ "$(cat "$record" 2>/dev/null)" = "$guest_ns" ] &&
        [ "$(stat -Lc '%d:%i' "$STAGE/rootfs" 2>/dev/null)" = "$guest_root" ] &&
        [ "$(guest_process_namespace "$process")" = "$process_ns" ] &&
        [ "$(readlink "$task/ns/mnt" 2>/dev/null)" = "$process_ns" ] &&
        same_target_thread || return 1
    current_process=$(task_info "$process" "$pid" 2>/dev/null) || return 1
    [ "${current_process%%:*}" = "$process_start" ] || return 1
    current_task=$(task_info "$task" "$tid" 2>/dev/null) || return 1
    [ "${current_task%%:*}" = "$thread_start" ] || return 1
    print_info=${current_task#*:}
    echo "$print_info"
}

baseline="0:$BASELINE_NICE"
trial="0:$TRIAL_NICE"
observed=$(current_info) || fail 'guest thread identity changed'
[ "$observed" = "$baseline" ] ||
    fail "expected SCHED_OTHER and nice $BASELINE_NICE, observed policy:nice $observed"
anim_level=$(getprop persist.sys.oplus.anim_level)
echo "Verified guest $thread_label PID=$pid TID=$tid nice=$BASELINE_NICE animation_level=${anim_level:-unset}"
[ "$1" = --run ] || exit 0
[ "$anim_level" = 1 ] || fail 'activate ColorOS animation level 1 for this trial'

restore() {
    status=$?
    trap - EXIT
    observed=$(current_info) || {
        echo 'Trial ended after the guest thread changed or disappeared' >&2
        exit 1
    }
    case "$observed" in
        "$baseline") ;;
        "$trial")
            if ! /system/bin/toybox renice -n "$((BASELINE_NICE - TRIAL_NICE))" -p "$tid" ||
                [ "$(current_info)" != "$baseline" ]; then
                fail 'priority restoration could not be verified. Return to One UI to end the guest task.'
            fi
            echo "Restored guest $thread_label TID=$tid nice=$BASELINE_NICE" ;;
        *) fail 'another policy changed the thread priority during the trial' ;;
    esac
    exit "$status"
}

trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
[ "$(getprop persist.sys.oplus.anim_level)" = 1 ] || fail 'animation level changed before applying priority'
[ "$(current_info)" = "$baseline" ] || fail 'guest thread changed before applying priority'
trap restore EXIT
/system/bin/toybox renice -n "$((TRIAL_NICE - BASELINE_NICE))" -p "$tid" ||
    fail 'could not apply trial priority'
[ "$(current_info)" = "$trial" ] || fail 'trial priority could not be verified'
echo "Trial active for $duration seconds with guest $thread_label nice=$TRIAL_NICE"
sleep "$duration" </dev/null >/dev/null 2>&1 &
wait $!
