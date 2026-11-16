#!/usr/bin/env python3
"""Testes de scripts/analyze-session.py com uma sessão sintética.

A sessão é criada e empacotada em .tgz dentro do próprio teste (tarfile), com
o layout do pull-run-logs.sh e um diretório raiz. O analisador é executado por
subprocess e o relatório é conferido no stdout; os casos de tgz ausente,
tgz vazio/corrompido, links perigosos e de sessão mínima também são verificados.
"""
import importlib.util
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANALYZER = ROOT / "scripts" / "analyze-session.py"

SESSION_FILES = {
    "session.env": """\
ONEOS_PROFILE=s23
ONEOS_GUEST_FORM=phone
ONEOS_HEAT_CAP=1
ONEOS_GUEST_THERMAL_MAP=1
ONEOS_FULL_LOGS=1
ONEOS_GUEST_SEC_IMS=0
ONEOS_GUEST_EDM_STUB=0
ONEOS_GUEST_HIDE_GMS=0
""",
    ".oneos_stage": """\
{
  "schema": "oneos.stage/2",
  "versionCode": 123,
  "profile": "s23",
  "image": "hyperos3-s23",
  "set": "s23-set",
  "created": 1792000000
}
""",
    "diag_series.txt": """\
# 2026-11-15 OneOS session sampler, one line every 30 s
10:00:00 thermal=0 skin=44.0 policy0=2016000/2016000 policy4=2802000/2802000 policy7=3360000/3360000 blur=true disable_window_blurs=0 battery=80%,Discharging
10:00:30 thermal=0 skin=46.0 policy0=1612800/2016000 policy4=2241600/2802000 policy7=2688000/3360000 blur=true disable_window_blurs=0 battery=79%,Discharging
10:01:00 thermal=1 skin=48.0 policy0=1612800/2016000 policy4=2241600/2802000 policy7=2688000/3360000 blur=true disable_window_blurs=0 battery=79%,Discharging
10:01:30 thermal=1 skin=50,5 policy0=1008000/2016000 policy4=1401000/2802000 policy7=1680000/3360000 blur=false disable_window_blurs=1 battery=78%,Discharging
# 10:02:00 the return started: sampler stopped
# 10:02:30 no guest namespace record: sampler stopped
""",
    "cpu_top.log": """\
# 2026-11-15 OneOS session sampler: every 30 s, CPU totals and the six busiest processes
10:00:10 cpu  99% user 1% system | 90.0 dex2oat64 85.0 installd 80.0 artd 62.0 system_server 50.0 com.android.systemui 40.0 surfaceflinger
10:02:10 cpu  98% user 2% system | 92.0 dex2oat64 84.0 installd 82.0 artd 60.0 system_server 48.0 com.android.systemui 38.0 surfaceflinger
10:04:50 cpu  97% user 3% system | 94.0 dex2oat64 86.0 installd 78.0 artd 64.0 system_server 52.0 com.android.systemui 36.0 surfaceflinger
10:05:10 cpu  70% user 30% system | 70.0 dex2oat64 65.0 installd 60.0 artd 30.0 system_server 20.0 com.android.systemui 15.0 com.android.phone | forks/s 123 ctxt/s 45678
10:07:10 cpu  68% user 32% system | 68.0 dex2oat64 63.0 installd 62.0 artd 32.0 system_server 18.0 com.android.systemui 12.0 com.android.phone | forks/s - ctxt/s -
""",
    "heat_cap.log": """\
# 2026-11-15 OneOS heat cap: from 45, 47, 50 C skin every CPU policy and the GPU at 80, 65, 50% of their top clock
10:02:00 skin 47.2 C, level 0 to 1: 80%:policy0=1612800/2016000 policy4=2241600/2802000 policy7=2688000/3360000 gpu=545/719
10:03:00 skin 47.0 C: guest thermal status 1 (cmd thermalservice override-status 1: ok)
10:04:00 skin 50,2 C, level 1 to 2: 65%:policy0=1310400/2016000 policy4=1821300/2802000 policy7=2184000/3360000 gpu=348/719
10:05:00 skin 49.0 C, level 2 to 0: full clock:policy0=2016000/2016000 policy4=2802000/2802000 policy7=3360000/3360000 gpu=719/719
# 10:06:00 stopped (test)
""",
    "performance_handover.log": """\
10:00:00 heat cap on: the worker is not started (the heat cap owns the limits)
10:02:00 requested policy7=2688000; effective 2688000
10:02:00 requested gpu=545; effective 545
10:03:00 requested policy4=2241600
""",
    "oneos_logs/boot.log": """\
11-15 10:00:00.000  1000  1000 I SystemServer: Build.MODEL=SM-S911B
11-15 10:00:00.100  1000  1000 I SystemServer: ro.product.model=SM-S911B
11-15 10:00:00.200  1000  1000 I SystemServer: Build fingerprint: 'samsung/dm3qxxx/dm3q:15/AP4A.250105.002/12345678:user/release-keys'
11-15 10:00:00.300  1000  1000 I SystemServer: Build.VERSION.RELEASE=15
11-15 10:00:00.400  1000  1000 I SystemServer: Build.VERSION.SDK_INT=35
11-15 10:00:01.000   111   222 E ActivityManager: Could not find service foo for pid 111 after 123 ms
11-15 10:00:02.000   222   333 E ActivityManager: Could not find service foo for pid 222 after 456 ms
11-15 10:00:03.000   333   444 E ActivityManager: Could not find service foo for pid 333 after 789 ms
11-15 10:00:04.000   444   555 W PackageManager: waitForService: Service bar with port 1234 not found yet
11-15 10:00:05.000   555   666 W PackageManager: waitForService: Service bar with port 5678 not found yet
11-15 10:00:06.000   666   777 E ActivityManager: Could not find service comma for pid 666 after 13,67 ms
11-15 10:00:07.000   777   888 E ActivityManager: Could not find service comma for pid 777 after 13.67 ms
11-15 10:00:08.000   888   999 E ActivityManager: Could not find service offset for pid 888 at 12,5 units
11-15 10:00:09.000   999  1000 E ActivityManager: Could not find service offset for pid 999 at 12.5 units
""",
    "oneos_logs/takeover_inner.log": """\
[Pivoted Root] SF render rate: attempt 1: no physical displays listed yet
[Pivoted Root] SF render rate: attempt 2: display 0: through mode 5 (60 Hz) to mode 1, pinned at 120 Hz: Result: Parcel(00000000)
[Pivoted Root] SF 0 displayManagerPolicy=default mode ID: 1, allowGroupSwitching = 0
""",
    "diag_20/surfaceflinger.txt": """\
Display 0:
  HWC missed frame count: 43
  GPU missed frame count: 5
  Total missed frame count: 48
30.00fps: 0d00:05:32.489 app phase: 1000000 ns SF phase: 1000000 ns app duration: 13666667 ns SF duration: 10333333 ns
""",
    "diag_20/frames.txt": """\
== com.google.android.apps.nexuslauncher
Total frames: 100
Janky frames: 20 (20.00%)
50th percentile: 5ms
90th percentile: 9ms
95th percentile: 12ms
99th percentile: 69ms
durations app/SF: 13,67 / 10,33 ms
durations app/SF: 13.67 / 10.33 ms
""",
    "diag_20/composition.txt": """\
      1 HWC missed frame count: 43
      1 GPU missed frame count: 5
      1 usesDeviceComposition: false
""",
    "diag_20/throttle.txt": """\
/sys/devices/system/cpu/cpufreq/policy0 cur/max/hw 900000 1612800 2016000
/sys/devices/system/cpu/cpufreq/policy4 cur/max/hw 1200000 2241600 2802000
/sys/devices/system/cpu/cpufreq/policy7 cur/max/hw 1400000 2688000 3360000
gpu cur 220000000 max 545000000
""",
}


