#!/usr/bin/env python3
"""Relatório offline de uma sessão OneOS (oneos_session_full.tgz).

Uso: scripts/analyze-session.py <oneos_session_full.tgz>

O arquivo é extraído num diretório temporário (removido ao sair) e nada é
alterado. O script só usa a biblioteca padrão do Python 3 e aceita o layout do
pull-run-logs.sh com ou sem diretório raiz: session.env/diag_series.txt/
cpu_top.log na raiz ou em oneos_logs/, logcat em oneos_logs/boot.log* ou
head/boot.log*, e as rodadas diag_* em qualquer nível.

O relatório tem no máximo ~150 linhas; o que faltar é marcado como
"não encontrado" em vez de interromper a análise.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

MAX_REPORT_LINES = 150

ENV_KEYS = (
    "ONEOS_PROFILE",
    "ONEOS_GUEST_FORM",
    "ONEOS_HEAT_CAP",
    "ONEOS_GUEST_THERMAL_MAP",
    "ONEOS_FULL_LOGS",
    "ONEOS_GUEST_SEC_IMS",
    "ONEOS_GUEST_EDM_STUB",
    "ONEOS_GUEST_HIDE_GMS",
)

FAIL_PHRASES = (
    "could not find",
    "not found",
    "not declared",
    "waitforservice",
    "tried to start",
    "failed to get service",
)
FAIL_RE = re.compile("|".join(re.escape(p) for p in FAIL_PHRASES), re.I)

THREADTIME_RE = re.compile(
    r"^(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s+"
    r"\d+\s+\d+\s+[VDIWEFS]\s+(.*)$"
)
TS_PREFIX_RE = re.compile(
    r"^(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})\b"
)

POLICY_RE = re.compile(r"\b(policy\d+)=(-?\d+)/(-?\d+)")
SKIN_RE = re.compile(r"\bskin=(-?\d+(?:[.,]\d+)?)")
HMS_RE = re.compile(r"^#?\s*(\d{1,2}):(\d{2}):(\d{2})\b")

EXCLUDED_PROC_RE = re.compile(r"^(?:dex2oat|installd|artd)", re.I)
CPU_USER_RE = re.compile(r"(\d+(?:[.,]\d+)?)%\s*us(?:er|r)?\b", re.I)
CPU_SYS_RE = re.compile(r"(\d+(?:[.,]\d+)?)%\s*sys(?:tem)?\b", re.I)
CPU_IDLE_RE = re.compile(r"(\d+(?:[.,]\d+)?)%\s*id(?:le)?\b", re.I)
FORKS_RATE_RE = re.compile(r"\bforks/s\s+(\d+(?:[.,]\d+)?)\b", re.I)
CTXT_RATE_RE = re.compile(r"\bctxt/s\s+(\d+(?:[.,]\d+)?)\b", re.I)


# ---------------------------------------------------------------------------
# pequenos utilitários
# ---------------------------------------------------------------------------

def fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MiB"
    if n >= 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n} B"


def parse_hms(text: str) -> Optional[int]:
    m = HMS_RE.search(text)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def hms(seconds: int) -> str:
    seconds = int(seconds) % 86400
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def fmt_duration(seconds: float) -> str:
    if seconds <= 0:
        return "0 min"
    if seconds < 90:
        return f"{int(round(seconds))} s"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:.0f} min" if abs(minutes - round(minutes)) < 0.1 else f"{minutes:.1f} min"
    return f"{int(minutes // 60)} h {int(minutes % 60)} min"


def pct_str(value: int, top: int) -> str:
    if top <= 0:
        return "?"
    pct = value * 100.0 / top
    if abs(pct - round(pct)) < 0.05:
        return f"{int(round(pct))}%"
    return f"{pct:.1f}%"


def fmt_num(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def median_value(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def shorten(text: str, limit: int = 180) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def to_ms(value: float, unit: str) -> float:
    unit = unit.lower().replace("µ", "u")
    if unit == "ns":
        return value / 1_000_000.0
    if unit == "us":
        return value / 1_000.0
    if unit == "s":
        return value * 1000.0
    return value


def mhz(value: int) -> float:
    """gpu_max_clock/gpu_maxclk vêm em MHz; alguns nós, em Hz."""
    return value / 1_000_000.0 if value > 100_000 else float(value)


def natural_policy_key(name: str):
    m = re.search(r"(\d+)$", name)
    return (0, int(m.group(1))) if m else (1, name)


def read_text_lines(path: Path) -> Iterable[str]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            yield line.rstrip("\n")


def parse_thread_ts(line: str) -> Tuple[Optional[Tuple[int, int, int, int, int, int]], str]:
    m = TS_PREFIX_RE.match(line)
    if not m:
        return None, ""
    mo, da, hh, mm, ss, ms = (int(x) for x in m.groups())
    return (mo, da, hh, mm, ss, ms), f"{mo:02d}-{da:02d} {hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"


def split_logcat(line: str) -> Tuple[str, str]:
    m = THREADTIME_RE.match(line)
    if not m:
        return "", line.strip()
    rest = m.group(7)
    if " : " in rest:
        tag, _, msg = rest.partition(" : ")
    else:
        tag, _, msg = rest.partition(": ")
    return tag.strip(), msg.strip()


@lru_cache(maxsize=16384)
def normalize_message(message: str) -> str:
    """Normaliza números variáveis para contar mensagens repetidas.

    Mensagens brutas idênticas (o caso comum em logs grandes) vêm do cache:
    a normalização continua linear no número de linhas, sem repetir regex.
    """
    s = message
    s = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        "<UUID>",
        s,
    )
    s = re.sub(r"0x[0-9a-fA-F]+", "<HEX>", s)
    s = re.sub(r"\b[0-9a-fA-F]{8,}\b", "<HEX>", s)
    s = re.sub(
        r"\b\d+(?:[.,]\d+)?\s*(?:ms|us|µs|ns|seconds?|secs?|s)\b",
        "<DUR>",
        s,
        flags=re.I,
    )
    s = re.sub(r"\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b", "<TIME>", s)
    s = re.sub(
        r"\b(pid|tid|uid|gid|id|port|fd|handle|token|seq|code)\s*[=:#]?\s*\d+",
        r"\1=<N>",
        s,
        flags=re.I,
    )
    s = re.sub(r"#\d+", "#<N>", s)
    s = re.sub(r"\[\d+\]", "[<N>]", s)
    s = re.sub(r"\b\d+(?:[.,]\d+)?\b", "<N>", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def file_fingerprint(path: Path) -> Tuple[int, str]:
    """Impressão barata para não contar boot.log e head/boot.log duas vezes."""
    st = path.stat()
    h = hashlib.sha1()
    h.update(str(st.st_size).encode())
    with open(path, "rb") as f:
        h.update(f.read(65536))
        if st.st_size > 65536:
            f.seek(max(0, st.st_size - 65536))
            h.update(f.read(65536))
    return st.st_size, h.hexdigest()


def join_numbers(values: Sequence[int], limit: int = 6) -> str:
    uniq = []
    for v in values:
        if v not in uniq:
            uniq.append(v)
    shown = ", ".join(str(v) for v in uniq[:limit])
    if len(uniq) > limit:
        shown += f" (+{len(uniq) - limit})"
    return shown


# ---------------------------------------------------------------------------
# arquivos da sessão
# ---------------------------------------------------------------------------

class Session:
    def __init__(self, root: Path):
        self.root = root
        self.files: List[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if not os.path.islink(os.path.join(dirpath, d))
            ]
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    if p.is_file() and not p.is_symlink():
                        self.files.append(p)
                except OSError:
                    continue
        self.files.sort(key=lambda p: self.rel(p).lower())

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def pick(self, name: str) -> Optional[Path]:
        matches = [p for p in self.files if p.name == name]
        if not matches:
            return None

        def key(p: Path):
            parts = p.relative_to(self.root).parts
            if "oneos_logs" in parts:
                pref = 1
            elif "head" in parts:
                pref = 2
            else:
                pref = 0
            return (pref, len(parts), self.rel(p))

        return sorted(matches, key=key)[0]

    def pick_all(self, name: str) -> List[Path]:
        return [p for p in self.files if p.name == name]

    def diag_files(self, name: str) -> List[Path]:
        found = [
            p for p in self.files
            if p.name == name and any(part.startswith("diag_") for part in p.parts)
        ]
        return found or self.pick_all(name)

    def round_label(self, path: Path) -> str:
        parts = path.relative_to(self.root).parts
        for part in parts:
            if part.startswith("diag_"):
                return part
        return "raiz" if len(parts) == 1 else parts[-2]

    def source_label(self, path: Path) -> str:
        rnd = self.round_label(path)
        return path.name if rnd == "raiz" else f"{rnd}/{path.name}"

    def boot_logs(self) -> List[Path]:
        files = [p for p in self.files if p.name.startswith("boot.log")]
        if not files:
            files = [p for p in self.files if p.name == "all.log"]
        seen = set()
        out = []
        for p in files:
            try:
                fp = file_fingerprint(p)
            except OSError:
                continue
            if fp in seen:
                continue
            seen.add(fp)
            out.append(p)

        def order(p: Path):
            suffix = p.name[len("boot.log"):]
            if not suffix:
                return (1, 0)
            m = re.match(r"\.(\d+)$", suffix)
            if m:
                return (0, -int(m.group(1)))
            return (0, 0)

        return sorted(out, key=lambda p: (order(p), self.rel(p)))

    def takeover_files(self) -> List[Path]:
        out = []
        for p in self.files:
            if p.name.startswith("takeover") and p.name.endswith(".log"):
                out.append(p)
                continue
            parts = p.relative_to(self.root).parts
            if (
                "oneos_logs" in parts
                and not p.name.startswith("boot.log")
                and p.name != "all.log"
            ):
                out.append(p)
        return out


@dataclass
class Reading:
    t: int
    ts: str
    policies: Dict[str, Tuple[int, int]]
    skin: Optional[float]


@dataclass
class HeatEvent:
    t: int
    ts: str
    kind: str
    old: int = 0
    new: int = 0
    pct: Optional[int] = None
    gpu: Optional[Tuple[int, int]] = None
    skin: Optional[float] = None
    text: str = ""


# ---------------------------------------------------------------------------
# logcat: modelo, sistema e falhas repetidas
# ---------------------------------------------------------------------------

def scan_logcat(session: Session) -> dict:
    info = {
        "files": [],
        "lines": 0,
        "model": "",
        "model_src": "",
        "brand": "",
        "device": "",
        "manufacturer": "",
        "fingerprint": "",
        "fingerprint_src": "",
        "release": "",
        "release_src": "",
        "sdk": "",
        "sdk_src": "",
        "display": "",
        "display_src": "",
        "failures": Counter(),
        "failure_first": {},
        "failure_tags": defaultdict(set),
        "failure_total": 0,
        "kick_lines": [],
    }
    files = session.boot_logs()
    info["files"] = [session.rel(p) for p in files]

    model_patterns = (
        re.compile(r"\bBuild\.MODEL\s*[=:]\s*[\"']?([^\"'\s,;]+)", re.I),
        re.compile(r"\[ro\.product\.model\]\s*:\s*\[([^\]]+)\]", re.I),
        re.compile(r"\bro\.product\.model\s*[=:]\s*\[?([^\]\s\"']+)", re.I),
    )
    brand_patterns = (
        re.compile(r"\[ro\.product\.brand\]\s*:\s*\[([^\]]+)\]", re.I),
        re.compile(r"\bro\.product\.brand\s*[=:]\s*\[?([^\]\s\"']+)", re.I),
    )
    device_patterns = (
        re.compile(r"\[ro\.product\.device\]\s*:\s*\[([^\]]+)\]", re.I),
        re.compile(r"\bro\.product\.device\s*[=:]\s*\[?([^\]\s\"']+)", re.I),
    )
    manufacturer_patterns = (
        re.compile(r"\bBuild\.MANUFACTURER\s*[=:]\s*[\"']?([^\"'\s,;]+)", re.I),
        re.compile(r"\[ro\.product\.manufacturer\]\s*:\s*\[([^\]]+)\]", re.I),
    )
    fp_patterns = (
        re.compile(r"\bBuild fingerprint\s*:\s*[\"']?([^\s\"']+)", re.I),
        re.compile(r"\bBuild\.FINGERPRINT\s*[=:]\s*[\"']?([^\s\"']+)", re.I),
        re.compile(r"\[ro\.build\.fingerprint\]\s*:\s*\[([^\]]+)\]", re.I),
        re.compile(r"\bro\.build\.fingerprint\s*[=:]\s*\[?([^\]\s\"']+)", re.I),
    )
    release_patterns = (
        re.compile(r"\bBuild\.VERSION\.RELEASE\s*[=:]\s*[\"']?([0-9][0-9A-Za-z.]*)", re.I),
        re.compile(r"\bro\.build\.version\.release\s*[=:]\s*\[?([0-9][0-9A-Za-z.]*)", re.I),
        re.compile(r"\bAndroid release\s*[=:]\s*[\"']?([0-9][0-9A-Za-z.]*)", re.I),
    )
    sdk_patterns = (
        re.compile(r"\bBuild\.VERSION\.SDK_INT\s*[=:]\s*(\d+)", re.I),
        re.compile(r"\bro\.build\.version\.sdk\s*[=:]\s*\[?(\d+)", re.I),
    )
    display_patterns = (
        re.compile(r"\bBuild\.DISPLAY\s*[=:]\s*[\"']?([^\s\"']+)", re.I),
        re.compile(r"\[ro\.build\.display\.id\]\s*:\s*\[([^\]]+)\]", re.I),
        re.compile(r"\bro\.build\.display\.id\s*[=:]\s*\[?([^\]\s\"']+)", re.I),
    )

    for path in files:
        rel = session.rel(path)
        try:
            handle = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                info["lines"] += 1
                low = line.lower()

                if not info["model"] and ("build.model" in low or "ro.product.model" in low):
                    for pat in model_patterns:
                        m = pat.search(line)
                        if m:
                            info["model"] = m.group(1).strip()
                            info["model_src"] = rel
                            break
                if not info["brand"] and "ro.product.brand" in low:
                    for pat in brand_patterns:
                        m = pat.search(line)
                        if m:
                            info["brand"] = m.group(1).strip()
                            break
                if not info["device"] and "ro.product.device" in low:
                    for pat in device_patterns:
                        m = pat.search(line)
                        if m:
                            info["device"] = m.group(1).strip()
                            break
                if not info["manufacturer"] and (
                    "build.manufacturer" in low or "ro.product.manufacturer" in low
                ):
                    for pat in manufacturer_patterns:
                        m = pat.search(line)
                        if m:
                            info["manufacturer"] = m.group(1).strip()
                            break
                if not info["fingerprint"] and (
                    "build fingerprint" in low or "build.fingerprint" in low
                    or "ro.build.fingerprint" in low
                ):
                    for pat in fp_patterns:
                        m = pat.search(line)
                        if m:
                            info["fingerprint"] = m.group(1).strip()
                            info["fingerprint_src"] = rel
                            break
                if not info["release"] and (
                    "build.version.release" in low or "android release" in low
                    or "ro.build.version.release" in low
                ):
                    for pat in release_patterns:
                        m = pat.search(line)
                        if m:
                            info["release"] = m.group(1).strip()
                            info["release_src"] = rel
                            break
                if not info["sdk"] and (
                    "build.version.sdk_int" in low or "ro.build.version.sdk" in low
                ):
                    for pat in sdk_patterns:
                        m = pat.search(line)
                        if m:
                            info["sdk"] = m.group(1).strip()
                            info["sdk_src"] = rel
                            break
                if not info["display"] and (
                    "build.display" in low or "ro.build.display.id" in low
                ):
                    for pat in display_patterns:
                        m = pat.search(line)
                        if m:
                            info["display"] = m.group(1).strip()
                            info["display_src"] = rel
                            break

                if FAIL_RE.search(line):
                    tag, message = split_logcat(line)
                    norm = normalize_message(message)
                    if norm:
                        info["failures"][norm] += 1
                        info["failure_total"] += 1
                        info["failure_tags"][norm].add(tag or "?")
                        ts_tuple, ts_text = parse_thread_ts(line)
                        if norm not in info["failure_first"]:
                            info["failure_first"][norm] = (ts_tuple, ts_text, rel, tag or "?")
                        else:
                            old = info["failure_first"][norm]
                            if ts_tuple is not None and (
                                old[0] is None or ts_tuple < old[0]
                            ):
                                info["failure_first"][norm] = (ts_tuple, ts_text, rel, tag or "?")

                if (
                    "sf render rate" in low
                    or "no physical displays" in low
                    or "left to the panel keeper" in low
                    or "displaymanagerpolicy" in low
                ):
                    info["kick_lines"].append((rel, line.rstrip()))

    if not info["release"] and info["fingerprint"]:
        m = re.search(r":([0-9][^/]*)/", info["fingerprint"])
        if m:
            info["release"] = m.group(1)
            info["release_src"] = info["fingerprint_src"] + " (fingerprint)"
    if not info["model"] and info["device"]:
        info["model"] = info["device"]
        info["model_src"] = "ro.product.device"
    return info


# ---------------------------------------------------------------------------
# 1. modelo, sistema e sessão
# ---------------------------------------------------------------------------

def read_env(path: Optional[Path]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if path is None:
        return values
    for line in read_text_lines(path):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def analyze_stage_marker(session: Session) -> List[str]:
    path = session.pick(".oneos_stage")
    if path is None:
        return ["Imagem OneOS: não encontrado (.oneos_stage ausente)"]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"Imagem OneOS: não foi possível ler .oneos_stage ({exc})"]
    marker = None
    try:
        marker = json.loads(text)
    except (ValueError, TypeError):
        marker = None
    if not isinstance(marker, dict):
        marker = {}
        for key in ("image", "profile", "set", "versionCode", "created"):
            m = re.search(r'"' + key + r'"\s*:\s*"?([^",\n]+)"?', text)
            if m:
                marker[key] = m.group(1).strip()
    image = marker.get("image") or "?"
    profile = marker.get("profile") or "?"
    fileset = marker.get("set")
    version = marker.get("versionCode")
    created = marker.get("created")
    extra = []
    if fileset:
        extra.append(f"set {fileset}")
    if version:
        extra.append(f"app {version}")
    if created:
        try:
            extra.append("criado " + datetime.fromtimestamp(int(created)).strftime("%Y-%m-%d"))
        except (TypeError, ValueError, OSError, OverflowError):
            extra.append(f"criado {created}")
    suffix = f" ({', '.join(extra)})" if extra else ""
    return [f"Imagem OneOS: {image} (perfil {profile}){suffix} (.oneos_stage)"]


def section_model(session: Session, logs: dict) -> List[str]:
    lines = ["== 1. Modelo, sistema e sessão =="]
    if logs["model"]:
        lines.append(f"Modelo do guest: {logs['model']} ({logs['model_src'] or 'boot.log'})")
    else:
        lines.append("Modelo do guest: não encontrado (boot.log sem Build.MODEL/ro.product.model)")

    if logs["release"] or logs["sdk"]:
        bits = []
        if logs["release"]:
            bits.append(f"Android {logs['release']}")
        else:
            bits.append("release não encontrado")
        if logs["sdk"]:
            bits.append(f"SDK {logs['sdk']}")
        src = logs["release_src"] or logs["sdk_src"] or "boot.log"
        lines.append(f"Sistema do guest: {', '.join(bits)} ({src})")
    else:
        lines.append("Sistema do guest: não encontrado (boot.log sem Build.VERSION.RELEASE/SDK_INT)")

    if logs["fingerprint"]:
        lines.append(f"Build fingerprint: {shorten(logs['fingerprint'], 160)} ({logs['fingerprint_src']})")
    else:
        lines.append("Build fingerprint: não encontrado")
    if logs["display"]:
        lines.append(f"Build/display: {shorten(logs['display'], 120)} ({logs['display_src']})")
    if logs["brand"] or logs["device"] or logs["manufacturer"]:
        bits = []
        if logs["brand"]:
            bits.append(f"brand={logs['brand']}")
        if logs["device"]:
            bits.append(f"device={logs['device']}")
        if logs["manufacturer"]:
            bits.append(f"manufacturer={logs['manufacturer']}")
        lines.append("Propriedades do guest: " + ", ".join(bits))
    lines.extend(analyze_stage_marker(session))

    env_path = session.pick("session.env")
    if env_path is None:
        lines.append("session.env: não encontrado (procurado na raiz e em oneos_logs/)")
        return lines
    env = read_env(env_path)
    lines.append(f"session.env ({session.rel(env_path)}):")
    for key in ENV_KEYS:
        if key in env:
            value = env[key]
            note = ""
            if key == "ONEOS_HEAT_CAP":
                note = "  [cap térmico ligado]" if value == "1" else "  [cap térmico desligado]"
            elif key == "ONEOS_GUEST_THERMAL_MAP":
                note = "  [status do guest pelo skin]" if value == "1" else "  [status do HAL do guest]"
            lines.append(f"  {key}={value}{note}")
        else:
            lines.append(f"  {key}=não encontrado")
    return lines


# ---------------------------------------------------------------------------
# 2. ceilings de CPU/GPU
# ---------------------------------------------------------------------------

def parse_diag_series(path: Optional[Path]) -> List[Reading]:
    if path is None:
        return []
    readings: List[Reading] = []
    day = 0
    last_abs: Optional[int] = None
    for line in read_text_lines(path):
        # Comentários do sampler ("# ... sampler stopped") podem conter um
        # HH:MM:SS e não são leituras.
        if line.lstrip().startswith("#"):
            continue
        ts = parse_hms(line)
        if ts is None:
            continue
        if last_abs is not None and ts + day * 86400 < last_abs - 43200:
            day += 1
        abs_t = ts + day * 86400
        last_abs = abs_t
        policies: Dict[str, Tuple[int, int]] = {}
        for m in POLICY_RE.finditer(line):
            name, scaling, hw = m.group(1), int(m.group(2)), int(m.group(3))
            if scaling > 0 and hw > 0:
                policies[name] = (scaling, hw)
        skin = None
        sm = SKIN_RE.search(line)
        if sm:
            try:
                skin = float(sm.group(1).replace(",", "."))
            except ValueError:
                skin = None
        readings.append(Reading(abs_t, hms(ts), policies, skin))
    return readings


def policy_timeline(readings: Sequence[Reading], name: str) -> "OrderedDict[Tuple[int, int], List[Reading]]":
    states: "OrderedDict[Tuple[int, int], List[Reading]]" = OrderedDict()
    for reading in readings:
        pair = reading.policies.get(name)
        if pair is None:
            continue
        states.setdefault(pair, []).append(reading)
    return states


def capped_seconds(readings: Sequence[Reading], name: Optional[str] = None) -> float:
    if len(readings) < 2:
        return 0.0
    intervals = [
        max(0, readings[i + 1].t - readings[i].t)
        for i in range(len(readings) - 1)
    ]
    intervals.sort()
    fallback = intervals[len(intervals) // 2] if intervals else 30
    total = 0.0
    for i, reading in enumerate(readings):
        capped = False
        if name is None:
            capped = any(mx < hw for mx, hw in reading.policies.values())
        else:
            pair = reading.policies.get(name)
            capped = bool(pair and pair[0] < pair[1])
        if not capped:
            continue
        dt = readings[i + 1].t - reading.t if i + 1 < len(readings) else fallback
        total += max(0, dt)
    return total


def values_at(reading: Optional[Reading]) -> str:
    if reading is None:
        return "?"
    parts = []
    for name in sorted(reading.policies, key=natural_policy_key):
        mx, hw = reading.policies[name]
        parts.append(f"{name}={mx}/{hw} ({pct_str(mx, hw)})")
    return ", ".join(parts) if parts else "sem policy"


def section_ceilings(session: Session, logs: dict) -> Tuple[List[str], dict]:
    lines = ["== 2. Ceilings de CPU/GPU =="]
    summary = {"diag_end": None, "diag_start": None, "max_skin_diag": None,
               "max_skin_diag_ts": "", "max_skin_heat": None, "max_skin_heat_ts": ""}

    series_path = session.pick("diag_series.txt")
    readings = parse_diag_series(series_path)
    if series_path is None:
        lines.append("diag_series.txt: não encontrado")
    elif not readings:
        lines.append(f"diag_series.txt ({session.rel(series_path)}): sem leituras HH:MM:SS válidas")
    else:
        duration = readings[-1].t - readings[0].t
        if len(readings) > 1:
            intervals = [
                readings[i + 1].t - readings[i].t
                for i in range(len(readings) - 1)
            ]
            duration += sorted(intervals)[len(intervals) // 2]
        summary["diag_start"] = readings[0].t
        summary["diag_end"] = readings[-1].t
        lines.append(
            f"diag_series.txt ({len(readings)} leituras, "
            f"{readings[0].ts}–{readings[-1].ts}, ~{fmt_duration(duration)}):"
        )
        policy_names = sorted(
            {name for r in readings for name in r.policies},
            key=natural_policy_key,
        )
        if not policy_names:
            lines.append("  nenhuma linha policyN=scaling_max_freq/cpuinfo_max_freq")
        for idx, name in enumerate(policy_names[:8]):
            states = policy_timeline(readings, name)
            top = max(hw for _, hw in states)
            pieces = []
            for (mx, hw), samples in list(states.items())[:4]:
                pieces.append(
                    f"{pct_str(mx, hw)} ({mx}) {len(samples)}x "
                    f"{samples[0].ts}–{samples[-1].ts}"
                )
            more = len(states) - 4
            suffix = f"; +{more} outro(s) valor(es)" if more > 0 else ""
            lines.append(
                f"  {name} (topo {top} kHz): " + "; ".join(pieces) + suffix
            )
        if len(policy_names) > 8:
            lines.append(f"  ... +{len(policy_names) - 8} policy(s) omitida(s)")
        lines.append(f"  primeira leitura {readings[0].ts}: {values_at(readings[0])}")
        lines.append(f"  última leitura {readings[-1].ts}: {values_at(readings[-1])}")
        capped_bits = []
        for name in policy_names[:8]:
            seconds = capped_seconds(readings, name)
            if seconds > 0:
                pct_session = 100.0 * seconds / duration if duration > 0 else 0
                capped_bits.append(f"{name} ~{fmt_duration(seconds)} ({pct_session:.0f}%)")
        any_seconds = capped_seconds(readings, None)
        if capped_bits:
            lines.append("  capado: " + "; ".join(capped_bits))
            if duration > 0:
                lines.append(
                    f"  qualquer policy capada: ~{fmt_duration(any_seconds)} "
                    f"({100.0 * any_seconds / duration:.0f}% da sessão)"
                )
        else:
            lines.append("  capado: não (todas as leituras em 100% do topo)")
        if any(r.skin is not None for r in readings):
            best = max(
                (r for r in readings if r.skin is not None),
                key=lambda r: (r.skin, -r.t),
            )
            summary["max_skin_diag"] = best.skin
            summary["max_skin_diag_ts"] = best.ts

    heat_path = session.pick("heat_cap.log")
    heat = parse_heat_cap(heat_path)
    if heat_path is None:
        lines.append("heat_cap.log: não encontrado")
    elif not heat["events"]:
        lines.append(f"heat_cap.log ({session.rel(heat_path)}): sem eventos reconhecidos")
    else:
        lines.append(
            f"heat_cap.log ({len(heat['events'])} evento(s), "
            f"{heat['events'][0].ts}–{heat['events'][-1].ts}):"
        )
        cap_events = [
            e for e in heat["events"]
            if e.kind in ("level", "full", "nocap", "stopped")
        ]
        shown = cap_events[:6]
        if len(cap_events) > 8:
            shown = cap_events[:4] + cap_events[-2:]
        for e in shown:
            lines.append("  " + format_heat_event(e))
        if len(cap_events) > len(shown):
            lines.append(f"  ... +{len(cap_events) - len(shown)} mudança(s) omitida(s)")
        if heat["cap_on"] is not None:
            lines.append(f"  cap térmico no log: {'ligado' if heat['cap_on'] else 'desligado'}")
        if heat["gpu_states"]:
            gpu_bits = []
            for (cur, top), ts_list in list(heat["gpu_states"].items())[:6]:
                gpu_bits.append(
                    f"{cur}/{top} ({pct_str(cur, top)}) {len(ts_list)}x "
                    f"{ts_list[0]}–{ts_list[-1]}"
                )
            lines.append("  gpu: " + "; ".join(gpu_bits))
        if heat["status"]:
            last = heat["status"][-1]
            lines.append(
                f"  status térmico do guest: {len(heat['status'])} evento(s); "
                f"último {last.ts}: {shorten(last.text, 110)}"
            )
        if cap_events:
            total_cap = 0.0
            level = 0
            prev = None
            for e in cap_events:
                if prev is not None and level > 0:
                    total_cap += max(0, e.t - prev)
                level = e.new if e.kind != "stopped" else 0
                prev = e.t
            lines.append(f"  tempo com nível >0 entre eventos: ~{fmt_duration(total_cap)}")
        if heat["max_skin"] is not None:
            summary["max_skin_heat"] = heat["max_skin"]
            summary["max_skin_heat_ts"] = heat["max_skin_ts"]

    perf_path = session.pick("performance_handover.log")
    perf = parse_performance(perf_path)
    if perf_path is None:
        lines.append("performance_handover.log: não encontrado")
    else:
        if perf["heat_on"]:
            lines.append("performance_handover.log: worker não iniciado (heat cap ligado)")
        elif not perf["requested"] and not perf["thermal"]:
            lines.append(f"performance_handover.log ({session.rel(perf_path)}): sem pedidos registrados")
        if perf["requested"]:
            bits = []
            for tag, counter in list(perf["requested"].items())[:8]:
                vals = ", ".join(
                    f"{value} ({count}x)" for value, count in counter.most_common(4)
                )
                eff_counter = perf["effective"].get(tag)
                if eff_counter:
                    effective = ", ".join(
                        f"{value} ({count}x)"
                        for value, count in eff_counter.most_common(4)
                    )
                    bits.append(f"{tag}={vals} -> efetivo {effective}")
                else:
                    bits.append(f"{tag}={vals}")
            lines.append("  pedidos: " + "; ".join(bits))
        if perf["thermal"]:
            last = perf["thermal"][-1]
            lines.append(
                f"  níveis do worker: {len(perf['thermal'])} mudança(s); "
                f"última {last.ts}: {shorten(last.text, 110)}"
            )

    throttle_files = session.diag_files("throttle.txt")
    if not throttle_files:
        lines.append("throttle.txt: não encontrado (diag_*/throttle.txt)")
    else:
        throttle = parse_throttle(session, throttle_files)
        if not throttle["policies"] and not throttle["gpu"]:
            names = ", ".join(session.source_label(p) for p in throttle_files[:6])
            lines.append(f"throttle.txt ({names}): sem policyN/gpu reconhecidos")
        else:
            names = ", ".join(session.round_label(p) for p in throttle_files[:6])
            lines.append(f"throttle.txt ({names}):")
            for (name, mx, hw), rounds in list(throttle["policies"].items())[:8]:
                lines.append(
                    f"  {name}= {mx}/{hw} ({pct_str(mx, hw)}) em {', '.join(rounds[:6])}"
                )
            for (cur, top), rounds in list(throttle["gpu"].items())[:4]:
                lines.append(
                    f"  gpu max={mhz(top):g} MHz em {', '.join(rounds[:6])}"
                )
            if throttle["thermal_core"]:
                lines.append("  thermal_core: " + shorten(throttle["thermal_core"][-1], 120))

    return lines, summary


def parse_heat_cap(path: Optional[Path]) -> dict:
    result = {
        "events": [],
        "status": [],
        "gpu_states": OrderedDict(),
        "cap_on": None,
        "max_skin": None,
        "max_skin_ts": "",
    }
    if path is None:
        return result
    day = 0
    last_abs: Optional[int] = None
    for line in read_text_lines(path):
        low = line.lower()
        if result["cap_on"] is None:
            if "heat cap: from" in low or "oneos heat cap: from" in low:
                result["cap_on"] = True
            elif "heat cap off" in low:
                result["cap_on"] = False
        ts = parse_hms(line)
        if ts is None:
            continue
        if last_abs is not None and ts + day * 86400 < last_abs - 43200:
            day += 1
        abs_t = ts + day * 86400
        last_abs = abs_t
        text = re.sub(r"^#?\s*\d{1,2}:\d{2}:\d{2}\s*", "", line).strip()
        skin = None
        sm = re.search(r"skin\s+(-?\d+(?:[.,]\d+)?)\s*C", line, re.I)
        if sm:
            try:
                skin = float(sm.group(1).replace(",", "."))
            except ValueError:
                skin = None
        gpu = None
        gm = re.search(r"gpu=(\d+)/(\d+)", line)
        if gm:
            gpu = (int(gm.group(1)), int(gm.group(2)))
            result["gpu_states"].setdefault(gpu, []).append(hms(ts))
        if skin is not None and (result["max_skin"] is None or skin > result["max_skin"]):
            result["max_skin"] = skin
            result["max_skin_ts"] = hms(ts)

        lm = re.search(r"\blevel\s+(\d+)\s+to\s+(\d+)", line)
        if lm:
            pm = re.search(r":\s*(\d+)%:", line)
            result["events"].append(HeatEvent(
                abs_t, hms(ts), "level", int(lm.group(1)), int(lm.group(2)),
                int(pm.group(1)) if pm else None, gpu, skin, text,
            ))
            continue
        if "guest thermal status" in low:
            event = HeatEvent(abs_t, hms(ts), "status", text=text)
            result["events"].append(event)
            result["status"].append(event)
            continue
        if "full clock" in low or "a cap from before" in low:
            result["events"].append(HeatEvent(abs_t, hms(ts), "full", new=0, gpu=gpu, skin=skin, text=text))
            continue
        if "no cap" in low:
            result["events"].append(HeatEvent(abs_t, hms(ts), "nocap", new=0, gpu=gpu, skin=skin, text=text))
            continue
        if "stopped" in low:
            result["events"].append(HeatEvent(abs_t, hms(ts), "stopped", new=0, gpu=gpu, skin=skin, text=text))
            continue
    return result


def format_heat_event(e: HeatEvent) -> str:
    if e.kind == "level":
        pct = f" ({e.pct}%)" if e.pct is not None else ""
        gpu = f", gpu={e.gpu[0]}/{e.gpu[1]}" if e.gpu else ""
        skin = f", skin {e.skin:g} °C" if e.skin is not None else ""
        full = " full clock" if e.new == 0 and "full clock" in e.text.lower() else ""
        return f"{e.ts} nível {e.old}→{e.new}{full}{pct}{skin}{gpu}"
    if e.kind == "full":
        gpu = f", gpu={e.gpu[0]}/{e.gpu[1]}" if e.gpu else ""
        skin = f", skin {e.skin:g} °C" if e.skin is not None else ""
        return f"{e.ts} full clock (nível→0){skin}{gpu}"
    if e.kind == "nocap":
        return f"{e.ts} sem cap (nível 0)"
    if e.kind == "stopped":
        return f"{e.ts} parou"
    return f"{e.ts} {shorten(e.text, 110)}"


def parse_performance(path: Optional[Path]) -> dict:
    result = {
        "requested": OrderedDict(),
        "effective": OrderedDict(),
        "thermal": [],
        "heat_on": False,
    }
    if path is None:
        return result
    for line in read_text_lines(path):
        low = line.lower()
        if "heat cap on" in low:
            result["heat_on"] = True
        ts = parse_hms(line)
        ts_text = hms(ts) if ts is not None else "?"
        m = re.search(
            r"requested\s+(\S+)=([^;]+)"
            r"(?:;\s*effective\s+(?:(\S+)=)?([^;\s]+))?",
            line,
        )
        if m:
            tag = m.group(1)
            value = m.group(2).strip()
            result["requested"].setdefault(tag, Counter())[value] += 1
            if m.group(4):
                effective = m.group(4).strip()
                result["effective"].setdefault(tag, Counter())[effective] += 1
        tm = re.search(r"thermal level\s+(\d+)->(\d+)", line)
        if tm:
            result["thermal"].append(HeatEvent(
                ts or 0, ts_text, "level", int(tm.group(1)), int(tm.group(2)),
                text=line.strip(),
            ))
    return result


def parse_throttle(session: Session, paths: Sequence[Path]) -> dict:
    policies: "OrderedDict[Tuple[str, int, int], List[str]]" = OrderedDict()
    gpu: "OrderedDict[Tuple[int, int], List[str]]" = OrderedDict()
    thermal_core: List[str] = []
    for path in paths:
        rnd = session.round_label(path)
        for line in read_text_lines(path):
            m = re.search(r"(policy\d+)\s+cur/max/hw\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)", line)
            if m:
                name, _cur, mx, hw = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
                if mx > 0 and hw > 0:
                    policies.setdefault((name, mx, hw), []).append(rnd)
                continue
            gm = re.search(r"gpu\s+cur\s+(-?\d+)\s+max\s+(-?\d+)", line)
            if gm:
                cur, top = int(gm.group(1)), int(gm.group(2))
                if top > 0:
                    gpu.setdefault((cur, top), []).append(rnd)
                continue
            if "thermal_core" in line.lower() or re.search(r"\bthermal\b", line, re.I):
                thermal_core.append(line.strip())
    # política: agrupa por nome/valor e preserva a ordem de primeira aparição
    grouped: "OrderedDict[Tuple[str, int, int], List[str]]" = OrderedDict()
    for key, rounds in policies.items():
        uniq = []
        for r in rounds:
            if r not in uniq:
                uniq.append(r)
        grouped[key] = uniq
    return {"policies": grouped, "gpu": gpu, "thermal_core": thermal_core}


# ---------------------------------------------------------------------------
# 3. top processos por CPU
# ---------------------------------------------------------------------------

def _parse_float_match(pattern: re.Pattern[str], text: str) -> Optional[float]:
    m = pattern.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _stat_peak_str(
    series: Sequence[Tuple[float, str, int]],
    unit: str = "",
) -> str:
    values = [v for v, _, _ in series]
    med = fmt_num(median_value(values))
    peak_val, peak_ts, _ = max(series, key=lambda item: (item[0], -item[2]))
    return f"mediana {med}{unit}, máx {fmt_num(peak_val)}{unit} em {peak_ts}"


def section_cpu(session: Session) -> List[str]:
    lines = ["== 3. Top processos por CPU =="]
    path = session.pick("cpu_top.log")
    if path is None:
        lines.append("cpu_top.log: não encontrado")
        return lines

    samples = []
    user_samples: List[Tuple[float, str, int]] = []
    sys_samples: List[Tuple[float, str, int]] = []
    idle_samples: List[Tuple[float, str, int]] = []
    forks_samples: List[Tuple[float, str, int]] = []
    ctxt_samples: List[Tuple[float, str, int]] = []
    has_rate_tail = False
    day = 0
    last_abs: Optional[int] = None
    excluded = 0
    for line in read_text_lines(path):
        if line.lstrip().startswith("#") or "|" not in line:
            continue
        parts = line.split("|", 2)
        left = parts[0]
        # Desde d58d388 (sessões bceaef29fb128e94 no S25 Ultra e 4d2e95c67f065941
        # no S23, onde o sys ficava em 150–300% com o aparelho quase ocioso e os
        # 6 processos do top somavam pouco), cada linha termina com
        # "| forks/s N ctxt/s N": fica fora da lista de processos e é resumido
        # abaixo junto com os totais de CPU do cabeçalho do top.
        right = parts[1]
        tail = parts[2] if len(parts) > 2 else ""
        ts = parse_hms(left)
        if ts is None:
            continue
        if last_abs is not None and ts + day * 86400 < last_abs - 43200:
            day += 1
        abs_t = ts + day * 86400
        last_abs = abs_t
        ts_str = hms(ts)
        samples.append((abs_t, ts_str, right))

        u_val = _parse_float_match(CPU_USER_RE, left)
        if u_val is not None:
            user_samples.append((u_val, ts_str, abs_t))
        s_val = _parse_float_match(CPU_SYS_RE, left)
        if s_val is not None:
            sys_samples.append((s_val, ts_str, abs_t))
        i_val = _parse_float_match(CPU_IDLE_RE, left)
        if i_val is not None:
            idle_samples.append((i_val, ts_str, abs_t))

        if tail and ("forks/s" in tail.lower() or "ctxt/s" in tail.lower()):
            has_rate_tail = True
            f_val = _parse_float_match(FORKS_RATE_RE, tail)
            if f_val is not None:
                forks_samples.append((f_val, ts_str, abs_t))
            c_val = _parse_float_match(CTXT_RATE_RE, tail)
            if c_val is not None:
                ctxt_samples.append((c_val, ts_str, abs_t))

    if not samples:
        lines.append(f"cpu_top.log ({session.rel(path)}): sem linhas de amostra reconhecidas")
        return lines
    start = samples[0][0]
    stats = {"first": defaultdict(lambda: [0.0, 0]), "rest": defaultdict(lambda: [0.0, 0])}
    first_samples = rest_samples = 0
    for abs_t, _ts, right in samples:
        window = "first" if abs_t - start < 300 else "rest"
        if window == "first":
            first_samples += 1
        else:
            rest_samples += 1
        for m in re.finditer(r"([0-9]+(?:\.[0-9]+)?)\s+([^\s|]+)", right):
            try:
                cpu = float(m.group(1))
            except ValueError:
                continue
            name = m.group(2)
            if EXCLUDED_PROC_RE.match(name):
                excluded += 1
                continue
            bucket = stats[window][name]
            bucket[0] += cpu
            bucket[1] += 1

    lines.append(
        f"cpu_top.log ({len(samples)} amostras; {first_samples} nos primeiros 5 min, "
        f"{rest_samples} no resto; %CPU de um núcleo):"
    )
    if user_samples or sys_samples or idle_samples:
        cpu_bits = []
        if user_samples:
            cpu_bits.append("user " + _stat_peak_str(user_samples, "%"))
        if sys_samples:
            high_sys = sum(1 for v, _, _ in sys_samples if v >= 150.0)
            extra_sys = f" ({high_sys}x >=150%)" if high_sys else ""
            cpu_bits.append("sys " + _stat_peak_str(sys_samples, "%") + extra_sys)
        if idle_samples:
            idle_med = fmt_num(median_value([v for v, _, _ in idle_samples]))
            cpu_bits.append(f"idle mediana {idle_med}%")
        lines.append("  totais de CPU: " + "; ".join(cpu_bits))

    if forks_samples or ctxt_samples:
        rate_count = max(len(forks_samples), len(ctxt_samples))
        rate_bits = []
        if forks_samples:
            rate_bits.append("forks/s " + _stat_peak_str(forks_samples))
        if ctxt_samples:
            rate_bits.append("ctxt/s " + _stat_peak_str(ctxt_samples))
        lines.append(
            f"  forks/ctxt (/proc/stat, {rate_count} amostra(s)): "
            + "; ".join(rate_bits)
        )
    elif has_rate_tail:
        lines.append("  forks/ctxt (/proc/stat): sem leitura numérica (apenas '-')")

    def top_lines(window: str, title: str) -> None:
        entries = sorted(
            stats[window].items(),
            key=lambda item: (-(item[1][0] / item[1][1]), item[0]),
        )[:5]
        lines.append(f"  {title}:")
        if not entries:
            lines.append("    (sem processos, fora os excluídos)")
            return
        for idx, (name, (total, count)) in enumerate(entries, 1):
            lines.append(f"    {idx}. {name} — {total / count:.1f}% ({count} amostra(s))")

    top_lines("first", "Primeiros 5 min (top 5 por %CPU médio)")
    top_lines("rest", "Resto da sessão (top 5 por %CPU médio)")
    lines.append(
        f"  excluídos por regra: {excluded} leitura(s) de compiladores/instalador"
    )
    return lines


# ---------------------------------------------------------------------------
# 4. kick do SF e frames
# ---------------------------------------------------------------------------

KICK_FAILURE_RE = re.compile(r"no\s+physical\s+displays\s+listed\s+yet", re.I)
KICK_EXHAUSTION_RE = re.compile(
    r"no\s+physical\s+displays\s+after\s+60\s*s|"
    r"left\s+to\s+the\s+panel\s+keeper|esgot|exhaust",
    re.I,
)
KICK_NEGATIVE_RE = re.compile(
    r"no\s+modes|left\s+alone|no\s+physical\s+displays|sem\s+sucesso|"
    r"not\s+succeeded|unsuccessful|\berror\b|\berro\b|\bfailed\b|\bfalhou\b",
    re.I,
)
KICK_SUCCESS_RE = re.compile(
    r"pinned\s+at|through\s+mode|Result:\s*Parcel",
    re.I,
)


def kick_line_is_success(line: str) -> bool:
    """Só aceita evidência positiva; rejeita 'no modes', 'left alone' etc."""
    text = line.strip()
    if not text:
        return False
    if KICK_NEGATIVE_RE.search(text):
        return False
    return bool(KICK_SUCCESS_RE.search(text))


def exhaustion_retries(
    boot_lines: Sequence[Tuple[str, str]],
    failure_lines: Sequence[str],
    exhaustion_lines: Sequence[str],
) -> int:
    """Número real de retries na exaustão, com fallback para logs antigos."""
    for line in exhaustion_lines:
        m = re.search(
            r"after\s+60\s*s\s*\(\s*(\d+)\s*/\s*30\s*retries?\s*\)",
            line,
            re.I,
        )
        if m:
            return int(m.group(1))
    retry_values = []
    for _, line in boot_lines:
        m = re.search(r"retry\s+(\d+)\s*/\s*30", line, re.I)
        if m:
            retry_values.append(int(m.group(1)))
    if retry_values:
        return max(retry_values)
    if any(re.search(r"after\s+60\s*s", line, re.I) for line in exhaustion_lines):
        return 30
    return max(len(failure_lines), 1)


def analyze_kick(session: Session, extra_lines: Sequence[Tuple[str, str]]) -> List[str]:
    lines = ["== 4. Kick do SF e frames =="]
    candidates: List[Tuple[str, str]] = []
    for path in session.takeover_files():
        try:
            for line in read_text_lines(path):
                low = line.lower()
                if (
                    "sf render rate" in low
                    or "no physical displays" in low
                    or "left to the panel keeper" in low
                    or "displaymanagerpolicy" in low
                ):
                    candidates.append((session.rel(path), line.rstrip()))
        except OSError:
            continue
    candidates.extend(extra_lines)

    # remove duplicatas exatas, preservando a ordem
    seen = set()
    deduped = []
    for rel, line in candidates:
        key = (rel, line)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((rel, line))
    candidates = deduped

    boot_lines = [
        (rel, line) for rel, line in candidates
        if "panel at " not in line.lower()
    ]
    if not boot_lines:
        lines.append("Kick do SF: não encontrado (sem '[Pivoted Root] SF render rate' em takeover*.log/oneos_logs)")
        kick_source = "?"
        skip = False
        failure_lines: List[str] = []
        exhaustion_lines: List[str] = []
        success_idx = None
        success_attempt = None
    else:
        kick_source = sorted({rel for rel, _ in boot_lines})[0]
        joined = "\n".join(line for _, line in boot_lines)
        low = joined.lower()
        skip = "left to displaymanager" in low or "oneos_guest_sf_pin=0" in low
        failure_lines = [
            line for _, line in boot_lines if KICK_FAILURE_RE.search(line)
        ]
        exhaustion_lines = [
            line for _, line in boot_lines
            if not KICK_FAILURE_RE.search(line) and KICK_EXHAUSTION_RE.search(line)
        ]
        success_idx = None
        for idx, (_, line) in enumerate(boot_lines):
            if kick_line_is_success(line):
                success_idx = idx
                break

        explicit = None
        if success_idx is not None:
            success_line = boot_lines[success_idx][1]
            # O guest imprime "display listed on retry N/30" na linha
            # seguinte (ou vizinha); o número explícito vence o fallback.
            window = [success_idx, success_idx + 1, success_idx + 2,
                      success_idx - 1, success_idx - 2]
            for j in window:
                if not 0 <= j < len(boot_lines):
                    continue
                m = re.search(
                    r"(?:retry|tentativa)\s+(\d+)\s*/\s*30",
                    boot_lines[j][1],
                    re.I,
                )
                if m:
                    explicit = int(m.group(1))
                    break
            if explicit is None:
                m = re.search(
                    r"(?:attempt|tentativa|retry|try)\s*#?\s*(\d+)",
                    success_line,
                    re.I,
                )
                if m:
                    explicit = int(m.group(1))
            if explicit is None:
                m = re.search(r"#\s*(\d+)\b", success_line)
                explicit = int(m.group(1)) if m else None

        failures_before = 0
        if success_idx is not None:
            failures_before = sum(
                1 for _, line in boot_lines[:success_idx]
                if KICK_FAILURE_RE.search(line)
            )
        success_attempt = explicit if explicit is not None else (
            failures_before + 1 if success_idx is not None else None
        )

        lines.append(f"Kick do SF (fonte: {kick_source}):")
        if skip:
            lines.append("  resultado: pulado — ONEOS_GUEST_SF_PIN=0, render rate deixado ao DisplayManager")
        elif success_idx is not None:
            if success_attempt == 1:
                lines.append(
                    "  resultado: sucesso na 1ª tentativa"
                    + (f" (0 falhas \"no physical displays\")" if failures_before == 0 else "")
                )
            else:
                lines.append(
                    f"  resultado: sucesso na tentativa {success_attempt} "
                    f"({failures_before} falha(s) \"no physical displays listed yet\")"
                )
        elif exhaustion_lines:
            retries = exhaustion_retries(boot_lines, failure_lines, exhaustion_lines)
            kicks = retries + len(exhaustion_lines)
            if retries:
                result = f"  resultado: esgotou após {retries} tentativas/60 s"
                result += f" ({kicks} kicks)"
                result += "; ficou para o panel keeper"
            else:
                result = "  resultado: esgotou após 60 s; ficou para o panel keeper"
            lines.append(result)
        elif failure_lines:
            lines.append(
                f"  resultado: sem display físico em {len(failure_lines)} tentativa(s); "
                "sem resultado final registrado"
            )
        else:
            lines.append("  resultado: linha de kick presente, mas sem sucesso/falha reconhecida")
        if failure_lines:
            suffix = " antes do sucesso" if success_idx is not None else ""
            lines.append(
                f"  \"no physical displays listed yet\": {len(failure_lines)} "
                f"ocorrência(s){suffix}"
            )
        if exhaustion_lines:
            lines.append(
                "  exaustão: \"no physical displays after 60 s; "
                "left to the panel keeper\""
            )

        policy_lines = [line for _, line in boot_lines if "displaymanagerpolicy" in line.lower()]
        if policy_lines:
            lines.append("  displayManagerPolicy: " + shorten(policy_lines[-1], 300))
        else:
            lines.append("  displayManagerPolicy: não encontrado")

    panel_path = session.pick("panel_keeper.log")
    if panel_path is None:
        lines.append("  panel keeper: não encontrado (panel_keeper.log ausente)")
    else:
        kicks = []
        for line in read_text_lines(panel_path):
            if "panel at " in line.lower():
                kicks.append(line.strip())
        if not kicks:
            lines.append("  panel keeper: 0 tentativa(s) registrada(s)")
        else:
            confirmed = 0
            last = kicks[-1]
            for line in kicks:
                m = re.search(r"now\s+(\d+)\s+Hz", line, re.I)
                if m and int(m.group(1)) >= 48:
                    confirmed += 1
            lines.append(
                f"  panel keeper: {len(kicks)} tentativa(s), {confirmed} com painel "
                f">=48 Hz; última: {shorten(last, 180)}"
            )
    return lines


def parse_durations(session: Session) -> List[Tuple[str, str, float, float, int]]:
    """(fonte, rodada, app_ms_médio, sf_ms_médio, pares)."""
    out = []
    for path in session.diag_files("surfaceflinger.txt") + session.diag_files("frames.txt"):
        pairs = []
        for line in read_text_lines(path):
            ll = line.lower()
            if "app duration" not in ll and "sfduration" not in ll and "app/sf" not in ll:
                continue
            app = re.search(
                r"(?<!early )\bapp duration:\s*(\d+(?:[.,]\d+)?)\s*(ns|us|µs|ms|s)\b",
                line, re.I,
            )
            sf = re.search(
                r"(?<!early )\bSF duration:\s*(\d+(?:[.,]\d+)?)\s*(ns|us|µs|ms|s)\b",
                line, re.I,
            )
            if app and sf:
                pairs.append((
                    to_ms(float(app.group(1).replace(",", ".")), app.group(2)),
                    to_ms(float(sf.group(1).replace(",", ".")), sf.group(2)),
                ))
                continue
            m = re.search(
                r"durations?\s+app\s*/\s*SF\s*[:=]?\s*([0-9.,]+)\s*/\s*([0-9.,]+)\s*ms",
                line, re.I,
            )
            if m:
                pairs.append((float(m.group(1).replace(",", ".")),
                              float(m.group(2).replace(",", "."))))
                continue
            m = re.search(
                r"\bapp\s*[:=]\s*([0-9.,]+)\s*ms\b.*\bSF\s*[:=]\s*([0-9.,]+)\s*ms",
                line, re.I,
            )
            if m:
                pairs.append((float(m.group(1).replace(",", ".")),
                              float(m.group(2).replace(",", "."))))
        if pairs:
            out.append((
                session.rel(path),
                session.round_label(path),
                sum(p[0] for p in pairs) / len(pairs),
                sum(p[1] for p in pairs) / len(pairs),
                len(pairs),
            ))
    return out


def section_frames(session: Session) -> List[str]:
    lines: List[str] = []
    durations = parse_durations(session)
    if durations:
        for rel, rnd, app, sf, count in durations[:6]:
            where = rel
            lines.append(
                f"  Durações app/SF ({where}): app {app:.1f} ms / SF {sf:.1f} ms "
                f"(média de {count} par(es))"
            )
    else:
        lines.append("  Durações app/SF: não encontrado (surfaceflinger.txt/frames.txt sem app/SF duration)")

    missed: Dict[str, Dict[int, set]] = defaultdict(lambda: defaultdict(set))
    frames_files = session.diag_files("frames.txt")
    surface_files = session.diag_files("surfaceflinger.txt")
    composition_files = session.diag_files("composition.txt")
    for path in surface_files + composition_files:
        source = session.source_label(path)
        for line in read_text_lines(path):
            for m in re.finditer(
                r"\b(Total|HWC|GPU)?\s*missed frame count\s*:\s*(\d+)", line, re.I
            ):
                label = (m.group(1) or "geral").upper() if m.group(1) else "geral"
                missed[label][int(m.group(2))].add(source)
            for m in re.finditer(r"\b(?:missed|dropped) frames?\s*[:=]\s*(\d+)", line, re.I):
                missed["geral"][int(m.group(1))].add(source)
    if missed:
        parts = []
        for label in ("TOTAL", "HWC", "GPU", "GERAL"):
            if label not in missed:
                continue
            entries = []
            for value, sources in sorted(missed[label].items()):
                entries.append(f"{value} ({', '.join(sorted(sources)[:3])})")
            parts.append(f"{label}: " + "; ".join(entries))
        lines.append("  Frames perdidos — " + " | ".join(parts))
    else:
        lines.append("  Frames perdidos: não encontrado (surfaceflinger.txt/composition.txt sem missed frame count)")

    percentiles, frames_summary = parse_percentiles(frames_files, surface_files, session)
    if percentiles:
        lines.append("  Percentis (gfxinfo/frames.txt):")
        for pkg, values, source in percentiles[:6]:
            bits = []
            for name in ("50", "90", "95", "99"):
                if name in values:
                    bits.append(f"{name}th={values[name]:g} ms")
            p99 = values.get("p99", values.get("99"))
            if p99 is not None:
                bits.append(f"p99={p99:g} ms")
            lines.append(f"    {pkg}: " + ", ".join(bits) + f" ({source})")
        if frames_summary:
            lines.append("  " + "; ".join(frames_summary[:3]))
    else:
        lines.append("  Percentis: não encontrado (frames.txt sem 50th/90th/95th/99th percentile/p99)")
    return lines


def parse_percentiles(
    frames_files: Sequence[Path],
    surface_files: Sequence[Path],
    session: Session,
) -> Tuple[List[Tuple[str, Dict[str, float], str]], List[str]]:
    out: "OrderedDict[str, Tuple[Dict[str, float], str]]" = OrderedDict()
    summaries: List[str] = []
    for path in list(frames_files) + list(surface_files):
        pkg = "(sem pacote)"
        source = session.source_label(path)
        for line in read_text_lines(path):
            stripped = line.strip()
            if stripped.startswith("== "):
                pkg = stripped[3:].strip() or pkg
                continue
            gm = re.search(r"Graphics info for pid \d+ \[([^\]]+)\]", line)
            if gm:
                pkg = gm.group(1)
            if re.search(r"Total frames|Janky frames", line, re.I):
                summaries.append(f"{pkg}: {shorten(line, 100)}")
            m = re.search(
                r"\b(50|90|95|99)(?:th|st|nd|rd)?\s+percentile\s*[:=]\s*"
                r"([0-9]+(?:[.,][0-9]+)?)\s*(ms|us|µs|ns|s)?",
                line, re.I,
            )
            if m:
                value = to_ms(float(m.group(2).replace(",", ".")), m.group(3) or "ms")
                entry = out.setdefault(pkg, ({}, source))
                entry[0][m.group(1)] = value
                continue
            m = re.search(
                r"\bp99\s*[:=]\s*([0-9]+(?:[.,][0-9]+)?)\s*(ms|us|µs|ns|s)?",
                line, re.I,
            )
            if m:
                value = to_ms(float(m.group(1).replace(",", ".")), m.group(2) or "ms")
                entry = out.setdefault(pkg, ({}, source))
                entry[0]["p99"] = value
    result = [(pkg, values, src) for pkg, (values, src) in out.items() if values]
    return result, summaries


# ---------------------------------------------------------------------------
# 5. falhas repetidas
# ---------------------------------------------------------------------------

def section_failures(logs: dict) -> List[str]:
    lines = ["== 5. Mensagens de falha repetida (logcat) =="]
    files = logs["files"]
    if not files:
        lines.append(
            "logcat: não encontrado (procurado oneos_logs/boot.log*, "
            "head/boot.log* e boot.log* na raiz)"
        )
        return lines
    lines.append(
        f"logcat: {len(files)} arquivo(s), {logs['lines']} linha(s); "
        f"{logs['failure_total']} linha(s) casaram os filtros; "
        f"{len(logs['failures'])} mensagem(ns) normalizada(s)"
    )
    if not logs["failures"]:
        lines.append("  nenhuma mensagem casou os filtros")
        return lines
    ordered = sorted(
        logs["failures"].items(),
        key=lambda item: (-item[1], logs["failure_first"].get(item[0], ((99, 99, 99, 99, 99, 99),))[0]
                          or (99, 99, 99, 99, 99, 99)),
    )
    for idx, (norm, count) in enumerate(ordered[:20], 1):
        first = logs["failure_first"].get(norm)
        if first:
            ts_tuple, ts_text, rel, _tag = first
            when = ts_text or "sem timestamp"
            tags = ", ".join(sorted(logs["failure_tags"][norm])[:3]) or "?"
        else:
            when, rel, tags = "sem timestamp", "?", "?"
        lines.append(
            f"  {idx}. {count}x  {shorten(norm, 150)} "
            f"[1ª: {when} em {rel}; tag {tags}]"
        )
    return lines


# ---------------------------------------------------------------------------
# 6. skin
# ---------------------------------------------------------------------------

def section_skin(summary: dict) -> List[str]:
    lines = ["== 6. Temperatura de skin =="]
    diag = summary.get("max_skin_diag")
    heat = summary.get("max_skin_heat")
    if diag is None and heat is None:
        lines.append("Skin máxima: não encontrado (sem skin= em diag_series.txt/heat_cap.log)")
        return lines
    if diag is not None:
        lines.append(
            f"Skin máxima (diag_series.txt): {diag:g} °C em {summary.get('max_skin_diag_ts') or '?'}"
        )
    else:
        lines.append("Skin máxima (diag_series.txt): não encontrado")
    if heat is not None:
        lines.append(
            f"Skin máxima (heat_cap.log): {heat:g} °C em {summary.get('max_skin_heat_ts') or '?'}"
        )
    else:
        lines.append("Skin máxima (heat_cap.log): não encontrado")
    return lines


# ---------------------------------------------------------------------------
# extração e relatório
# ---------------------------------------------------------------------------

def validate_tar_members(members: Sequence[tarfile.TarInfo], strict: bool = False) -> None:
    """Valida caminhos sempre; no fallback sem filter, também links/modos."""
    for member in members:
        name = member.name
        if name.startswith("/") or ".." in Path(name).parts:
            raise ValueError(f"caminho inseguro no tgz: {name}")
        if not strict:
            continue
        if member.issym() or member.islnk():
            raise ValueError(f"link não permitido no tgz: {name}")
        if member.isdev() or member.isfifo():
            raise ValueError(f"tipo especial não permitido no tgz: {name}")
        if member.mode & 0o7022:
            raise ValueError(
                f"permissões perigosas no tgz: {name} ({member.mode:o})"
            )


def extract_session(tgz: Path, dest: Path) -> None:
    with tarfile.open(tgz, "r:*") as tar:
        members = tar.getmembers()
        if not members:
            raise ValueError("o tgz não tem nenhum membro")
        validate_tar_members(members)
        regular = [m for m in members if m.isfile()]
        if not regular:
            raise ValueError("o tgz não tem nenhum arquivo regular")
        try:
            tar.extractall(dest, filter="data")
        except TypeError:
            # Python antigo sem filter="data": valida tudo antes de extrair.
            validate_tar_members(members, strict=True)
            tar.extractall(dest)


def limit_report(lines: List[str], max_lines: int = MAX_REPORT_LINES) -> List[str]:
    """Corta detalhes dentro das seções, preservando a seção 6 e o rodapé."""
    if len(lines) <= max_lines:
        return lines
    skin_start = next(
        (i for i, line in enumerate(lines) if line.startswith("== 6.")),
        len(lines),
    )
    head = lines[:skin_start]
    tail = lines[skin_start:]
    preferred = [
        i for i, line in enumerate(head)
        if line.startswith(("  ", "    "))
        and line.strip()
        and "resultado:" not in line
    ]
    preferred_set = set(preferred)
    others = [
        i for i, line in enumerate(head)
        if line.strip()
        and not line.startswith("==")
        and i not in preferred_set
    ]
    order = list(reversed(preferred)) + list(reversed(others))
    excess = len(lines) - (max_lines - 1)
    remove = set(order[: max(0, excess)])
    out_head = [line for i, line in enumerate(head) if i not in remove]
    while len(out_head) + len(tail) + 1 > max_lines:
        for i in range(len(out_head) - 1, -1, -1):
            if out_head[i].strip() and not out_head[i].startswith("=="):
                del out_head[i]
                break
        else:
            break
    omitted = len(lines) - len(out_head) - len(tail)
    out = out_head + tail
    marker = (
        f"... relatório truncado: {omitted} linha(s) omitida(s) "
        f"(limite ~{max_lines})"
    )
    footer = next((i for i, line in enumerate(out) if line == "--"), len(out))
    out.insert(footer, marker)
    return out


def build_report(tgz: Path, session: Session, logs: dict) -> List[str]:
    lines = [
        "OneOS — análise offline de sessão (somente leitura)",
        f"Sessão: {tgz.name} ({fmt_size(tgz.stat().st_size)})",
        "",
    ]
    model_lines = section_model(session, logs)
    ceiling_lines, ceiling_summary = section_ceilings(session, logs)
    cpu_lines = section_cpu(session)
    kick_lines = analyze_kick(session, logs["kick_lines"])
    frame_lines = section_frames(session)
    failure_lines = section_failures(logs)
    skin_lines = section_skin(ceiling_summary)

    lines.extend(model_lines)
    lines.append("")
    lines.extend(ceiling_lines)
    lines.append("")
    lines.extend(cpu_lines)
    lines.append("")
    lines.extend(kick_lines)
    lines.extend(frame_lines)
    lines.append("")
    lines.extend(failure_lines)
    lines.append("")
    lines.extend(skin_lines)
    lines.extend([
        "",
        "--",
        "Fim do relatório. Nenhum arquivo da sessão foi alterado.",
    ])
    return limit_report(lines)


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        print("uso: analyze-session.py <oneos_session_full.tgz>", file=sys.stderr)
        return 2
    tgz = Path(argv[1])
    if not tgz.exists() or not tgz.is_file():
        print(f"erro: sessão não encontrada: {tgz}", file=sys.stderr)
        return 2
    try:
        size = tgz.stat().st_size
    except OSError as exc:
        print(f"erro: não foi possível ler {tgz}: {exc}", file=sys.stderr)
        return 2
    if size == 0:
        print(f"erro: tgz vazio: {tgz}", file=sys.stderr)
        return 2

    tmp = tempfile.mkdtemp(prefix="oneos-analyze-")
    try:
        try:
            extract_session(tgz, Path(tmp))
        except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
            print(f"erro: não foi possível ler o tgz {tgz}: {exc}", file=sys.stderr)
            return 2
        session = Session(Path(tmp))\n        if not session.files:
            print(f"erro: sessão vazia: nenhum arquivo extraído de {tgz}", file=sys.stderr)
            return 2
        logs = scan_logcat(session)
        report = build_report(tgz, session, logs)
        print("\n".join(report))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
