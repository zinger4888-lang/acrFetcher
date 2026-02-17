from __future__ import annotations

import re
from typing import Optional


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s or "")


def pad_ansi(s: str, width: int) -> str:
    if width <= 0:
        return ""
    raw = s or ""
    vis = strip_ansi(raw)
    try:
        from wcwidth import wcswidth, wcwidth
    except Exception:
        wcswidth = None
        wcwidth = None

    def disp_w(txt: str) -> int:
        if not txt:
            return 0
        if wcswidth:
            w = wcswidth(txt)
            return w if w >= 0 else len(txt)
        return len(txt)

    def truncate_disp(txt: str, target: int) -> str:
        if target <= 0:
            return ""
        if disp_w(txt) <= target:
            return txt
        if target == 1:
            return "..."
        limit = target - 3
        out = []
        w = 0
        for ch in txt:
            cw = (wcwidth(ch) if wcwidth else 1)
            if cw < 0:
                cw = 0
            if w + cw > limit:
                break
            out.append(ch)
            w += cw
        return "".join(out) + "..."

    vis_w = disp_w(vis)
    if vis_w > width:
        return truncate_disp(vis, width)
    return raw + (" " * (width - vis_w))


def normalize_telegram_link(url: str) -> str:
    if not url:
        return url
    u = url.strip()
    ul = u.lower()
    if ul.startswith("t.me/") or ul.startswith("telegram.me/"):
        return "https://" + u
    if ul.startswith("//t.me/") or ul.startswith("//telegram.me/"):
        return "https:" + u
    return u


def is_telegram_link(url: str) -> bool:
    u = normalize_telegram_link(url).strip().lower()
    return (
        u.startswith("https://t.me/")
        or u.startswith("http://t.me/")
        or u.startswith("https://telegram.me/")
        or u.startswith("http://telegram.me/")
        or u.startswith("tg://")
    )


def parse_message_link(link: str) -> tuple[Optional[str], Optional[int]]:
    try:
        s = (link or "").strip()
        if not s:
            return None, None
        s = s.replace("http://", "https://")
        m = re.search(r"t\.me/c/(\d+)/(\d+)", s)
        if m:
            return "c/" + m.group(1), int(m.group(2))
        m = re.search(r"t\.me/([A-Za-z0-9_]+)/(\d+)", s)
        if m:
            return "@" + m.group(1), int(m.group(2))
    except Exception:
        pass
    return None, None


def parse_miniapp_direct_link(url: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    try:
        u = (url or "").strip()
        if not u:
            return None, None, None
        m = re.search(r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)/([A-Za-z0-9_]+)", u)
        if not m:
            return None, None, None
        bot = m.group(1)
        short = m.group(2)
        m2 = re.search(r"[?&]startapp=([^&]+)", u)
        start_param = m2.group(1) if m2 else None
        return bot, short, start_param
    except Exception:
        return None, None, None


def extract_ticket_info(text: str) -> str:
    t = text or ""
    dollars = re.findall(r"\$\s*\d+", t)
    dollars = [d.replace(" ", "") for d in dollars]
    gtd = []
    tnorm = re.sub(r"\s+", " ", t)
    for m in re.finditer(r"([^\.\n]{0,120}GTD[^\.\n]{0,20})", tnorm, flags=re.IGNORECASE):
        chunk = m.group(1)
        mm = re.search(r"(\d{1,3}(?:[\s,]\d{3})+|\d+\s*K|\d+K)\s*GTD", chunk, flags=re.IGNORECASE)
        if not mm:
            continue
        val = mm.group(1).replace(" ", "").replace(",", "")
        if val.lower().endswith("k"):
            gtd.append(val.upper() + " GTD")
        else:
            try:
                n = int(val)
                gtd.append(f"{n//1000}K GTD" if n >= 1000 and n % 1000 == 0 else f"{n} GTD")
            except Exception:
                gtd.append(val + " GTD")
        break
    parts: list[str] = []
    if dollars:
        parts.extend(dollars)
    if gtd:
        parts.append(gtd[0])
    if not parts:
        return "-"
    out: list[str] = []
    for p in parts:
        if p not in out:
            out.append(p)
    return " ".join(out)
