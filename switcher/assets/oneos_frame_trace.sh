#!/system/bin/sh
# On-demand A/B capture. Run in the system being measured, then reproduce the gesture.
set -eu
PERFETTO=/system/bin/perfetto
[ -x "$PERFETTO" ] || { echo "Perfetto is unavailable in this system" >&2; exit 1; }
if ! "$PERFETTO" --query 2>/dev/null | grep -qF android.surfaceflinger.frametimeline; then
    echo "FrameTimeline is unavailable from this session; run as root or adb shell" >&2
    exit 1
fi
out=$(mktemp -d /storage/emulated/0/Download/OneOS-frames-XXXXXX)
part="$out/frames.tmp"
trap 'rm -f "$part"; rmdir "$out" 2>/dev/null || :' EXIT
echo "Recording for 15 seconds. Open Home and repeat the gesture now."
"$PERFETTO" --txt -c - -o - > "$part" <<'CONFIG'
buffers { size_kb: 32768 fill_policy: RING_BUFFER }
duration_ms: 15000
data_sources { config { name: "android.surfaceflinger.frametimeline" } }
data_sources { config {
    name: "linux.ftrace"
    ftrace_config {
        ftrace_events: "sched/sched_switch"
        ftrace_events: "sched/sched_waking"
        ftrace_events: "power/cpu_frequency"
    }
} }
data_sources { config { name: "linux.process_stats" process_stats_config { scan_all_processes_on_start: true } } }
CONFIG
[ -s "$part" ] || { echo "Perfetto returned an empty trace" >&2; exit 1; }
mv "$part" "$out/frames.perfetto-trace"
echo "$out/frames.perfetto-trace"