def write_session(root: Path, files: dict) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def pack(root: Path, tgz: Path, prefix: str = "") -> None:
    with tarfile.open(tgz, "w:gz") as tar:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            arcname = f"{prefix}/{rel}" if prefix else rel
            tar.add(path, arcname=arcname)


def run_analyzer(tgz: Path):
    return subprocess.run(
        [sys.executable, str(ANALYZER), str(tgz)],
        capture_output=True,
        text=True,
    )


def section(text: str, number: int) -> str:
    start = text.index(f"== {number}.")
    if number == 6:
        return text[start:]
    end = text.index(f"== {number + 1}.", start)
    return text[start:end]


def load_analyzer():
    spec = importlib.util.spec_from_file_location("analyze_session", ANALYZER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AnalyzeSessionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oneos-analyze-test-")
        self.root = Path(self.temp.name)
        self.tree = self.root / "oneos_session_full"
        write_session(self.tree, SESSION_FILES)
        self.tgz = self.root / "oneos_session_full.tgz"
        pack(self.tree, self.tgz, prefix="oneos_session_full")

    def tearDown(self):
        self.temp.cleanup()

    def run_tree(self, name: str, files: dict):
        root = self.root / name
        write_session(root, files)
        tgz = self.root / f"{name}.tgz"
        pack(root, tgz)
        return run_analyzer(tgz)

    def test_full_session_report(self):
        result = run_analyzer(self.tgz)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        out = result.stdout

        # 1. modelo, sistema e session.env
        self.assertIn("SM-S911B", out)
        self.assertIn("Android 15", out)
        self.assertIn("ONEOS_HEAT_CAP=1", out)
        self.assertIn("cap térmico ligado", out)
        self.assertIn("ONEOS_GUEST_THERMAL_MAP=1", out)
        self.assertIn("hyperos3-s23", out)

        # 2. ceilings e percentuais; comentários do sampler não são leituras
        self.assertIn("4 leituras", out)
        self.assertNotIn("6 leituras", out)
        self.assertIn("policy7", out)
        self.assertIn("2688000", out)
        self.assertIn("80%", out)
        self.assertIn("50%", out)
        self.assertIn("545 MHz", out)
        self.assertIn("policy7=2688000 (1x) -> efetivo 2688000 (1x)", out)
        self.assertIn("gpu=545 (1x) -> efetivo 545 (1x)", out)
        self.assertIn("policy4=2241600 (1x)", out)

        # 3. top por CPU, sem dex2oat64/installd/artd e com corte de 5 min
        sec3 = section(out, 3)
        self.assertIn("system_server", sec3)
        self.assertIn("62.0%", sec3)
        self.assertIn("Primeiros 5 min", sec3)
        self.assertIn("Resto da sessão", sec3)
        first_part, rest_part = sec3.split("Resto da sessão", 1)
        self.assertNotIn("com.android.phone", first_part)
        self.assertIn("com.android.phone", rest_part)
        first_entries = [
            line for line in first_part.splitlines()
            if re.match(r"\s+\d+\. ", line)
        ]
        rest_entries = [
            line for line in rest_part.splitlines()
            if re.match(r"\s+\d+\. ", line)
        ]
        self.assertLessEqual(len(first_entries), 5)
        self.assertLessEqual(len(rest_entries), 5)
        self.assertNotIn("dex2oat64", sec3)
        # The sampler's "| forks/s N ctxt/s N" tail (bceaef29fb128e94) is not a process.
        self.assertNotIn("ctxt", sec3)
        self.assertNotIn("installd", sec3)
        self.assertNotIn("artd", sec3)

        # 4. kick, durações, missed frames e p99
        sec4 = section(out, 4)
        self.assertIn("sucesso na tentativa 2", sec4)
        self.assertIn("displayManagerPolicy", sec4)
        self.assertIn("HWC: 43", sec4)
        self.assertIn("TOTAL: 48", sec4)
        self.assertIn("GPU: 5", sec4)
        self.assertIn("app 13.7 ms / SF 10.3 ms", sec4)
        self.assertIn("média de 2 par(es)", sec4)
        self.assertIn("p99=69 ms", sec4)

        # 5. mensagens normalizadas (incluindo vírgula decimal)
        sec5 = section(out, 5)
        self.assertIn("3x  Could not find service foo for pid=<N> after <DUR>", sec5)
        self.assertIn("2x  waitForService: Service bar with port=<N> not found yet", sec5)
        self.assertIn(
            "2x  Could not find service comma for pid=<N> after <DUR>", sec5
        )
        self.assertIn(
            "2x  Could not find service offset for pid=<N> at <N> units", sec5
        )

        # 6. skin máxima e horário (vírgula decimal em diag e heat_cap)
        sec6 = section(out, 6)
        self.assertIn("Skin máxima (diag_series.txt): 50.5 °C em 10:01:30", sec6)
        self.assertIn("Skin máxima (heat_cap.log): 50.2 °C", sec6)

        for number in range(1, 7):
            self.assertIn(f"== {number}.", out)
        self.assertIn("Fim do relatório.", out)
        self.assertLessEqual(len(out.splitlines()), 150)

    def test_missing_tgz_fails_with_clear_message(self):
        missing = self.root / "nao_existe.tgz"
        result = run_analyzer(missing)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("não encontrada", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_empty_tgz_fails_with_clear_message(self):
        empty = self.root / "vazio.tgz"
        empty.write_bytes(b"")
        result = run_analyzer(empty)
        self.assertEqual(2, result.returncode)
        self.assertIn("vazio", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_corrupt_nonempty_tgz_fails_with_clear_message(self):
        corrupt = self.root / "corrompido.tgz"
        corrupt.write_bytes(b"isto nao e um tar\n" * 20)
        result = run_analyzer(corrupt)
        self.assertEqual(2, result.returncode)
        self.assertIn("não foi possível ler o tgz", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_tgz_with_link_is_rejected(self):
        tgz = self.root / "link.tgz"
        with tarfile.open(tgz, "w:gz") as tar:
            payload = self.root / "payload.txt"
            payload.write_text("x", encoding="utf-8")
            tar.add(payload, arcname="payload.txt")
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        result = run_analyzer(tgz)
        self.assertEqual(2, result.returncode)
        self.assertIn("não foi possível ler o tgz", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_only_session_env_does_not_crash(self):
        root = self.root / "so_env"
        write_session(root, {"session.env": SESSION_FILES["session.env"]})
        tgz = self.root / "so_env.tgz"
        pack(root, tgz)
        result = run_analyzer(tgz)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("ONEOS_HEAT_CAP=1", result.stdout)
        self.assertIn("não encontrado", result.stdout)
        for number in range(1, 7):
            self.assertIn(f"== {number}.", result.stdout)
        self.assertIn("Skin máxima: não encontrado", result.stdout)
        self.assertIn("Fim do relatório.", result.stdout)
        self.assertLessEqual(len(result.stdout.splitlines()), 150)

    def test_head_layout_is_accepted(self):
        files = {
            "oneos_logs/session.env": SESSION_FILES["session.env"],
            "head/boot.log": SESSION_FILES["oneos_logs/boot.log"],
            "diag_90/surfaceflinger.txt": SESSION_FILES["diag_20/surfaceflinger.txt"],
        }
        result = self.run_tree("head_layout", files)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("SM-S911B", result.stdout)
        self.assertIn("oneos_logs/session.env", result.stdout)
        self.assertIn("HWC: 43", section(result.stdout, 4))

    def test_kick_retry_number_from_neighbor_line(self):
        files = dict(SESSION_FILES)
        files["oneos_logs/takeover_inner.log"] = (
            "[Pivoted Root] SF render rate: attempt 1: no physical displays listed yet\n"
            "[Pivoted Root] SF render rate: display 0: through mode 5 (60 Hz) "
            "to mode 1, pinned at 120 Hz\n"
            "[Pivoted Root] SF render rate: display listed on retry 5/30\n"
        )
        result = self.run_tree("kick_retry", files)
        self.assertEqual(0, result.returncode, result.stderr)
        sec4 = section(result.stdout, 4)
        self.assertIn("sucesso na tentativa 5", sec4)
        self.assertNotIn("sucesso na tentativa 2", sec4)

    def test_kick_exhaustion_real_sequence_uses_30_retries(self):
        files = dict(SESSION_FILES)
        files["oneos_logs/takeover_inner.log"] = (
            "[Pivoted Root] SF render rate: attempt 1: no physical displays listed yet\n"
            "[Pivoted Root] SF render rate: no physical displays after 60 s "
            "(30/30 retries); left to the panel keeper\n"
        )
        result = self.run_tree("kick_exhausted_real", files)
        self.assertEqual(0, result.returncode, result.stderr)
        sec4 = section(result.stdout, 4)
        self.assertIn("esgotou após 30 tentativas/60 s", sec4)
        self.assertIn("31 kicks", sec4)
        self.assertIn('"no physical displays listed yet": 1 ocorrência(s)', sec4)
        self.assertIn("exaustão:", sec4)
        self.assertNotIn("esgotou após 1", sec4)
        self.assertNotIn("31 ocorrência(s)", sec4)

    def test_kick_exhaustion_old_format_assumes_30_retries(self):
        files = dict(SESSION_FILES)
        files["oneos_logs/takeover_inner.log"] = (
            "[Pivoted Root] SF render rate: attempt 1: no physical displays listed yet\n"
            "[Pivoted Root] SF render rate: no physical displays after 60 s; "
            "left to the panel keeper\n"
        )
        result = self.run_tree("kick_exhausted_old", files)
        self.assertEqual(0, result.returncode, result.stderr)
        sec4 = section(result.stdout, 4)
        self.assertIn("esgotou após 30 tentativas/60 s", sec4)
        self.assertIn("31 kicks", sec4)
        self.assertIn('"no physical displays listed yet": 1 ocorrência(s)', sec4)

    def test_kick_exhaustion_old_retry_lines_use_max(self):
        files = dict(SESSION_FILES)
        lines = [
            "[Pivoted Root] SF render rate: attempt 1: no physical displays listed yet",
            "[Pivoted Root] SF render rate: retry 7/30: no physical displays listed yet",
            "[Pivoted Root] SF render rate: no physical displays after 60 s; "
            "left to the panel keeper",
        ]
        files["oneos_logs/takeover_inner.log"] = "\n".join(lines) + "\n"
        result = self.run_tree("kick_exhausted_retry_lines", files)
        self.assertEqual(0, result.returncode, result.stderr)
        sec4 = section(result.stdout, 4)
        self.assertIn("esgotou após 7 tentativas/60 s", sec4)
        self.assertIn("8 kicks", sec4)
        self.assertIn('"no physical displays listed yet": 2 ocorrência(s)', sec4)

    def test_kick_exhaustion_explicit_7_of_30_retries(self):
        files = dict(SESSION_FILES)
        files["oneos_logs/takeover_inner.log"] = (
            "[Pivoted Root] SF render rate: attempt 1: no physical displays listed yet\n"
            "[Pivoted Root] SF render rate: no physical displays after 60 s "
            "(7/30 retries); left to the panel keeper\n"
        )
        result = self.run_tree("kick_exhausted_7", files)
        self.assertEqual(0, result.returncode, result.stderr)
        sec4 = section(result.stdout, 4)
        self.assertIn("esgotou após 7 tentativas/60 s", sec4)
        self.assertIn("8 kicks", sec4)
        self.assertIn('"no physical displays listed yet": 1 ocorrência(s)', sec4)

    def test_kick_no_modes_negative_lines_are_not_success(self):
        files = dict(SESSION_FILES)
        files["oneos_logs/takeover_inner.log"] = (
            "[Pivoted Root] SF render rate: attempt 1: no physical displays listed yet\n"
            "[Pivoted Root] SF render rate: display 0: no modes in the dump, left alone\n"
            "[Pivoted Root] SF render rate: display 0: through mode 5 sem sucesso\n"
            "[Pivoted Root] SF render rate: display 0: pinned at 120 Hz not succeeded\n"
        )
        result = self.run_tree("kick_no_modes", files)
        self.assertEqual(0, result.returncode, result.stderr)
        sec4 = section(result.stdout, 4)
        self.assertIn("sem resultado final registrado", sec4)
        self.assertNotIn("resultado: sucesso", sec4)
        self.assertIn('"no physical displays listed yet": 1 ocorrência(s)', sec4)

    def test_diag_series_sampler_comments_are_not_readings(self):
        files = {
            "session.env": SESSION_FILES["session.env"],
            "diag_series.txt": (
                "# 10:00:00 oneos sampler header\n"
                "# 10:00:30 the return started: sampler stopped\n"
                "# 10:01:00 no guest namespace record: sampler stopped\n"
                "10:02:00 skin=41,5 policy0=2016000/2016000\n"
            ),
        }
        result = self.run_tree("diag_comments", files)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("1 leituras", result.stdout)
        self.assertNotIn("3 leituras", result.stdout)
        self.assertIn("Skin máxima (diag_series.txt): 41.5 °C", result.stdout)

    def test_diag_series_two_readings_with_comment_does_not_crash(self):
        files = {
            "session.env": SESSION_FILES["session.env"],
            "diag_series.txt": (
                "# 10:00:30 the return started: sampler stopped\n"
                "10:01:00 skin=41,5 policy0=2016000/2016000\n"
                "10:01:30 skin=42.0 policy0=1612800/2016000\n"
            ),
        }
        result = self.run_tree("diag_two_readings", files)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("2 leituras", result.stdout)
        self.assertIn("Skin máxima (diag_series.txt): 42 °C", result.stdout)

    def test_stage_created_non_numeric_shows_raw_value(self):
        files = dict(SESSION_FILES)
        files[".oneos_stage"] = """\
{
  "profile": "s23",
  "image": "hyperos3-s23",
  "created": {"bad": 1}
}
"""
        result = self.run_tree("stage_created", files)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("criado {'bad': 1}", result.stdout)

    def test_many_failure_lines_still_have_section_6_and_footer(self):
        files = dict(SESSION_FILES)
        lines = []
        for i in range(300):
            lines.append(
                f"11-15 10:00:{i % 60:02d}.000  {1000 + i}  {2000 + i} "
                f"E ActivityManager: Could not find service svc{i} for pid {i} "
                f"after {i} ms"
            )
        files["oneos_logs/boot.log"] = "\n".join(lines) + "\n"
        result = self.run_tree("many_failures", files)
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout
        self.assertLessEqual(len(out.splitlines()), 150)
        self.assertIn("== 6. Temperatura de skin ==", out)
        self.assertIn("Skin máxima (diag_series.txt): 50.5 °C", out)
        self.assertIn("Fim do relatório.", out)

    def test_limit_report_truncates_details_and_keeps_skin_and_footer(self):
        module = load_analyzer()
        lines = [
            "OneOS — análise offline de sessão (somente leitura)",
            "Sessão: x.tgz (1 B)",
            "",
            "== 1. Modelo, sistema e sessão ==",
        ]
        lines.extend(f"  detalhe {i}" for i in range(300))
        lines.extend([
            "",
            "== 6. Temperatura de skin ==",
            "Skin máxima (diag_series.txt): 50.5 °C em 10:01:30",
            "",
            "--",
            "Fim do relatório. Nenhum arquivo da sessão foi alterado.",
        ])
        out = module.limit_report(lines)
        self.assertLessEqual(len(out), 150)
        self.assertIn("== 6. Temperatura de skin ==", out)
        self.assertIn("Skin máxima (diag_series.txt): 50.5 °C em 10:01:30", out)
        self.assertIn("Fim do relatório. Nenhum arquivo da sessão foi alterado.", out)
        self.assertTrue(any("relatório truncado" in line for line in out))


if __name__ == "__main__":
    unittest.main()
