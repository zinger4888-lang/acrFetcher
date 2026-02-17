from __future__ import annotations

from enum import Enum


class StatusCode(str, Enum):
    WAITING = "WAITING"
    MONITORING = "MONITORING"
    POLL = "POLL"
    OPENING = "OPENING"
    SUCCESS = "SUCCESS"
    MISSED = "MISSED"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    PROXY_TGR = "PROXY_TGR"
    PROXY_WEBR = "PROXY_WEBR"
    ERROR = "ERROR"
    STOPPED = "STOPPED"
    LOGIN = "LOGIN"
    PAUSED = "PAUSED"
    SKIP = "SKIP"
    BADLINK = "BADLINK"
    NEWMSG = "NEWMSG"
    NOACCESS = "NOACCESS"
    JOINING = "JOINING"
    JOINED = "JOINED"
    JOINFAIL = "JOINFAIL"
    DELAY = "DELAY"


ALIASES: dict[str, StatusCode] = {
    "WAIT": StatusCode.WAITING,
    "WAITRESULT": StatusCode.WAITING,
    "IDLE": StatusCode.WAITING,
    "POLLING": StatusCode.POLL,
    "PING": StatusCode.POLL,
    "MIST": StatusCode.MISSED,
    "STOP": StatusCode.STOPPED,
    "PAUSE": StatusCode.PAUSED,
    "BLOCKED": StatusCode.BADLINK,
    "BLOCK": StatusCode.BADLINK,
    "NO_ACCESS": StatusCode.NOACCESS,
    "NOTMEMBER": StatusCode.NOACCESS,
    "NEW_MESSAGE": StatusCode.NEWMSG,
    "NEWMSG_EVENT": StatusCode.NEWMSG,
    "JOIN_FAILED": StatusCode.JOINFAIL,
}


def normalize_status(code: str) -> StatusCode | None:
    c = str(code or "").upper()
    if c in ALIASES:
        return ALIASES[c]
    try:
        return StatusCode[c]
    except Exception:
        return None


def status_label(code: str, detail: str = "") -> str:
    st = normalize_status(code)
    d = (detail or "").strip()
    if st == StatusCode.WAITING:
        return "⏳ WAITING…"
    if st == StatusCode.MONITORING:
        return "👀 MONITORING"
    if st == StatusCode.POLL:
        return "📡 POLL"
    if st == StatusCode.OPENING:
        return "🔗 OPENING"
    if st == StatusCode.SUCCESS:
        return "✅ SUCCESS"
    if st == StatusCode.MISSED:
        return "⏱ MISSED"
    if st == StatusCode.FAIL:
        return "✖ FAIL"
    if st == StatusCode.TIMEOUT:
        return "⚠️ TIMEOUT"
    if st == StatusCode.PROXY_TGR:
        return f"🧱 PROXY TGR {d}".strip() if d else "🧱 PROXY TGR"
    if st == StatusCode.PROXY_WEBR:
        return f"🧱 PROXY WEBR {d}".strip() if d else "🧱 PROXY WEBR"
    if st == StatusCode.ERROR:
        return "❌ ERROR"
    if st == StatusCode.STOPPED:
        return "⏹ STOPPED"
    if st == StatusCode.LOGIN:
        return "🔑 LOGIN"
    if st == StatusCode.PAUSED:
        return "⏸ PAUSED"
    if st == StatusCode.SKIP:
        return "⚠️ SKIP"
    if st == StatusCode.BADLINK:
        return "⛔ BLOCKED LINK"
    if st == StatusCode.NEWMSG:
        return "📩 NEW MSG"
    if st == StatusCode.NOACCESS:
        return "🙈 NOT IN CHANNEL"
    if st == StatusCode.JOINING:
        return "➕ JOINING"
    if st == StatusCode.JOINED:
        return "✅ JOINED"
    if st == StatusCode.JOINFAIL:
        return "⚠️ JOIN FAILED"
    if st == StatusCode.DELAY:
        return f"⏳ DELAY {d}".strip() if d else "⏳ DELAY"
    return str(code or "")
