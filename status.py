#!/usr/bin/env python3
"""Antigravity IDE — Status Line Script
Row 1: model · state · context left · path · quota · session tokens
Row 2: 💡 Tip  (rotates at most once every 3 seconds, persisted across restarts)
"""
import sys
import json
import time
import os
import random
import select
import re
import subprocess
import http.client
import ssl
import shutil
import unicodedata
from datetime import datetime, timezone
import traceback

# ── ANSI Colors ────────────────────────────────────────────────────────────────
RESET        = "\033[0m"
BOLD         = "\033[1m"
GRAY         = "\033[90m"
WHITE        = "\033[37m"
GREEN        = "\033[32m"
YELLOW       = "\033[33m"
ORANGE       = "\033[38;5;208m"
LIGHT_ORANGE = "\033[38;5;214m"
RED          = "\033[31m"
MAGENTA      = "\033[35m"
CYAN         = "\033[36m"
BLUE         = "\033[34m"
PURPLE       = "\033[38;5;141m"

# ── Tips ───────────────────────────────────────────────────────────────────────
TIPS = [
    "Use /goal for complex, multi-step tasks that need deep focus.",
    "Press Ctrl+O to expand tool output details inline.",
    "Try /grill-me to refine your implementation plan interactively.",
    "Use /agents to see all active sub-agents and their status.",
    "Mention @conversation to reference past work in any session.",
    "Run agy --sandbox for restricted, sandboxed command execution.",
    "Use /schedule to automate recurring tasks or set reminders.",
    "Press Ctrl+R to search through your full command history.",
    "Use /changelog to see what's new in the latest release.",
    "Drag a file into the chat to attach it as context.",
    "Use /goal overnight for thorough, unattended long-running tasks.",
    "Prefix a message with ! to run it as a shell command directly.",
]

TIP_CACHE_FILE = "/tmp/agy_status_tip.json"
TIP_MIN_SECONDS = 3.0   # minimum seconds before rotating to the next tip
QUOTA_CACHE_FILE = os.environ.get(
    "AGY_QUOTA_CACHE",
    os.path.expanduser("~/.gemini/antigravity-cli/quota-cache.json"),
)
STATUS_STATE_FILE = os.environ.get(
    "AGY_STATUS_STATE",
    os.path.expanduser("~/.gemini/antigravity-cli/status-state.json"),
)
STATUS_ERROR_LOG = os.environ.get(
    "AGY_STATUS_ERROR_LOG",
    os.path.expanduser("~/.gemini/antigravity-cli/status-error.log"),
)
QUOTA_MAX_AGE_SECONDS = float(os.environ.get("AGY_QUOTA_MAX_AGE_SECONDS", "900"))
QUOTA_REFRESH_INTERVAL_SECONDS = float(os.environ.get("AGY_QUOTA_REFRESH_INTERVAL_SECONDS", "30"))
USER_STATUS_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"

# ── Error Logging & Safety Helpers ─────────────────────────────────────────────
MAX_ERROR_LOG_LINES = 300

def record_error(exc: Exception, raw_data: any = None) -> None:
    try:
        os.makedirs(os.path.dirname(STATUS_ERROR_LOG), exist_ok=True)
        with open(STATUS_ERROR_LOG, "a", encoding="utf-8") as f:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n========================================\n")
            f.write(f"[{now_str}] Status Line Error: {type(exc).__name__}: {exc}\n")
            traceback.print_exc(file=f)
            if raw_data is not None:
                try:
                    payload_sample = json.dumps(raw_data, ensure_ascii=False)[:1000]
                    f.write(f"Payload sample: {payload_sample}\n")
                except Exception:
                    pass
        # 限制日志最大行数（最多保留最新 300 行，自动修剪历史冗余）
        try:
            if os.path.exists(STATUS_ERROR_LOG):
                with open(STATUS_ERROR_LOG, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                if len(lines) > MAX_ERROR_LOG_LINES:
                    with open(STATUS_ERROR_LOG, "w", encoding="utf-8") as f:
                        f.writelines(lines[-MAX_ERROR_LOG_LINES:])
        except Exception:
            pass
    except Exception:
        pass


def safe_int(v, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(float(v))
    except (ValueError, TypeError):
        return default


def safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


# ── Helpers ────────────────────────────────────────────────────────────────────
def format_tokens(n: int) -> str:
    n = safe_int(n, 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def context_color(pct: float) -> str:
    if pct >= 50:
        return GREEN
    elif pct >= 20:
        return ORANGE
    else:
        return RED


def normalize_model_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def quota_color(pct: float) -> str:
    if pct >= 50:
        return PURPLE
    if pct >= 20:
        return LIGHT_ORANGE
    return RED


def format_reset_time(reset_time: str) -> str:
    try:
        reset = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
        diff = int((reset - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return ""

    if diff <= 0:
        return "now"
    minutes = (diff + 59) // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    if hours >= 24:
        days, rem_hours = divmod(hours, 24)
        return f"{days}d {rem_hours}h" if rem_hours else f"{days}d"
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def extract_arg(command_line: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}(?:=|\s+)([^\s\"']+|\"[^\"]+\"|'[^']+')", command_line)
    if not match:
        return ""
    return match.group(1).strip("\"'")


def find_server_candidates() -> list[dict]:
    try:
        ps = subprocess.check_output(["ps", "auxww"], text=True, timeout=1.5)
    except Exception:
        return []

    candidates = []
    for line in ps.splitlines():
        lower = line.lower()
        is_cli = re.search(r"\bagy(\s|$)", line) is not None
        is_language_server = "language_server" in lower
        if not is_cli and not is_language_server:
            continue
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        token = extract_arg(parts[10], "--csrf_token")
        score = 10
        if is_cli:
            score += 40
        if is_language_server:
            score += 20
        if token:
            score += 10
        if "/applications/antigravity.app" in lower:
            score -= 10
        candidates.append({
            "pid": pid,
            "csrf_token": token,
            "score": score,
            "kind": "cli" if is_cli else "language_server",
        })

    return sorted(candidates, key=lambda x: x["score"], reverse=True)


def get_listening_ports(pid: int) -> list[int]:
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
            text=True,
            timeout=1.5,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return []

    ports = []
    for match in re.finditer(r":(\d+)\s+\(LISTEN\)", out):
        port = int(match.group(1))
        if port not in ports:
            ports.append(port)
    return sorted(ports)


def request_user_status(port: int, csrf_token: str, use_https: bool) -> dict:
    body = json.dumps({
        "metadata": {
            "ideName": "antigravity",
            "extensionName": "antigravity",
            "locale": "en",
        }
    })
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
    }
    if csrf_token:
        headers["X-Codeium-Csrf-Token"] = csrf_token
    if use_https:
        conn = http.client.HTTPSConnection(
            "127.0.0.1",
            port,
            timeout=2,
            context=ssl._create_unverified_context(),
        )
    else:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request("POST", USER_STATUS_PATH, body, headers)
    res = conn.getresponse()
    raw = res.read().decode("utf-8", "replace")
    if res.status < 200 or res.status >= 300:
        raise RuntimeError(f"HTTP {res.status}")
    return json.loads(raw)


def parse_user_status_quota(response: dict) -> dict:
    user_status = response.get("userStatus", {})
    plan_status = user_status.get("planStatus", {})
    plan_info = plan_status.get("planInfo", {})
    cascade = user_status.get("cascadeModelConfigData", {})
    models = {}

    for model in cascade.get("clientModelConfigs", []) or []:
        quota_info = model.get("quotaInfo") or {}
        if "remainingFraction" not in quota_info:
            continue
        label = model.get("label") or model.get("modelOrAlias", {}).get("model") or "Unknown"
        remaining = max(0.0, min(100.0, float(quota_info.get("remainingFraction", 0)) * 100))
        entry = {
            "name": label,
            "remaining_percentage": remaining,
            "source": "local_language_server",
        }
        reset_time = quota_info.get("resetTime")
        if reset_time:
            entry["reset_time"] = reset_time
            entry["refreshes_in"] = format_reset_time(reset_time)
        models[normalize_model_name(label)] = entry

    return {
        "timestamp": time.time(),
        "source": "local_language_server",
        "scope": {
            "email": user_status.get("email") or "",
            "plan_tier": plan_info.get("planName") or "",
        },
        "models": models,
    }


def fetch_live_quota_cache(expected_email: str = "") -> dict:
    fallback = {}
    expected = (expected_email or "").lower()

    for process_info in find_server_candidates():
        ports = get_listening_ports(process_info["pid"])
        for port in ports:
            for use_https in (True, False):
                try:
                    response = request_user_status(
                        port,
                        process_info.get("csrf_token", ""),
                        use_https,
                    )
                    cache = parse_user_status_quota(response)
                    if not cache.get("models"):
                        continue
                    cache["source_process"] = process_info.get("kind", "")
                    cache["source_port"] = port
                    email = str(cache.get("scope", {}).get("email", "")).lower()
                    if expected and email == expected:
                        return cache
                    if not fallback:
                        fallback = cache
                except Exception:
                    continue
    return fallback


def read_json_file(path: str) -> dict:
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_quota_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(QUOTA_CACHE_FILE), exist_ok=True)
        with open(QUOTA_CACHE_FILE, "w") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
    except Exception:
        pass


def quota_scope(data: dict) -> dict:
    return {
        "email": data.get("email") or "",
        "plan_tier": data.get("plan_tier") or "",
    }


def write_status_state(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATUS_STATE_FILE), exist_ok=True)
        old_state = read_json_file(STATUS_STATE_FILE)
        state = quota_scope(data)
        state["session_id"] = data.get("conversation_id") or data.get("session_id") or ""
        model_info = data.get("model", {})
        if isinstance(model_info, dict):
            state["model"] = model_info.get("display_name") or model_info.get("id") or ""
        else:
            state["model"] = str(model_info or "")
        state["timestamp"] = time.time()

        
        # 保留指标计时字段
        for k in (
            "total_active_time",
            "last_step_time",
            "last_tps",
            "current_execution_start",
            "current_start_tokens",
            "last_state",
            "active_subagents_count",
        ):
            if k in old_state:
                state[k] = old_state[k]

        with open(STATUS_STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
    except Exception:
        pass


def scope_mismatch(cache: dict, data: dict) -> str:
    expected = quota_scope(data)
    actual = cache.get("scope", {})
    if not isinstance(actual, dict):
        return "scope"

    for key, label in (
        ("email", "account"),
        ("plan_tier", "plan"),
    ):
        # CLI status JSON says "Google AI Pro"; local API says "Pro".
        if key == "plan_tier" and expected.get(key) and actual.get(key):
            if expected[key].lower().endswith(str(actual[key]).lower()):
                continue
        if expected.get(key) and actual.get(key) and actual.get(key) != expected.get(key):
            return label
    return ""


def should_refresh_quota(data: dict, cache: dict) -> bool:
    if not cache:
        return True
    now = time.time()
    ts = float(cache.get("timestamp", 0) or 0)
    if not ts or now - ts >= QUOTA_REFRESH_INTERVAL_SECONDS:
        return True
    state = read_json_file(STATUS_STATE_FILE)
    current_session = data.get("conversation_id") or data.get("session_id") or ""
    if current_session and state.get("session_id") and state.get("session_id") != current_session:
        return True
    if scope_mismatch(cache, data):
        return True
    return False


def refresh_quota_if_needed(data: dict) -> dict:
    cache = read_json_file(QUOTA_CACHE_FILE)
    if should_refresh_quota(data, cache):
        live_cache = fetch_live_quota_cache(data.get("email") or "")
        if live_cache:
            cache = live_cache
            write_quota_cache(cache)
    return cache


def load_quota_for_model(model_name: str, data: dict) -> dict:
    """Read the latest /usage cache and return the entry for the active model."""
    cache = refresh_quota_if_needed(data)
    if not cache:
        return {}

    mismatch = scope_mismatch(cache, data)
    if mismatch:
        return {"stale": True, "reason": mismatch}

    ts = float(cache.get("timestamp", 0) or 0)
    if ts and time.time() - ts > QUOTA_MAX_AGE_SECONDS:
        return {"stale": True, "reason": "age"}

    models = cache.get("models", {})
    if not isinstance(models, dict):
        return {}

    wanted = normalize_model_name(model_name)
    if wanted in models:
        return models[wanted]

    for key, value in models.items():
        key_norm = normalize_model_name(key)
        if key_norm and (key_norm in wanted or wanted in key_norm):
            return value
    return {}


def get_tip(force_rotate=False) -> str:
    """Return the current tip. Rotate if force_rotate is True or if time is up."""
    now = time.time()
    tip = ""
    ts = 0.0

    try:
        with open(TIP_CACHE_FILE, "r") as f:
            cache = json.load(f)
        tip = cache.get("tip", "")
        ts  = float(cache.get("timestamp", 0))
    except Exception:
        pass

    if tip and not force_rotate and (now - ts) < TIP_MIN_SECONDS:
        return tip

    # Pick a new tip that's different from the current one if possible
    choices = [t for t in TIPS if t != tip] or TIPS
    new_tip = random.choice(choices)
    
    try:
        with open(TIP_CACHE_FILE, "w") as f:
            json.dump({"tip": new_tip, "timestamp": now}, f)
    except Exception:
        pass
        
    return new_tip


# ── Rendering ──────────────────────────────────────────────────────────────────

def visible_len(s: str) -> int:
    no_ansi = re.sub(r"\033\[[0-9;]*[a-zA-Z]", "", s)
    w = 0
    for ch in no_ansi:
        w += 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
    return w

SEP = f" {GRAY}|{RESET} "


def format_max_ctx(n: int) -> str:
    """自动将后端返回的最大上下文转换为 200k / 1M / 2M 格式"""
    n = safe_int(n, 0)
    if n >= 1_000_000:
        m = n / 1_048_576 if n >= 1_048_000 else n / 1_000_000
        return f"{int(m)}M" if m.is_integer() else f"{m:.1f}M"
    if n >= 1_000:
        k = n / 1024 if n % 1024 == 0 else n / 1000
        return f"{int(k)}k" if k.is_integer() else f"{k:.0f}k"
    return str(n) if n > 0 else "auto"


def format_seconds(sec: int) -> str:
    sec = safe_int(sec, 0)
    if sec <= 0:
        return "now"
    minutes = (sec + 59) // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    if hours >= 24:
        days, rem_h = divmod(hours, 24)
        return f"{days}d {rem_h}h" if rem_h else f"{days}d"
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def format_duration(sec: float, include_tenths_under_60: bool = False) -> str:
    sec = safe_float(sec, 0.0)
    if include_tenths_under_60 and sec < 59.95:
        return f"{sec:.1f}s"

    s = int(round(max(0, sec)))
    if s < 60:
        return f"{s}s"
    mins, sec_rem = divmod(s, 60)
    if mins < 60:
        return f"{mins}m{sec_rem:02d}s" if sec_rem else f"{mins}m"
    hours, mins_rem = divmod(mins, 60)
    return f"{hours}h{mins_rem:02d}m"


def format_session_time(sec: float) -> str:
    return format_duration(sec, include_tenths_under_60=False)


def format_model_time(sec: float, is_working: bool = False, active_subs: int = 0) -> str:
    sec = safe_float(sec, 0.0)
    if is_working:
        sub_badge = ""
        if active_subs > 0:
            badge_text = f"({active_subs} sub)" if active_subs == 1 else f"({active_subs} subs)"
            sub_badge = f" {MAGENTA}{badge_text}{CYAN}"
        dur_str = format_duration(sec, include_tenths_under_60=True)
        return f"⏱ {dur_str}{sub_badge}"

    if sec <= 0:
        return "-"
    return format_duration(sec, include_tenths_under_60=True)


def format_tps(tps: float) -> str:
    tps = safe_float(tps, 0.0)
    if tps <= 0:
        return "- t/s"
    return f"{tps:.0f} t/s" if tps >= 10 else f"{tps:.1f} t/s"


def check_activity(data: dict) -> tuple[bool, int]:
    """
    检查系统是否处于活跃干活状态（主会话在工作，或有 subagent 在运行，或有后台任务）。
    返回: (is_active: bool, active_subagents_count: int)
    """
    if not isinstance(data, dict):
        return False, 0

    curr_state = str(data.get("agent_state") or data.get("last_state") or "idle").strip().lower()

    # 1. 提取活跃子智能体
    active_subs = 0
    subagents = data.get("subagents")
    if isinstance(subagents, dict):
        subagents = list(subagents.values())
    if isinstance(subagents, list):
        inactive_states = {
            "idle", "done", "finished", "completed", "killed", 
            "canceled", "cancelled", "errored", "error", "terminated", "closed"
        }
        for s in subagents:
            if isinstance(s, dict):
                st = str(
                    s.get("agent_state") or 
                    s.get("state") or 
                    s.get("status") or 
                    s.get("lifecycle") or 
                    ""
                ).strip().lower()
                if st in inactive_states:
                    continue
                active_subs += 1
            elif s:
                active_subs += 1
    elif "active_subagents_count" in data:
        active_subs = safe_int(data.get("active_subagents_count"), 0)

    # 2. 检查是否有运行中的后台任务（如 background_tasks / tasks）
    running_tasks = 0
    tasks = data.get("background_tasks") or data.get("tasks")
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    if isinstance(tasks, list):
        for t in tasks:
            if isinstance(t, dict):
                st = str(t.get("status") or t.get("state") or "").strip().lower()
                if st in ("running", "active", "executing"):
                    running_tasks += 1

    # 3. 活跃判定：只要主模型在 working，或有活跃 subagent，或有后台任务在跑，均为活跃干活状态
    is_active = (curr_state == "working") or (active_subs > 0) or (running_tasks > 0)
    return is_active, active_subs


# ── Metrics Tracker ────────────────────────────────────────────────────────────
class SessionMetricsTracker:
    def __init__(self, state_file: str):
        self.state_file = state_file
        self.session_id = ""
        self.total_active_time = 0.0
        self.last_step_time = 0.0
        self.last_tps = 0.0
        self.current_execution_start = 0.0
        self.current_start_tokens = 0
        self.last_state = "idle"
        self.active_subagents_count = 0
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r") as f:
                    d = json.load(f)
                    self.session_id = str(d.get("session_id") or "")
                    self.total_active_time = safe_float(d.get("total_active_time"), 0.0)
                    self.last_step_time = safe_float(d.get("last_step_time"), 0.0)
                    self.last_tps = safe_float(d.get("last_tps"), 0.0)
                    self.current_execution_start = safe_float(d.get("current_execution_start"), 0.0)
                    self.current_start_tokens = safe_int(d.get("current_start_tokens"), 0)
                    self.last_state = str(d.get("last_state") or "idle")
                    self.active_subagents_count = safe_int(d.get("active_subagents_count"), 0)
        except Exception:
            pass

    def _save(self):
        try:
            state = {}
            if os.path.exists(self.state_file):
                try:
                    with open(self.state_file, "r") as f:
                        state = json.load(f)
                except Exception:
                    pass
            state["session_id"] = self.session_id
            state["total_active_time"] = self.total_active_time
            state["last_step_time"] = self.last_step_time
            state["last_tps"] = self.last_tps
            state["current_execution_start"] = self.current_execution_start
            state["current_start_tokens"] = self.current_start_tokens
            state["last_state"] = self.last_state
            state["active_subagents_count"] = self.active_subagents_count
            with open(self.state_file, "w") as f:
                json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:
            pass

    def update(self, data: dict):
        if not isinstance(data, dict):
            return

        now = time.time()
        sid = str(data.get("session_id") or data.get("conversation_id") or "")
        is_active, active_subs = check_activity(data)
        self.active_subagents_count = active_subs

        cw = data.get("context_window") or {}
        if not isinstance(cw, dict):
            cw = {}
        curr_out_tokens = safe_int(cw.get("total_output_tokens"), 0)

        # 1. 会话变更检测：新会话重置累计时间
        if sid and sid != self.session_id:
            self.session_id = sid
            self.total_active_time = 0.0
            self.last_step_time = 0.0
            self.last_tps = 0.0
            self.current_execution_start = now if is_active else 0.0
            self.current_start_tokens = curr_out_tokens
            self.last_state = "working" if is_active else "idle"
            self._save()
            return

        # 2. 状态机流转与执行时间精确结算：
        if is_active:
            # 刚进入活跃干活（或首次记录）：标记本次执行开始（“第一个启动”）
            if self.current_execution_start <= 0.0:
                self.current_execution_start = now
                self.current_start_tokens = curr_out_tokens
                self._save()
            else:
                # 仍在活跃执行中，更新实时速率
                cur_dur = max(0.1, now - self.current_execution_start)
                delta_tokens = max(0, curr_out_tokens - self.current_start_tokens)
                if delta_tokens > 0:
                    self.last_tps = delta_tokens / cur_dur

        else:
            # 当前完全空闲（“最后一个结束”）：
            if self.current_execution_start > 0.0:
                # 计算从第一个启动到最后一个结束的真实物理耗时
                final_dur = max(0.1, now - self.current_execution_start)

                # 防异常跨度（例如休眠/跨天重开导致时间异常虚高，超过 4 小时则丢弃）
                if final_dur < 14400.0:
                    delta_tokens = max(0, curr_out_tokens - self.current_start_tokens)
                    if delta_tokens > 0:
                        self.last_tps = delta_tokens / final_dur

                    # 【核心】：将物理耗时累计到总时间上去！
                    self.total_active_time += final_dur
                    self.last_step_time = final_dur

                self.current_execution_start = 0.0  # 结算完成，清零本轮开始时间
                self._save()

        self.last_state = "working" if is_active else "idle"

    def get_metrics(self, is_active: bool, active_subs: int = 0) -> tuple[float, float, float, bool, int]:
        now = time.time()
        is_working = (is_active and self.current_execution_start > 0.0)

        if is_working:
            # 执行中：本次耗时 = 当前已走过的秒数
            cur_step_time = max(0.1, now - self.current_execution_start)
            # 执行中总时间 = 历史已结算总时间 + 本轮正在进行的秒数
            total_time = self.total_active_time + cur_step_time
            return total_time, cur_step_time, self.last_tps, True, active_subs
        else:
            # 空闲时：显示历史已结算总时间，以及上一轮结算的真实耗时与速率
            return self.total_active_time, self.last_step_time, self.last_tps, False, 0


metrics_tracker = SessionMetricsTracker(STATUS_STATE_FILE)


def render(data: dict) -> str:
    try:
        return _render_internal(data)
    except Exception as e:
        record_error(e, data)
        err_type = type(e).__name__
        err_msg = str(e).strip()[:25]
        cwd = data.get("cwd", "") if isinstance(data, dict) else ""
        dir_name = os.path.basename(str(cwd).rstrip("/")) or str(cwd) or "workspace"
        return f"{RED}⚠️ 状态栏异常 [{err_type}: {err_msg}]{RESET} · 详见 status-error.log {GRAY}|{RESET} {GREEN}dir:{dir_name}{RESET}"


def resolve_quota_for_model(model_name: str, quota_dict: dict) -> tuple[dict, dict]:
    if not isinstance(quota_dict, dict) or not quota_dict:
        return {}, {}

    m_lower = model_name.lower()
    is_argon = "argon" in m_lower
    is_3p = any(x in m_lower for x in ["claude", "gpt", "o1", "o3", "deepseek"])

    # 1. 匹配长期/周限制配额 (统一对外标签为 Week)
    q_long = None
    if is_argon:
        for k, v in quota_dict.items():
            if "argon" in k and ("week" in k or "wtu" in k):
                q_long = v
                break
        if not q_long:
            q_long = quota_dict.get("gemini-argon-wtu-weekly")
    elif is_3p:
        for k, v in quota_dict.items():
            if ("3p" in k or "claude" in k or "gpt" in k) and "week" in k:
                q_long = v
                break
        if not q_long:
            q_long = quota_dict.get("3p-weekly")

    if not q_long:
        q_long = quota_dict.get("gemini-weekly")
        for k, v in quota_dict.items():
            if "week" in k and "3p" not in k and "argon" not in k:
                q_long = v
                break

    # 2. 匹配短期 5小时限制配额
    q_short = None
    if is_argon:
        for k, v in quota_dict.items():
            if "argon" in k and "5h" in k:
                q_short = v
                break
        if not q_short:
            q_short = quota_dict.get("gemini-5h")
    elif is_3p:
        q_short = quota_dict.get("3p-5h")
        if not q_short:
            for k, v in quota_dict.items():
                if ("3p" in k or "claude" in k) and "5h" in k:
                    q_short = v
                    break
    else:
        q_short = quota_dict.get("gemini-5h")

    if not q_short:
        for k, v in quota_dict.items():
            if "5h" in k and not v.get("disabled"):
                q_short = v
                break

    if not q_long:
        for k, v in quota_dict.items():
            if "week" in k and not v.get("disabled"):
                q_long = v
                break

    return q_short or {}, q_long or {}


def _render_internal(data: dict) -> str:
    if not isinstance(data, dict):
        data = {}

    # 1. 更新指标跟踪（带防崩兜底）
    try:
        metrics_tracker.update(data)
    except Exception as e:
        record_error(e, data)

    # 2. 模型（思考级别情况）兼容多字段与类型
    model_obj = data.get("model") or data.get("model_info") or data.get("active_model") or {}
    if isinstance(model_obj, dict):
        raw_name = model_obj.get("display_name") or model_obj.get("id") or model_obj.get("name") or "AGY"
        effort = model_obj.get("effort")
        if effort and effort.lower() not in raw_name.lower():
            model_name = f"{raw_name} ({effort.capitalize()})"
        else:
            model_name = raw_name
    elif isinstance(model_obj, str) and model_obj:
        model_name = model_obj
    else:
        model_name = "AGY"
    model_display = f"{YELLOW}{BOLD}{model_name}{RESET}"

    # 3. 当前目录：dir:test
    cwd = data.get("cwd") or data.get("current_dir") or data.get("project_dir") or (data.get("workspace") or {}).get("current_dir") or (data.get("workspace") or {}).get("project_dir") or ""
    dir_name = os.path.basename(str(cwd).rstrip("/")) or str(cwd) or "workspace"
    dir_display = f"{GREEN}dir:{dir_name}{RESET}"

    # 4. ↑0 ↓0 (输入/输出 token 统计)
    cw = data.get("context_window") or {}
    if not isinstance(cw, dict):
        cw = {}
    in_t = safe_int(cw.get("total_input_tokens"), 0)
    out_t = safe_int(cw.get("total_output_tokens"), 0)
    tokens_io = f"{GRAY}↑{format_tokens(in_t)} ↓{format_tokens(out_t)}{RESET}"

    # 5. 当前上下文占用多少 tok
    used_pct = safe_float(cw.get("used_percentage"), 0.0)
    max_ctx = safe_int(cw.get("context_window_size"), 0)
    current_tok = int(max_ctx * used_pct / 100) if max_ctx > 0 else (in_t + out_t)
    tok_display = f"{WHITE}{format_tokens(current_tok)} tok{RESET}"

    # 6. 上下文占用百分比 / 模型后端支持的最大上下文 (例如: 6%/1M 或 10%/200k)
    rem_pct = safe_float(cw.get("remaining_percentage"), 100.0 - used_pct)
    cc = context_color(rem_pct)
    max_ctx_str = format_max_ctx(max_ctx) if max_ctx > 0 else "auto"
    ctx_ratio_display = f"{cc}{used_pct:.0f}%/{max_ctx_str}{RESET}"

    # 7. 会话总时间 / 每次模型执行的时间
    try:
        is_active, active_subs = check_activity(data)
        session_sec, model_sec, tps, is_working, num_subs = metrics_tracker.get_metrics(is_active, active_subs)
        time_str = f"{format_session_time(session_sec)}/{format_model_time(model_sec, is_working, num_subs)}"
        time_display = f"{CYAN}{time_str}{RESET}"
        tps_str = format_tps(tps)
        tps_display = f"{LIGHT_ORANGE}{tps_str}{RESET}"
    except Exception as e:
        record_error(e, data)
        time_display = f"{CYAN}-/-{RESET}"
        tps_display = f"{LIGHT_ORANGE}- t/s{RESET}"

    # 8. Quota 配额与倒计时（包含 4h/5h 短周期与周限制）
    try:
        quota_dict = data.get("quota") or {}
        if not isinstance(quota_dict, dict):
            quota_dict = {}

        q_5h, q_week = resolve_quota_for_model(model_name, quota_dict)

        if isinstance(q_5h, dict) and "remaining_fraction" in q_5h:
            pct_5h = int(round(safe_float(q_5h.get("remaining_fraction")) * 100))
            qc_5h = quota_color(pct_5h)
            reset_sec_5h = safe_int(q_5h.get("reset_in_seconds"), 0)
            reset_5h_str = f" · reset {format_seconds(reset_sec_5h)}" if reset_sec_5h > 0 else ""

            # 周限制统一命名为 Week
            week_str = ""
            if isinstance(q_week, dict) and "remaining_fraction" in q_week:
                pct_week = int(round(safe_float(q_week.get("remaining_fraction")) * 100))
                qc_week = quota_color(pct_week)
                reset_sec_week = safe_int(q_week.get("reset_in_seconds"), 0)
                reset_week_str = f" · reset {format_seconds(reset_sec_week)}" if reset_sec_week > 0 else ""
                week_str = f" · Week: {qc_week}{pct_week}%{RESET}{reset_week_str}"

            quota_display = f"{qc_5h}Quota: {pct_5h}%{RESET}{reset_5h_str}{week_str}"
        else:
            # 兼容备用方案：读取本地缓存
            quota = load_quota_for_model(model_name, data)
            if isinstance(quota, dict) and "remaining_percentage" in quota:
                q_pct = safe_float(quota.get("remaining_percentage"))
                qc = quota_color(q_pct)
                reset_in = str(quota.get("refreshes_in") or "")
                reset_str = f" · reset {reset_in}" if reset_in else ""
                quota_display = f"{qc}Quota: {q_pct:.0f}%{reset_str}{RESET}"
            else:
                quota_display = f"{GRAY}Quota: sync /usage{RESET}"
    except Exception as e:
        record_error(e, data)
        quota_display = f"{GRAY}Quota: sync /usage{RESET}"

    # 终端宽度自适应（核心防折行）：
    term_width = safe_int(data.get("terminal_width"), 0)
    if term_width <= 0:
        term_width = shutil.get_terminal_size((120, 24)).columns
    max_w = max(40, term_width - 1)

    parts = [
        dir_display,
        tokens_io,
        tok_display,
        ctx_ratio_display,
        time_display,
        tps_display,
        model_display,
        quota_display,
    ]
    line = SEP.join([p for p in parts if p])

    # 如果超长，逐级自适应裁切非核心信息以严防折行：
    if visible_len(line) > max_w and " · Week:" in quota_display:
        quota_display = quota_display.split(" · Week:")[0]
        parts[7] = quota_display
        line = SEP.join([p for p in parts if p])

    if visible_len(line) > max_w and " · reset" in quota_display:
        quota_display = quota_display.split(" · reset")[0]
        parts[7] = quota_display
        line = SEP.join([p for p in parts if p])

    if visible_len(line) > max_w:
        parts[5] = ""  # 省略 tps
        line = SEP.join([p for p in parts if p])

    if visible_len(line) > max_w:
        parts[1] = ""  # 省略 ↑0 ↓0
        line = SEP.join([p for p in parts if p])

    if visible_len(line) > max_w:
        # 简化模型名 (去掉括号)
        short_model = model_name.split("(")[0].strip()
        parts[6] = f"{YELLOW}{BOLD}{short_model}{RESET}"
        line = SEP.join([p for p in parts if p])

    return line


# ── Main loop ──────────────────────────────────────────────────────────────────
def _main_internal():
    if sys.stdin.isatty():
        state = read_json_file(STATUS_STATE_FILE)
        if state:
            sys.stdout.write(render(state))
            sys.stdout.flush()
        return

    decoder = json.JSONDecoder()
    buffer = ""
    last_data = None
    fd = sys.stdin.fileno()

    while True:
        try:
            r, _, _ = select.select([sys.stdin], [], [], 0.3)
            if r:
                chunk = os.read(fd, 65536).decode("utf-8", "replace")
                if not chunk:
                    break
                buffer += chunk
                while buffer:
                    stripped = buffer.lstrip()
                    if not stripped:
                        buffer = ""
                        break
                    try:
                        data, idx = decoder.raw_decode(stripped)
                        buffer = stripped[idx:]
                        if isinstance(data, dict):
                            write_status_state(data)
                            last_data = data
                    except json.JSONDecodeError:
                        break
            else:
                if os.getppid() == 1:
                    break
                if last_data:
                    break
        except (EOFError, KeyboardInterrupt):
            break
        except Exception as e:
            record_error(e, buffer)
            break

    # 严格单行输出，末尾不产生多余换行符
    if last_data:
        out_line = render(last_data)
    else:
        state = read_json_file(STATUS_STATE_FILE)
        if state:
            out_line = render(state)
        else:
            out_line = f"{GRAY}dir:workspace | ↑0 ↓0 | 0 tok | 0%/auto | -/- | - t/s | AGY | Quota: sync /usage{RESET}"

    clean_line = out_line.rstrip("\r\n")
    try:
        sys.stdout.write(clean_line)
        sys.stdout.flush()
    except (BrokenPipeError, IOError):
        pass


def main():
    try:
        _main_internal()
    except (BrokenPipeError, IOError):
        # 管道写入端/父进程提前关闭输出管道，属于标准 Unix CLI 行为，静默退出
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except Exception:
            pass
        sys.exit(0)
    except Exception as e:
        record_error(e, None)
        err_type = type(e).__name__
        try:
            print(f"{RED}⚠️ 状态栏故障 [{err_type}: {str(e)[:40]}]{RESET} · 详见 status-error.log", flush=True)
        except Exception:
            pass
        sys.exit(0)


if __name__ == "__main__":
    main()
