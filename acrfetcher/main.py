#!/usr/bin/env python3
import asyncio
import csv
import json
import logging
import os
import sys
import random
import re
import shutil
import time
from pathlib import Path
from typing import Optional, Tuple, Union

from telethon import TelegramClient, events
from telethon import functions, types
from telethon.errors.rpcerrorlist import UserNotParticipantError, ChannelPrivateError
import urllib.request
import urllib.parse
import ssl

from ui_theme import theme
# Telethon proxy support relies on PySocks.
# We use socks constants (e.g., socks.HTTP) to avoid ambiguity across Telethon versions.
try:
    import socks  # type: ignore
except Exception:
    socks = None


def safe_url(u: str) -> str:
    """Strip sensitive Telegram WebApp fragments from URLs in logs."""
    try:
        if not u:
            return ""
        u_no_frag = u.split('#', 1)[0]
        # keep only tgWebAppStartParam if present
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        p = urlparse(u_no_frag)
        qs = parse_qs(p.query)
        keep = {}
        if 'tgWebAppStartParam' in qs and qs['tgWebAppStartParam']:
            keep['tgWebAppStartParam'] = qs['tgWebAppStartParam'][0]
        query = urlencode(keep) if keep else ''
        return urlunparse((p.scheme, p.netloc, p.path, '', query, ''))
    except Exception:
        return (u or '').split('#', 1)[0]

APP_NAME = "acrFetcher"
APP_VERSION = "0.1.56"

_WEBHOOK_CFG = {}
_WARM_CACHE: dict[str, "WarmBrowserSession"] = {}
_SUPPRESS_PREFLIGHT_ONCE = False
_RUNTIME_PREFLIGHT_DONE = False
DEFAULT_CONFIG: dict = {
    "api_id": 33457801,
    "api_hash": "b64017f6fca7dac2d51786396e58cfdf",
    "channel": "",
    "launch_button_text": "Launch",
    "pre_open_delay_ms": "500",
    "duplicate_window_ms": 60000,
    "open_only_telegram_links": True,
    "force_open_in_telegram_app": True,
    "gotem": 0,
    "result_timeout_ms": 15000,
    "result_poll_ms": 500,
    "success_patterns": ["you got", "ticket"],
    "fail_patterns": ["this offer has expired", "keep an eye out for new offers"],
    "watch_mode": "new",
    "ui_mode": "ptk",
    "monitor_mode": "live+poll",
    "poll_interval_sec": 1,
    "keepalive_interval_sec": 1,
    "event_dedup_ttl_sec": 1800,
    "miniapp_link_fallback": True,
    "headless_mode": False,
    "browser_profile_dir": "",
    "browser_first_login_headed": True,
    "accounts_csv": "",
    "webhook_enabled": True,
    "webhook_bot_token": "",
    "webhook_chat_id": "",
    "webhook_on_error": False,
    "storage_state_mode": "off",
    "storage_state_path": "",
}



class WarmBrowserSession:
    """Per-account Playwright session that stays alive between tasks.

    Primary objective (per user): make the browser OPEN fast and avoid slowing
    down the page load by waiting for heavy load states.

    Storage-state support (server-friendly):
      - mode "capture": export cookies/localStorage to a JSON file after you login once.
      - mode "use": import that JSON file on startup (cross-platform).

    Notes:
      - We keep the default (mode "off") behavior identical to older builds.
      - For mode "use", we run a non-persistent context so we can load storage_state.
    """

    def __init__(
        self,
        *,
        profile_dir: Path,
        proxy: Optional[dict],
        headless: bool,
        wait_until: str = "commit",
        storage_state_mode: str = "off",
        storage_state_path: Optional[Path] = None,
    ):
        self.profile_dir = profile_dir
        self.proxy = proxy if proxy else None
        self.headless = bool(headless)
        self.wait_until = (wait_until or "commit").strip() or "commit"
        self.storage_state_mode = (storage_state_mode or "off").strip().lower()
        self.storage_state_path = storage_state_path

        self._pw = None
        self._browser = None  # only used for non-persistent contexts
        self._ctx = None
        self._page = None
        self._lock = asyncio.Lock()
        self._capture_done = False

    def _resolved_storage_path(self) -> Optional[Path]:
        if self.storage_state_mode in ("off", ""):
            return None
        p = self.storage_state_path
        if p is None:
            return None
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return p

    async def start(self) -> None:
        async with self._lock:
            if self._pw and self._ctx and self._page:
                return
            try:
                self.profile_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()

            # Launch flags: aim for faster first paint / less background noise.
            # (Safe defaults; do NOT change any user-visible behavior.)
            launch_args = [
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-sync",
                "--disable-features=TranslateUI",
            ]

            mode = self.storage_state_mode
            # "use" mode: we need a regular context to import storage_state.
            if mode == "use":
                self._browser = await self._pw.chromium.launch(
                    headless=self.headless,
                    proxy=self.proxy,
                    args=launch_args,
                )
                ctx_kwargs = {}
                sp = self._resolved_storage_path()
                if sp is not None and sp.exists():
                    ctx_kwargs["storage_state"] = str(sp)
                self._ctx = await self._browser.new_context(**ctx_kwargs)
            else:
                # Default behavior: persistent profile folder (keeps you logged in).
                self._ctx = await self._pw.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=self.headless,
                    proxy=self.proxy,
                    args=launch_args,
                )

            self._page = await self._ctx.new_page()
            # Keep a lightweight page open so the window exists immediately on first navigation.
            try:
                await self._page.goto("about:blank", wait_until="commit", timeout=3000)
            except Exception:
                pass

    async def maybe_capture_storage_state(self) -> None:
        """Capture storage_state once (interactive) for cross-platform server use."""
        if self.storage_state_mode != "capture":
            return
        if self._capture_done:
            return
        sp = self._resolved_storage_path()
        if sp is None:
            return

        try:
            status_info(f"🔑 LOGIN CAPTURE: finish login in the browser, then press ENTER here to save → {sp.name}")
        except Exception:
            print(f"LOGIN CAPTURE: finish login in the browser, then press ENTER to save → {sp}")

        try:
            await ainput("")
        except Exception:
            return

        try:
            await self._ctx.storage_state(path=str(sp))
            self._capture_done = True
            try:
                status_success(f"💾 Saved storage_state → {sp}")
            except Exception:
                print(f"Saved storage_state → {sp}")
        except Exception as e:
            try:
                status_error(f"storage_state save failed: {type(e).__name__}: {e}")
            except Exception:
                print(f"storage_state save failed: {type(e).__name__}: {e}")

    async def goto(self, url: str, *, timeout_ms: int = 15000):
        """Navigate quickly. Returns the page."""
        await self.start()
        try:
            await self._page.goto(url, wait_until=self.wait_until, timeout=int(timeout_ms))
        except Exception as e:
            # If the browser was closed/crashed, restart once.
            msg = str(e).lower()
            if "target closed" in msg or "browser has been closed" in msg or "context closed" in msg:
                await self.close()
                await self.start()
                try:
                    await self._page.goto(url, wait_until=self.wait_until, timeout=int(timeout_ms))
                except Exception:
                    pass
            else:
                # We still want the window/tab to open ASAP; swallow nav errors here.
                pass

        # Optional interactive capture (headed mode; you press ENTER in terminal).
        try:
            await self.maybe_capture_storage_state()
        except Exception:
            pass

        return self._page

    async def close(self) -> None:
        async with self._lock:
            try:
                if self._ctx:
                    await self._ctx.close()
            except Exception:
                pass
            try:
                if self._browser:
                    await self._browser.close()
            except Exception:
                pass
            try:
                if self._pw:
                    await self._pw.stop()
            except Exception:
                pass
            self._pw = None
            self._browser = None
            self._ctx = None
            self._page = None


def _default_data_dir() -> Path:
    """Platform default data dir.

    macOS: ~/Library/Application Support/acrFetcher
    Linux: ~/.local/share/acrFetcher
    Windows: %APPDATA%\acrFetcher
    """
    try:
        plat = sys.platform.lower()
    except Exception:
        plat = ""

    if plat.startswith("darwin"):
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if plat.startswith("linux"):
        return Path.home() / ".local" / "share" / APP_NAME
    if plat.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    # fallback
    return Path.home() / ".local" / "share" / APP_NAME


def resolve_data_dir() -> Path:
    """Resolve the runtime data dir.

    Priority:
      1) env ACRFETCHER_DATA_DIR
      2) platform default
    """
    env_raw = str(os.environ.get("ACRFETCHER_DATA_DIR", "") or "").strip()
    if env_raw:
        p = Path(env_raw).expanduser()
        if not p.is_absolute():
            # treat relative as cwd-relative (nice for servers)
            p = (Path.cwd() / p).resolve()
        return p

    return _default_data_dir()

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = resolve_data_dir()
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    # Sandbox/permission fallback (tests/CI): keep app runnable with local data dir.
    DATA_DIR = (APP_ROOT / ".acr_data").resolve()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"
ASSETS_DIR = APP_ROOT / "assets"
BUNDLED_ART_PATH = ASSETS_DIR / "menu_art.txt"
LEGACY_ART_PATH = APP_ROOT / "menu_art.txt"
DEFAULT_ACCOUNTS_PATH = DATA_DIR / "accounts.csv"
LEGACY_ACCOUNTS_PATH = APP_ROOT / "accounts.csv"

# Full-screen UI (prompt_toolkit/classic redraw) must not be corrupted by
# background stdout/stderr noise. We keep all library logging in DATA_DIR/logs.
_UI_QUIET = False
_RUNTIME_LOG_PATH: Optional[Path] = None

def _configure_file_logging() -> None:
    """Route Python logging to DATA_DIR/logs/runtime.log and remove console handlers.

    This prevents Telethon/proxy connection retry logs from printing under the TUI.
    """
    global _RUNTIME_LOG_PATH
    try:
        logs_dir = DATA_DIR / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        _RUNTIME_LOG_PATH = logs_dir / "runtime.log"

        handler = logging.FileHandler(_RUNTIME_LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

        root = logging.getLogger()
        # Remove any console handlers (StreamHandler to stdout/stderr).
        for h in list(root.handlers):
            root.removeHandler(h)
        root.addHandler(handler)
        root.setLevel(logging.INFO)

        # Some libs attach their own StreamHandlers; remove those too.
        try:
            for _lg in list(logging.root.manager.loggerDict.values()):
                if isinstance(_lg, logging.Logger):
                    for h in list(_lg.handlers):
                        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                            try:
                                _lg.removeHandler(h)
                            except Exception:
                                pass
        except Exception:
            pass

        # Ensure Telethon messages propagate to root (file) and never to console.
        for name in (
            "telethon",
            "telethon.network",
            "telethon.network.connection",
            "telethon.network.connection.connection",
            "python_socks",
            "socks",
        ):
            try:
                lg = logging.getLogger(name)
                lg.propagate = True
            except Exception:
                pass
    except Exception:
        pass

_configure_file_logging()

# Sessions live here (kept compatible with older builds)
SESSION_BASE = DATA_DIR / APP_NAME

# Backward-compat alias (some parts may call session_path())
def session_path():
    return str(SESSION_BASE)

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
ORANGE = "\033[38;5;208m"
CYAN = "\033[36m"
BLUE = "\033[34m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

_FORCE_COLOR = (os.getenv("ACRFETCHER_FORCE_COLOR") or "").strip().lower() in ("1", "true", "yes", "on")
_NO_COLOR = (not _FORCE_COLOR) and (bool(os.getenv("NO_COLOR")) or (os.getenv("TERM", "").lower() == "dumb"))
if _NO_COLOR:
    GREEN = RED = YELLOW = ORANGE = CYAN = BLUE = DIM = BOLD = RESET = ""

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def _strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s or "")

def _pad_ansi(s: str, width: int) -> str:
    """Pad/truncate a string that may contain ANSI color codes, using display width.

    Notes:
    - ANSI sequences have width 0.
    - Emoji / wide chars may have width 2 (terminal columns).
    """
    if width <= 0:
        return ""
    raw = s or ""
    vis = _strip_ansi(raw)

    # Display width helper (wcwidth if available, else len as fallback)
    try:
        from wcwidth import wcswidth, wcwidth
    except Exception:
        wcswidth = None
        wcwidth = None

    def _disp_w(txt: str) -> int:
        if not txt:
            return 0
        if wcswidth:
            w = wcswidth(txt)
            return w if w >= 0 else len(txt)
        return len(txt)

    def _truncate_disp(txt: str, target: int) -> str:
        """Truncate plain text to target display width (no ANSI)."""
        if target <= 0:
            return ""
        if _disp_w(txt) <= target:
            return txt
        if target == 1:
            return "…"
        limit = target - 1  # room for ellipsis
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
        return "".join(out) + "…"

    vis_w = _disp_w(vis)
    if vis_w > width:
        # Truncate visible chars. To keep logic simple, drop ANSI on truncation.
        return _truncate_disp(vis, width)

    return raw + (" " * (width - vis_w))


def zebraRow(idx: int, text: str) -> str:
    return theme.dim(text) if (idx % 2 == 1) else text


def dimBorder(text: str, bright: bool = False) -> str:
    return theme.border(text, bright=bright)


def _truncate_right(s: str, width: int) -> str:
    s = s or ""
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    if width == 1:
        return "…"
    return s[: width - 1] + "…"

def _truncate_middle_email(s: str, width: int) -> str:
    """Middle-truncate emails so you keep both username start and domain."""
    s = s or ""
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    if "@" not in s or width < 5:
        return _truncate_right(s, width)
    user, domain = s.split("@", 1)
    # leave at least 2 chars of user and 2 of domain
    # Reserve 1 for '@' and 1 for ellipsis
    min_user = 2
    min_dom = 2
    if width < (min_user + min_dom + 2):  # '@' + '…'
        return _truncate_right(s, width)
    # Try keep domain end
    dom_keep = min(max(min_dom, int(width * 0.45)), len(domain))
    user_keep = width - (dom_keep + 2)  # '@' + '…'
    user_keep = min(max(min_user, user_keep), len(user))
    dom_keep = width - (user_keep + 2)
    return f"{user[:user_keep]}…@{domain[-dom_keep:]}"

def _fit_cell(text: str, width: int, kind: str = "right") -> str:
    """Fit plain (non-ANSI) text into width with ellipsis."""
    if kind == "email":
        return _truncate_middle_email(text, width)
    return _truncate_right(text, width)


def formatProxyMasked(raw: str, width: Optional[int] = None) -> str:
    s = (raw or "").strip()
    if not s or s in ("-", "—"):
        return theme.gray_text("—")

    def _mask_secret(val: str) -> str:
        return "******" if val else ""

    plain = s
    if "://" in s:
        try:
            p = urllib.parse.urlparse(s)
            scheme = (p.scheme + "://") if p.scheme else ""
            host = p.hostname or ""
            port = str(p.port) if p.port else ""
            user = p.username or ""
            pwd = p.password or ""
            auth = ""
            if user or pwd:
                auth = f"{user}:{_mask_secret(pwd)}@" if (user or pwd) else ""
                if user and not pwd:
                    auth = f"{user}@"
            plain = f"{scheme}{auth}{host}{(':' + port) if port else ''}"
        except Exception:
            plain = s
    else:
        parts = s.split(":")
        if len(parts) >= 4:
            host, port, user = parts[0], parts[1], parts[2]
            pwd = ":".join(parts[3:])
            plain = f"{host}:{port}:{user}:{_mask_secret(pwd)}"
        elif len(parts) == 2:
            host, port = parts[0], parts[1]
            plain = f"{host}:{port}"
        else:
            plain = s

    if width is not None:
        plain = _fit_cell(plain, width)

    # Colorize parts based on the truncated visible proxy.
    if "://" in plain:
        m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*://)(.*)$", plain)
        if not m:
            return theme.cyan_text(plain)
        scheme, rest = m.group(1), m.group(2)
        auth = ""
        hostport = rest
        if "@" in rest:
            auth, hostport = rest.split("@", 1)
        out = theme.gray_text(scheme)
        if auth:
            if ":" in auth:
                user, pwd = auth.split(":", 1)
                out += theme.white_text(user)
                out += theme.gray_text(":")
                out += theme.gray_text("******" if pwd else "")
            else:
                out += theme.white_text(auth)
            out += theme.gray_text("@")
        host = hostport
        port = ""
        if ":" in hostport:
            host, port = hostport.rsplit(":", 1)
        out += theme.cyan_text(host) if host else ""
        if port:
            # Ports are not "warn/time" anymore (amber is reserved for timeouts/delays).
            out += theme.gray_text(":") + theme.purple_text(port)
        return out

    parts = plain.split(":")
    if len(parts) >= 4:
        host, port, user = parts[0], parts[1], parts[2]
        pwd = ":".join(parts[3:])
        out = theme.cyan_text(host)
        out += theme.gray_text(":") + theme.purple_text(port)
        out += theme.gray_text(":") + theme.white_text(user)
        out += theme.gray_text(":") + theme.gray_text("******" if pwd else "")
        return out
    if len(parts) == 2:
        host, port = parts[0], parts[1]
        return theme.cyan_text(host) + theme.gray_text(":") + theme.purple_text(port)
    return theme.cyan_text(plain)

# UI animation phase for 👀 MONITORING... (advanced by the render loop)
_MONITOR_PHASE = 0

# UI overlay for showing which account is doing POLL right now (truthful, UI-only)
_POLL_OVERLAY_LABEL: str | None = None
_POLL_OVERLAY_UNTIL: float = 0.0

def _monitoring_text() -> str:
    # Animated dots are advanced by the render loop tick (NOT wall clock).
    # This guarantees the visual sequence: 0 → 1 → 2 → 3 → 0 even if the
    # redraw interval is not a multiple of 0.5s.
    phase = globals().get("_MONITOR_PHASE", 0) % 4
    return "👀 MONITORING" + ("." * phase)

def colorizeStatus(code: str, detail: str = "") -> str:
    c = (code or "").upper()
    d = (detail or "").strip()
    if c in ("WAITING", "IDLE", "WAIT", "WAITRESULT"):
        return theme.gray_text("⏳ WAITING…")
    if c in ("MONITORING",):
        # Keep MONITORING distinct from link/active cyan.
        return theme.monitor(_monitoring_text())
    if c in ("GOT", "GOTLINK"):
        return theme.lime_text("🚀 GOT THE LINK")
    if c in ("OPENING",):
        return theme.amber_text("🔗 OPENING")
    if c in ("JOINING",):
        return theme.amber_text("➕ JOINING")
    if c in ("JOINED",):
        return theme.lime_text("✅ JOINED")
    if c in ("JOINFAIL", "JOIN_FAILED"):
        return theme.red_text("⚠️ JOIN FAILED")
    if c in ("NOACCESS", "NO_ACCESS", "NOTMEMBER"):
        return theme.gray_text("🙈 NOT IN CHANNEL")
    if c in ("NEWMSG", "NEW_MESSAGE", "NEWMSG_EVENT"):
        return theme.cyan_text("📩 NEW MSG")
    if c in ("POLL", "PING", "POLLING"):
        return theme.pink_text("📡 POLL")
    if c in ("DELAY",):
        d = (detail or "").strip()
        return theme.amber_text(f"⏳ DELAY {d}".strip() if d else "⏳ DELAY")
    if c in ("SUCCESS", "DONE"):
        return theme.lime_text("✅ SUCCESS")
    if c in ("MISSED", "MIST"):
        # MISSED = page loaded but reward is unavailable (already claimed/expired/etc.).
        return theme.amber_text("⏱ MISSED")
    if c in ("FAIL",):
        # FAIL = other negative outcome (patterns matched).
        return theme.amber_text("✖ FAIL")
    if c in ("PROXY_TGR",):
        return theme.red_text(f"🧱 PROXY TGR {d}".strip() if d else "🧱 PROXY TGR")
    if c in ("PROXY_WEBR",):
        return theme.red_text(f"🧱 PROXY WEBR {d}".strip() if d else "🧱 PROXY WEBR")
    if c in ("ERROR",):
        return theme.red_text("❌ ERROR")
    if c in ("TIMEOUT",):
        return theme.amber_text("⚠️ TIMEOUT")
    if c in ("LOGIN",):
        return theme.amber_text("🔑 LOGIN")
    if c in ("PAUSED", "PAUSE"):
        return theme.amber_text("⏸ PAUSED")
    if c in ("STOPPED", "STOP"):
        return theme.red_text("⏹ STOPPED")
    if c in ("SKIP",):
        return theme.amber_text("⚠️ SKIP")
    if c in ("BADLINK", "BLOCKED", "BLOCK"):
        return theme.red_text("⛔ BLOCKED LINK")
    return c

def _status_cell(code: str, detail: str = "") -> str:
    """Map internal status codes to the UI emoji+color style."""
    return colorizeStatus(code, detail)

def extract_ticket_info(text: str) -> str:
    """Best-effort ticket/GTD extraction for the watch table (UI only)."""
    t = (text or "")
    # $ amounts like "$50" / "$ 50"
    dollars = re.findall(r"\$\s*\d+", t)
    dollars = [d.replace(" ", "") for d in dollars]
    # GTD like "50K GTD" / "50 000 GTD" / "50000 GTD"
    gtd = []
    tnorm = re.sub(r"\s+", " ", t)
    for m in re.finditer(r"([^\.\n]{0,120}GTD[^\.\n]{0,20})", tnorm, flags=re.IGNORECASE):
        chunk = m.group(1)
        mm = re.search(r"(\d{1,3}(?:[\s,]\d{3})+|\d+\s*K|\d+K)\s*GTD", chunk, flags=re.IGNORECASE)
        if mm:
            val = mm.group(1).replace(" ", "").replace(",", "")
            if val.lower().endswith("k"):
                gtd.append(val.upper() + " GTD")
            else:
                try:
                    n = int(val)
                    if n >= 1000 and n % 1000 == 0:
                        gtd.append(f"{n//1000}K GTD")
                    else:
                        gtd.append(f"{n} GTD")
                except Exception:
                    gtd.append(val + " GTD")
            break
    parts: list[str] = []
    if dollars:
        parts.extend(dollars)
    if gtd:
        parts.append(gtd[0])
    if not parts:
        return "—"
    # de-dup
    out: list[str] = []
    for p in parts:
        if p not in out:
            out.append(p)
    return " ".join(out)

def clear():
    os.system("clear")

def status_success_msg(msg: str):
    if globals().get("_UI_QUIET", False):
        try:
            logging.getLogger("ui").info("SUCCESS: %s", msg)
        except Exception:
            pass
        return
    print(f"{GREEN}✅ SUCCESS: {msg}{RESET}", flush=True)
    try:
        if webhook_enabled():
            webhook_send(f"✅ SUCCESS: {msg}")
    except Exception:
        pass
def status_error(msg: str):
    if globals().get("_UI_QUIET", False):
        try:
            logging.getLogger("ui").error("%s", msg)
        except Exception:
            pass
        return
    print(f"{RED}❌ ERROR: {msg}{RESET}", flush=True)
    try:
        if webhook_enabled() and bool(_WEBHOOK_CFG.get("webhook_on_error", False)):
            webhook_send(f"❌ ERROR: {msg}")
    except Exception:
        pass
def status_warn(msg: str):
    if globals().get("_UI_QUIET", False):
        try:
            logging.getLogger("ui").warning("%s", msg)
        except Exception:
            pass
        return
    print(f"{YELLOW}⚠️ {msg}{RESET}", flush=True)
def status_info(msg: str):
    if globals().get("_UI_QUIET", False):
        try:
            logging.getLogger("ui").info("%s", msg)
        except Exception:
            pass
        return
    print(msg, flush=True)

DelaySpec = Union[int, Tuple[int, int]]

def parse_delay_spec(s: str) -> DelaySpec:
    s = str(s).strip()
    if not s:
        return 0
    if re.fullmatch(r"\d+", s):
        return int(s)
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = (a, b) if a <= b else (b, a)
        return (lo, hi)
    raise ValueError("Delay must be ms like 5000 or range like 3000-8000")

def choose_delay_ms(spec: DelaySpec) -> int:
    if isinstance(spec, int):
        return spec
    lo, hi = spec
    return lo if lo == hi else random.randint(lo, hi)

def parse_http_proxy(spec: str) -> Optional[dict]:
    """Parse proxy in formats:
    - ip:port
    - ip:port:user:pass
    Returns Playwright proxy dict or None.
    """
    s = (spec or "").strip()
    if not s:
        return None
    parts = s.split(":")
    if len(parts) < 2:
        return None
    host = parts[0].strip()
    port = parts[1].strip()
    if not host or not port.isdigit():
        return None
    server = f"http://{host}:{port}"
    if len(parts) >= 4:
        user = parts[2]
        pw = ":".join(parts[3:])
        return {"server": server, "username": user, "password": pw}
    return {"server": server}

def load_accounts_csv(csv_path: Path) -> list[dict]:
    """Load accounts.csv with columns: phone,email,proxy."""
    if csv_path.is_dir():
        csv_path = csv_path / "accounts.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"accounts.csv not found: {csv_path}")
    rows: list[dict] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            phone = (r.get("phone") or r.get("Phone") or "").strip()
            email = (r.get("email") or r.get("Email") or "").strip()
            proxy_raw = (r.get("proxy") or r.get("Proxy") or "").strip()
            if not phone:
                continue
            rows.append({
                "phone": phone,
                "email": email or phone,
                "proxy_raw": proxy_raw,
                "proxy": parse_http_proxy(proxy_raw),
                "tg_proxy": parse_telethon_http_proxy(proxy_raw),
            })
    if not rows:
        raise ValueError("accounts.csv has no valid rows (need at least one with phone).")
    return rows

def acct_label(account: dict) -> str:
    return str(account.get("email") or account.get("phone") or "account")

def normalize_telegram_link(url: str) -> str:
    """Normalize common Telegram link forms so they can be validated and opened.

    Telegram links sometimes appear without a scheme, e.g. 't.me/xyz'.
    Playwright/Chromium expects a full URL, so we upgrade these to https://.
    """
    if not url:
        return url
    u = url.strip()
    ul = u.lower()
    # Bare domain without scheme
    if ul.startswith("t.me/") or ul.startswith("telegram.me/"):
        return "https://" + u
    # Protocol-relative (rare)
    if ul.startswith("//t.me/") or ul.startswith("//telegram.me/"):
        return "https:" + u
    return u

def is_telegram_link(url: str) -> bool:
    u = normalize_telegram_link(url).strip().lower()
    return (
        u.startswith("https://t.me/") or u.startswith("http://t.me/") or
        u.startswith("https://telegram.me/") or u.startswith("http://telegram.me/") or
        u.startswith("tg://")
    )

def ensure_config():
    if (not CONFIG_PATH.exists()) or CONFIG_PATH.stat().st_size == 0:
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")

def _migrate_webhook_config(cfg: dict) -> tuple[dict, bool]:
    """
    Back-compat: older builds used push_* keys. This build uses webhook_* keys.
    If push_* keys exist, we copy to webhook_* (if missing) and then drop push_* to avoid duplicates.
    """
    changed = False
    if not isinstance(cfg, dict):
        return {}, True
    # map old -> new
    mapping = {
        "push_enabled": "webhook_enabled",
        "push_bot_token": "webhook_bot_token",
        "push_channel_invite": "webhook_channel_invite",
        "push_chat_id": "webhook_chat_id",
        "push_on_error": "webhook_on_error",
        "push_updates_offset": "webhook_updates_offset",
    }
    for old, new in mapping.items():
        if old in cfg and new not in cfg:
            cfg[new] = cfg.get(old)
            changed = True
    # drop old keys to keep config clean
    for old in list(mapping.keys()):
        if old in cfg:
            cfg.pop(old, None)
            changed = True
    return cfg, changed


def load_config() -> dict:
    ensure_config()
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg, changed = _migrate_webhook_config(cfg)
    if changed:
        try:
            save_config(cfg)
        except Exception:
            pass
    return cfg


def webhook_enabled() -> bool:
    return bool(_WEBHOOK_CFG.get("webhook_enabled", False)) and bool(str(_WEBHOOK_CFG.get("webhook_bot_token", "")).strip())


def http_ssl_context():
    """Return SSL context that trusts certifi bundle if available (Homebrew Python often needs this)."""
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return None

def webhook_api_base() -> str:
    token = str(_WEBHOOK_CFG.get("webhook_bot_token", "")).strip()
    return f"https://api.telegram.org/bot{token}"

def webhook_send(text: str) -> tuple[bool, str]:
    try:
        chat_id = _WEBHOOK_CFG.get("webhook_chat_id", "")
        if chat_id in (None, ""):
            return (False, "webhook_chat_id is empty (run Webhook setup)")
        url = webhook_api_base() + "/sendMessage"
        payload = {
            "chat_id": str(chat_id),
            "text": text,
            "disable_web_page_preview": True
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10, context=http_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        try:
            j = json.loads(raw or "{}")
        except Exception:
            j = {}
        if j.get("ok") is True:
            return (True, "")
        return (False, str(j.get("description") or raw)[:200])
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")


async def webhook_send_async(text: str) -> tuple[bool, str]:
    """Async wrapper to avoid blocking the event loop on Bot API HTTP calls."""
    try:
        return await asyncio.to_thread(webhook_send, text)
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")

def webhook_delete_webhook(drop_pending_updates: bool = True) -> tuple[bool, str]:
    """Ensure getUpdates works: Bot API getUpdates won't return data if a webhook is set."""
    try:
        if not str(_WEBHOOK_CFG.get("webhook_bot_token", "")).strip():
            return (False, "missing bot token")
        q = {}
        if drop_pending_updates:
            q["drop_pending_updates"] = "true"
        url = webhook_api_base() + "/deleteWebhook"
        if q:
            url += "?" + urllib.parse.urlencode(q)
        with urllib.request.urlopen(url, timeout=10, context=http_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
        if data.get("ok"):
            return (True, "")
        return (False, (data.get("description") or "deleteWebhook failed")[:200])
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")


def webhook_detect_chat_id(seconds: int = 20) -> tuple[bool, str]:
    """Detect chat_id via getUpdates.

    Notes:
      - getUpdates does NOT work while a webhook is set on the bot.
      - We call deleteWebhook(drop_pending_updates=true) before polling.
      - Bot must be in the channel/group and you must make at least 1 fresh post.
    """
    try:
        if not webhook_enabled():
            return (False, "webhook not enabled / missing token")

        # Ensure long-polling works
        ok, err = webhook_delete_webhook(drop_pending_updates=True)
        if not ok:
            # Still continue; but return a better hint.
            return (False, f"deleteWebhook failed: {err}")

        deadline = time.time() + max(3, int(seconds))
        offset = int(_WEBHOOK_CFG.get("webhook_updates_offset", 0) or 0)
        found = None

        while time.time() < deadline:
            q = {
                "timeout": 0,
                "limit": 100,
                "allowed_updates": json.dumps(["message", "channel_post"])
            }
            if offset:
                q["offset"] = offset

            url = webhook_api_base() + "/getUpdates?" + urllib.parse.urlencode(q)
            with urllib.request.urlopen(url, timeout=10, context=http_ssl_context()) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")

            if not data.get("ok"):
                desc = data.get("description") or "getUpdates failed"
                return (False, str(desc)[:200])

            results = data.get("result", []) or []
            if results:
                offset = results[-1].get("update_id", offset) + 1

            # Try most recent updates first
            for upd in reversed(results):
                chat = None
                if isinstance(upd, dict):
                    if "channel_post" in upd and isinstance(upd["channel_post"], dict):
                        chat = upd["channel_post"].get("chat")
                    elif "message" in upd and isinstance(upd["message"], dict):
                        chat = upd["message"].get("chat")

                if isinstance(chat, dict) and "id" in chat:
                    found = chat["id"]
                    break

            if found is not None:
                break

            time.sleep(1)

        if found is None:
            return (False, "no updates found yet. Post in the channel/group then try again.")

        _WEBHOOK_CFG["webhook_chat_id"] = found
        _WEBHOOK_CFG["webhook_updates_offset"] = offset
        try:
            CONFIG_PATH.write_text(json.dumps(_WEBHOOK_CFG, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return (True, f"detected chat_id={found}")
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")




def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def load_art() -> str:
    for pth in (BUNDLED_ART_PATH, LEGACY_ART_PATH):
        try:
            if pth.exists():
                return pth.read_text(encoding="utf-8")
        except Exception:
            continue
    return ""


def resolve_accounts_csv_path(csv_raw: str) -> Path:
    raw = str(csv_raw or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        return p
    if DEFAULT_ACCOUNTS_PATH.exists():
        return DEFAULT_ACCOUNTS_PATH
    if LEGACY_ACCOUNTS_PATH.exists():
        return LEGACY_ACCOUNTS_PATH
    return DEFAULT_ACCOUNTS_PATH

def slot_bounds(line: str) -> Optional[Tuple[int, int]]:
    """
    For your template, a slot line looks like:
      "  [[[ : <CONTENT> : [[[[[[...."
    We find the first "[[[" and then the next " : " after it.
    Content starts after "  [[[ : " => i + 6. Content ends at the delimiter position.
    """
    i = line.find("[[[")
    if i < 0:
        return None
    j = line.find(" : ", i + 4)  # delimiter after content
    if j < 0:
        return None
    start = i + 6
    end = j
    if end <= start:
        return None
    return (start, end)

def put_in_slot(line: str, text: str) -> str:
    b = slot_bounds(line)
    if not b:
        return line
    start, end = b
    width = end - start
    return line[:start] + _pad_ansi(text, width) + line[end:]

def render_menu(cfg: dict) -> str:
    art = load_art()
    if not art.strip():
        return f"{APP_NAME} v{APP_VERSION}"

    title = f"{theme.pink_text(APP_NAME)} {theme.purple_text(f'v{APP_VERSION}')}"
    subtitle = "FROM - Phoeni><"

    # REAL values from config (Application Support)
    channel = str(cfg.get("channel", ""))
    pre = str(cfg.get("pre_open_delay_ms", ""))
    # cooldown removed in 0.1.20
    got = str(cfg.get("gotem", 0))

    def _center_ansi(s: str, width: int) -> str:
        vis = _strip_ansi(s)
        pad = max(0, int(width) - len(vis))
        left = pad // 2
        right = pad - left
        return (" " * left) + s + (" " * right)

    def _info_line(kind: str, width: int) -> str:
        if kind == "channel":
            val = channel or "—"
            prefix = f"{theme.gray_text('Channel:')} "
            val_color = theme.cyan_text
        elif kind == "pre":
            val = pre or "—"
            prefix = f"{theme.gray_text('Pre-delay:')} "
            val_color = theme.amber_text
        else:
            val = got
            prefix = f'{theme.gray_text("Got\'em:")} '
            val_color = theme.lime_text

        # Fit value into remaining width so we don't trigger _pad_ansi truncation
        # (which strips ANSI codes).
        prefix_vis = _strip_ansi(prefix)
        rem = max(0, int(width) - len(prefix_vis))
        val_fit = _truncate_right(str(val), rem)
        return f"{prefix}{val_color(val_fit)}"

    info_kinds = ["channel", "pre", "got"]

    out = []
    info_idx = 0
    inserted_blank = False

    art_lines = art.splitlines()

    _frame_chars = set(["[", "]", "_", "-", "|", "\\", "/", "{", "}", "(", ")", ":", "`", "'"])

    def _ascii_style_for_char(ch: str, scanline: bool) -> tuple[str, str] | None:
        if ch == " ":
            return None
        _ = scanline
        if ch in _frame_chars:
            return ("frame", "")
        # Solid glyph style for logo body (no translucent/noise rendering).
        return ("logo", "")

    def _color_ascii_line(raw_line: str, scanline: bool) -> str:
        # Run-length encode ANSI to avoid terminal reflow/jitter from per-char SGR spam.
        out: list[str] = []
        buf: list[str] = []
        cur_style: tuple[str, str] | None = None

        def _flush():
            nonlocal buf, cur_style
            if not buf:
                return
            s = "".join(buf)
            if cur_style is None:
                out.append(s)
            else:
                kind, payload = cur_style
                if kind == "logo":
                    out.append(theme.fg(theme.accent_2, s, bold=True))
                elif kind == "frame":
                    out.append(theme.pink_text(s))
                elif kind == "text":
                    out.append(theme.white_text(s))
                else:
                    out.append(theme.fg(payload, s))
            buf = []

        i = 0
        while i < len(raw_line):
            if raw_line.startswith("Gold", i):
                _flush()
                out.append(theme.amber_text("Gold"))
                cur_style = None
                i += 4
                continue

            ch = raw_line[i]
            style = _ascii_style_for_char(ch, scanline)
            if style != cur_style:
                _flush()
                cur_style = style
            buf.append(ch)
            i += 1
        _flush()
        return "".join(out)

    def _color_art_line(raw_line: str, slot_text: Optional[str], scanline: bool) -> str:
        b = slot_bounds(raw_line)
        if b:
            start, end = b
            left = raw_line[:start]
            right = raw_line[end:]
            inner_raw = raw_line[start:end]
            inner = slot_text if slot_text is not None else _color_ascii_line(inner_raw, scanline)
            left_col = _color_ascii_line(left, scanline)
            right_col = _color_ascii_line(right, scanline)
            return f"{left_col}{inner}{right_col}"
        return _color_ascii_line(raw_line, scanline)

    for idx, ln in enumerate(art_lines):
        b = slot_bounds(ln)
        if not b:
            scanline = False
            out.append(_color_art_line(ln, None, scanline))
            continue

        start, end = b
        inner = ln[start:end]
        inner_strip = inner.strip()
        width = end - start
        scanline = False

        if "HAPPY ST." in inner and "PATRICK" in inner:
            slot_txt = _center_ansi(title, width)
            out.append(_color_art_line(ln, slot_txt, scanline))
            continue
        if "FROM" in inner and "Phoeni" in inner:
            slot_txt = _center_ansi(theme.white_text(subtitle), width)
            out.append(_color_art_line(ln, slot_txt, scanline))
            continue

        if inner_strip == "":
            if not inserted_blank:
                # one empty line after subtitle
                slot_txt = ""
                out.append(_color_art_line(ln, slot_txt, scanline))
                inserted_blank = True
                continue
            if info_idx < len(info_kinds):
                slot_txt = _info_line(info_kinds[info_idx], width)
                out.append(_color_art_line(ln, slot_txt, scanline))
                info_idx += 1
                continue

        out.append(_color_art_line(ln, None, scanline))

    return "\n".join(out)


def is_miniapp_link(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    if "t.me/" not in u and "telegram.me/" not in u:
        return False
    # Common mini-app markers:
    # - startapp= (most common for web apps)
    # - start= (some bots use classic deep-linking)
    if ("startapp=" in u) or ("start=" in u):
        return True
    # Also allow bot/path style links (t.me/<bot>/<app>)
    return (re.search(r"(?:t\.me|telegram\.me)/[^/\s]+/[^?\s]+", u) is not None)


def extract_miniapp_url(msg):
    """
    Find a Telegram Mini App link in entities or webpage preview.
    Returns (url, source) where source is 'entities:text_url', 'entities:url', 'webpage:url', or 'text:scan'.
    """
    # Entities
    try:
        ents = getattr(msg, "entities", None) or []
        text = getattr(msg, "message", "") or ""
        for e in ents:
            u = getattr(e, "url", None)
            if u and is_miniapp_link(u):
                return u, "entities:text_url"
            if e.__class__.__name__ == "MessageEntityUrl":
                off = getattr(e, "offset", None)
                ln = getattr(e, "length", None)
                if off is not None and ln is not None:
                    u2 = text[off:off+ln]
                    if is_miniapp_link(u2):
                        return u2, "entities:url"
    except Exception:
        pass

    # Webpage preview url
    try:
        media = getattr(msg, "media", None)
        webpage = getattr(media, "webpage", None) if media else None
        u = getattr(webpage, "url", None) if webpage else None
        if u and is_miniapp_link(u):
            return u, "webpage:url"
    except Exception:
        pass

    # Plain text scan
    try:
        text = getattr(msg, "message", "") or ""
        m = re.search(r"(https?://\S+)", text)
        if m:
            u = m.group(1).rstrip(").,;")
            if is_miniapp_link(u):
                return u, "text:scan"
    except Exception:
        pass

    return None, None


def extract_any_url(msg):
    """Best-effort: return the first URL we can find (entities/webpage/text)."""
    # Entities
    try:
        ents = getattr(msg, "entities", None) or []
        text = getattr(msg, "message", "") or ""
        for e in ents:
            u = getattr(e, "url", None)
            if u:
                return str(u), "entities:text_url"
            if e.__class__.__name__ == "MessageEntityUrl":
                off = getattr(e, "offset", None)
                ln = getattr(e, "length", None)
                if off is not None and ln is not None:
                    u2 = text[off:off+ln]
                    if u2:
                        return str(u2), "entities:url"
    except Exception:
        pass

    # Webpage preview url
    try:
        media = getattr(msg, "media", None)
        webpage = getattr(media, "webpage", None) if media else None
        u = getattr(webpage, "url", None) if webpage else None
        if u:
            return str(u), "webpage:url"
    except Exception:
        pass

    # Plain text scan
    try:
        text = getattr(msg, "message", "") or ""
        m = re.search(r"(https?://\S+)", text)
        if m:
            u = m.group(1).rstrip(").,;)")
            if u:
                return str(u), "text:scan"
    except Exception:
        pass

    return None, None

def extract_launch_url(msg, launch_text: str) -> Optional[str]:
    # 1) Inline keyboard button (reply_markup)
    try:
        rm = getattr(msg, "reply_markup", None)
        if rm and getattr(rm, "rows", None):
            target = (launch_text or "").strip().lower()
            for row in rm.rows:
                for btn in row.buttons:
                    text = (getattr(btn, "text", "") or "").strip().lower()
                    if target and text != target:
                        continue
                    url = getattr(btn, "url", None)
                    if url:
                        return url
                    web_app = getattr(btn, "web_app", None)
                    if web_app and getattr(web_app, "url", None):
                        return web_app.url
    except Exception:
        pass

    # 2) Telegram "card" / webpage preview / hidden link -> Mini App link
    try:
        u, _src = extract_miniapp_url(msg)
        if u:
            return u
    except Exception:
        pass

    # 3) Fallback: if enabled, accept any URL from the post.
    # This helps in NEW mode when users paste links directly into their own channel.
    try:
        if bool(cfg.get("miniapp_link_fallback", True)):
            u, _src = extract_any_url(msg)
            if u:
                return u
    except Exception:
        pass

    return None


def parse_telethon_http_proxy(spec: str):
    """Parse HTTP proxy spec for Telethon.

    Telethon accepts a PySocks-style proxy tuple:
      (proxy_type, addr, port, rdns, username, password)

    We support the same accounts.csv formats as parse_http_proxy:
      host:port:user:pass
      host:port

    Returns a tuple suitable for TelegramClient(..., proxy=tuple) or None.
    """
    try:
        s = (spec or "").strip()
        if not s:
            return None
        # Common "empty" values after CSV editing
        if s.lower() in {"-", "none", "null", "nan"}:
            return None

        parts = s.split(":")
        if len(parts) < 2:
            return None

        host = parts[0].strip()
        port_s = parts[1].strip()
        if not host or not port_s:
            return None
        port = int(port_s)

        # Optional auth. Keep password intact even if it contains ':'
        user = None
        pwd = None
        if len(parts) >= 4:
            user = parts[2].strip() or None
            pwd = ":".join(parts[3:]).strip() or None

        # Telethon expects a PySocks-style proxy tuple.
        # Use socks.HTTP constant when available for maximum compatibility.
        proxy_type = getattr(socks, "HTTP", None) if socks else None
        if proxy_type is None:
            # Fallback: older Telethon sometimes accepts a string, but this is less reliable.
            proxy_type = "http"

        if user and pwd:
            return (proxy_type, host, port, True, user, pwd)
        # No auth
        return (proxy_type, host, port, True)
    except Exception:
        return None


async def ainput(prompt: str = "") -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))


HARD_MISSED_PHRASES = [
    "expired",
    "offer has expired",
    "this offer has expired",
    "no longer",
    "not available",
    "not availible",
    "unavailable",
]
ALREADY_CLAIMED_SUCCESS_PHRASES = [
    "already been claimed",
    "already claimed",
    "offer has already been claimed",
    "this offer has already been claimed",
]


def _match_phrase_detail(tnorm: str, lines: list[str], phrases: list[str]) -> tuple[bool, str]:
    for phrase in phrases:
        p = str(phrase or "").strip().lower()
        if not p:
            continue
        if p in tnorm:
            detail = next((l for l in lines if p in l.lower()), p)
            return True, detail
    return False, ""


async def detect_result_via_playwright(url: str, cfg: dict, timeout_ms: int, poll_ms: int, success_patterns, fail_patterns, profile_dir_override: Optional[Path] = None, proxy: Optional[dict] = None, headless: Optional[bool] = None) -> tuple[str, str]:
    """
    Open URL in Playwright and search page text for patterns.
    Uses a persistent browser profile so login/session can be reused.

    Returns: ("success"|"fail"|"timeout"|"error", detail_line)
    """
    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        return ("error", f"Playwright not available: {type(e).__name__}: {e}")

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", str(s)).strip().lower()

    succ = [norm(x) for x in (success_patterns or []) if str(x).strip()]
    fail = [norm(x) for x in (fail_patterns or []) if str(x).strip()]

    # profile dir (can be overridden per account)
    if profile_dir_override is not None:
        profile_dir = profile_dir_override
    else:
        prof_raw = str(cfg.get("browser_profile_dir", "")).strip()
        profile_dir = Path(prof_raw).expanduser() if prof_raw else (DATA_DIR / "browser_profile")
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    def profile_is_empty(p: Path) -> bool:
        try:
            return not any(p.iterdir())
        except Exception:
            return True


    def dump_enabled(flag_name: str, default: bool = True) -> bool:
        # supports both new nested config and legacy flat keys
        try:
            rd = cfg.get("result_detection", {}) if isinstance(cfg.get("result_detection", {}), dict) else {}
        except Exception:
            rd = {}
        return bool(rd.get(flag_name, cfg.get(flag_name, default)))

    def logs_root() -> Path:
        try:
            rd = cfg.get("result_detection", {}) if isinstance(cfg.get("result_detection", {}), dict) else {}
        except Exception:
            rd = {}
        raw = str(rd.get("logs_dir", cfg.get("logs_dir", "")) or "").strip()
        p = Path(raw).expanduser() if raw else (DATA_DIR / "logs")
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return p

    async def dump_page_artifacts(page, url: str, reason: str, detail: str = "") -> str:
        """Save text/html/screenshot for debugging. Returns folder path or '' on failure."""
        root = logs_root()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        folder = root / stamp
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            return ""
        # safe url for logs (strip tgWebAppData)
        surl = safe_url(url)
        meta = {
            "ts": stamp,
            "reason": reason,
            "detail": detail,
            "url": surl,
            "headless": bool(cfg.get("headless_mode", True)),
            "profile_dir": str(profile_dir),
        }
        try:
            (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        try:
            text = await page.inner_text("body")
        except Exception:
            try:
                text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                text = ""

        try:
            (folder / "page.txt").write_text(str(text or ""), encoding="utf-8")
        except Exception:
            pass

        try:
            html = await page.content()
            (folder / "page.html").write_text(str(html or ""), encoding="utf-8")
        except Exception:
            pass

        try:
            await page.screenshot(path=str(folder / "shot.png"), full_page=True)
        except Exception:
            pass

        return str(folder)
    first_login_headed = bool(cfg.get("browser_first_login_headed", True))


    def get_int(key: str, default: int) -> int:
        # supports nested 'retries' and legacy flat keys
        try:
            retries = cfg.get("retries", {}) if isinstance(cfg.get("retries", {}), dict) else {}
        except Exception:
            retries = {}
        try:
            return int(retries.get(key, cfg.get(key, default)))
        except Exception:
            return int(default)

    def get_list(key: str, default: list) -> list:
        try:
            retries = cfg.get("retries", {}) if isinstance(cfg.get("retries", {}), dict) else {}
        except Exception:
            retries = {}
        val = retries.get(key, cfg.get(key, default))
        if isinstance(val, list):
            return val
        return default

    def host_key(h: str) -> str:
        h = (h or "").strip().lower()
        if h.startswith("www."):
            h = h[4:]
        return h

    def domain_blocked(host: str, domains: list) -> str:
        h = host_key(host)
        for d in (domains or []):
            dd = host_key(str(d))
            if not dd:
                continue
            if h == dd or h.endswith("." + dd):
                return dd
        return ""

    async def run_once(headless: bool) -> tuple[str, str]:
        """
        Retry strategy:
          - checks_per_cycle checks every interval_ms
          - then reload
          - repeat reload_cycles times
        If final URL domain is in no_retry_domains => do ONE check then return 'skip' (unless success/fail matched).
        """
        start_t = time.time()
        # NOTE: The mini-app result text often appears with a delay.
        # We intentionally re-check the page a few times before declaring a final result.
        # Per current UX requirements: check every 5 seconds, 5 times, then return timeout.
        checks_per_cycle = 5
        interval_ms = 5000
        reload_cycles = 1
        no_retry_domains = get_list("no_retry_domains", [
            "twitch.tv", "instagram.com", "x.com", "twitter.com", "kick.com", "youtube.com", "youtu.be"
        ])

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                proxy=proxy if proxy else None,
            )
            page = await context.new_page()
            # Detect when the user closes the browser window (headed mode).
            browser_closed = asyncio.Event()
            def _on_close(*args, **kwargs):
                try:
                    browser_closed.set()
                except Exception:
                    pass
            try:
                page.on("close", _on_close)
            except Exception:
                pass
            try:
                context.on("close", _on_close)
            except Exception:
                pass


            wait_until = str(cfg.get("goto_wait_until", "commit") or "commit").strip() or "commit"

            async def nav_first():
                # Fast open: do NOT wait for networkidle. We only need a quick navigation
                # so the tab/window appears immediately; detection will poll.
                try:
                    await page.goto(url, wait_until=wait_until)
                except Exception:
                    pass

            async def nav_reload():
                try:
                    await page.reload(wait_until=wait_until)
                except Exception as e:
                    if 'closed' in str(e).lower() or 'target' in str(e).lower():
                        try:
                            browser_closed.set()
                        except Exception:
                            pass
                    try:
                        await page.goto(url, wait_until=wait_until)
                    except Exception:
                        pass
                # No networkidle wait here on purpose.

            async def read_text() -> tuple[str, list]:
                try:
                    text = await page.inner_text("body")
                except Exception:
                    try:
                        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
                    except Exception:
                        text = ""
                raw_lines = [re.sub(r"\s+", " ", l).strip() for l in str(text).splitlines()]
                lines = [l for l in raw_lines if l]
                return (str(text or ""), lines)

            def match_fail(tnorm: str, lines: list) -> tuple[bool, str]:
                for pat in fail:
                    if pat and pat in tnorm:
                        detail = next((l for l in lines if pat in l.lower()), pat)
                        return True, detail
                return False, ""

            def match_missed(tnorm: str, lines: list) -> tuple[bool, str]:
                ok, detail = _match_phrase_detail(tnorm, lines, HARD_MISSED_PHRASES)
                if ok:
                    return True, detail
                return False, ""

            def match_success(tnorm: str, lines: list) -> tuple[bool, str]:
                ok, detail = _match_phrase_detail(tnorm, lines, ALREADY_CLAIMED_SUCCESS_PHRASES)
                if ok:
                    return True, detail
                if succ:
                    for l in lines:
                        low = l.lower()
                        if all(p in low for p in succ):
                            return True, l
                    if len(succ) >= 2 and all(p in tnorm for p in succ[:2]):
                        detail = next((l for l in lines if succ[0] in l.lower()), succ[0])
                        return True, detail
                return False, ""

            await nav_first()

            # After redirects, decide skip based on final URL domain
            final_url = ""
            try:
                final_url = page.url or ""
            except Exception:
                final_url = ""
            blocked = ""
            try:
                blocked = domain_blocked(urllib.parse.urlparse(final_url).hostname or "", no_retry_domains)
            except Exception:
                blocked = ""

            # One-shot check for blocked domains (SKIP if no match)
            if blocked:
                # Hard SKIP for blocked domains: do not read page text, do not retry, do not dump.
                try:
                    await context.close()
                except Exception:
                    pass
                return ("skip", f"blocked domain={blocked} | url={final_url or url}")

            for cycle in range(reload_cycles):
                # Hard stop: if user closed the window (headed) or we exceed 5 minutes, stop.
                if browser_closed.is_set():
                    try:
                        await context.close()
                    except Exception:
                        pass
                    return ("user_stop", "browser closed")
                if (time.time() - start_t) >= 300:
                    try:
                        await context.close()
                    except Exception:
                        pass
                    return ("timeout", "auto-stop after 5m")
                if cycle > 0:
                    status_info(f"🔄 RELOAD {cycle+1}/{reload_cycles}")
                    await nav_reload()

                for chk in range(checks_per_cycle):
                    if browser_closed.is_set():
                        try:
                            await context.close()
                        except Exception:
                            pass
                        return ("user_stop", "browser closed")
                    if (time.time() - start_t) >= 300:
                        try:
                            await context.close()
                        except Exception:
                            pass
                        return ("timeout", "auto-stop after 5m")
                    status_info(f"🔁 CHECK {chk+1}/{checks_per_cycle} (cycle {cycle+1}/{reload_cycles})")
                    text, lines = await read_text()
                    tnorm = norm(text)

                    # hard-success phrases must stay SUCCESS
                    ok, detail = _match_phrase_detail(tnorm, lines, ALREADY_CLAIMED_SUCCESS_PHRASES)
                    if ok:
                        await context.close()
                        return ("success", detail)

                    ok, detail = match_missed(tnorm, lines)
                    if ok:
                        await context.close()
                        return ("missed", detail)

                    ok, detail = match_success(tnorm, lines)
                    if ok:
                        await context.close()
                        return ("success", detail)

                    ok, detail = match_fail(tnorm, lines)
                    if ok:
                        dump_path = ""
                        if dump_enabled("dump_on_fail", True):
                            dump_path = await dump_page_artifacts(page, (page.url or url), reason="fail", detail=str(detail))
                        await context.close()
                        if dump_path:
                            return ("fail", f"{detail} | dump={dump_path}")
                        return ("fail", detail)

                    last_snip = norm(text)[:220] if text else ""
                    # wait between checks
                    if chk < checks_per_cycle - 1:
                        await asyncio.sleep(interval_ms / 1000)

            # exhausted cycles
            dump_path = ""
            if dump_enabled("dump_on_timeout", True):
                dump_path = await dump_page_artifacts(page, (page.url or url), reason="timeout", detail=last_snip)
            await context.close()
            elapsed = int((time.time() - start_t) * 1000)
            base = f"no match after {elapsed}ms (cycles={reload_cycles}, checks={checks_per_cycle}, interval_ms={interval_ms}) | url={safe_url(page.url or url)} | snippet='{last_snip}'"
            if dump_path:
                return ("timeout", base + f" | dump={dump_path}")
            return ("timeout", base)


    # If it's the first time (profile empty), open headed once so user can login.
    if first_login_headed and profile_is_empty(profile_dir):
        try:
            status_info("🔐 LOGIN NEEDED: a browser window will open. Login once, then press Enter here.")
            # run headed just to let user login; we don't attempt detection here
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=False,
                    proxy=proxy if proxy else None,
                )
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                except Exception:
                    pass
                await ainput("")
                await context.close()
        except Exception:
            # if headed fails, continue with headless anyway
            pass

    # Real detection run
    try:
        # If headless is not explicitly provided, fall back to config (default True)
        if headless is None:
            headless = bool(cfg.get("headless_mode", True))
        return await run_once(headless=bool(headless))
    except Exception as e:
        return ("error", f"{type(e).__name__}: {e}")


async def detect_result_playwright_keep_open(url: str, cfg: dict, timeout_ms: int, poll_ms: int, success_patterns, fail_patterns, profile_dir: Path, proxy: Optional[dict], headless: bool) -> tuple[str, str, object, object]:
    """Like detect_result_via_playwright(), but keeps Chromium window/context open.

    Returns: (res, detail, playwright_handle, context_handle)
    Caller MUST close: await context.close(); await playwright.stop()
    """
    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        return ("error", f"Playwright not available: {type(e).__name__}: {e}", None, None)

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", str(s)).strip().lower()

    succ = [norm(x) for x in (success_patterns or []) if str(x).strip()]
    fail = [norm(x) for x in (fail_patterns or []) if str(x).strip()]

    async def read_text(page) -> tuple[str, list]:
        try:
            text = await page.inner_text("body")
        except Exception:
            try:
                text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                text = ""
        raw_lines = [re.sub(r"\s+", " ", l).strip() for l in str(text).splitlines()]
        lines = [l for l in raw_lines if l]
        return (str(text or ""), lines)

    def match_fail(tnorm: str, lines: list) -> tuple[bool, str]:
        for pat in fail:
            if pat and pat in tnorm:
                detail = next((l for l in lines if pat in l.lower()), pat)
                return True, detail
        return False, ""

    def match_missed(tnorm: str, lines: list) -> tuple[bool, str]:
        ok, detail = _match_phrase_detail(tnorm, lines, HARD_MISSED_PHRASES)
        if ok:
            return True, detail
        return False, ""

    def match_success(tnorm: str, lines: list) -> tuple[bool, str]:
        ok, detail = _match_phrase_detail(tnorm, lines, ALREADY_CLAIMED_SUCCESS_PHRASES)
        if ok:
            return True, detail
        if succ:
            for l in lines:
                low = l.lower()
                if all(p in low for p in succ):
                    return True, l
            if len(succ) >= 2 and all(p in tnorm for p in succ[:2]):
                detail = next((l for l in lines if succ[0] in l.lower()), succ[0])
                return True, detail
        return False, ""

    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=bool(headless),
        proxy=proxy if proxy else None,
    )
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded")
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    # In OLD (test) flows, the page often renders result text with a delay.
    # UX requirement: re-check every 5 seconds, 5 times, then give a final answer.
    start_t = __import__('time').time()
    attempts = 5
    delay_s = 5
    # Ensure timeout_ms is not shorter than our minimum retry window.
    timeout_ms_eff = max(int(timeout_ms or 0), attempts * delay_s * 1000)

    for i in range(attempts):
        if (__import__('time').time() - start_t) * 1000 >= timeout_ms_eff:
            return ("timeout", f"no match after {int((__import__('time').time()-start_t)*1000)}ms", pw, context)

        text, lines = await read_text(page)
        tnorm = norm(text)
        ok, detail = _match_phrase_detail(tnorm, lines, ALREADY_CLAIMED_SUCCESS_PHRASES)
        if ok:
            return ("success", detail, pw, context)
        ok, detail = match_missed(tnorm, lines)
        if ok:
            return ("missed", detail, pw, context)
        ok, detail = match_fail(tnorm, lines)
        if ok:
            return ("fail", detail, pw, context)
        ok, detail = match_success(tnorm, lines)
        if ok:
            return ("success", detail, pw, context)

        if i < attempts - 1:
            await __import__('asyncio').sleep(delay_s)

    return ("timeout", f"no match after {int((__import__('time').time()-start_t)*1000)}ms", pw, context)


async def detect_result_via_warm_session(session: 'WarmBrowserSession', url: str, cfg: dict, timeout_ms: int, poll_ms: int, success_patterns, fail_patterns) -> tuple[str, str]:
    """Navigate using an already-started Playwright session (per-account warm browser).

    Optimized for FAST OPENING:
    - navigation uses session.wait_until (default: commit)
    - avoids networkidle waits
    - detection polls lightly (poll_ms)
    """

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", str(s)).strip().lower()

    succ = [norm(x) for x in (success_patterns or []) if str(x).strip()]
    fail = [norm(x) for x in (fail_patterns or []) if str(x).strip()]

    async def read_text(page) -> tuple[str, list]:
        try:
            text = await page.inner_text("body")
        except Exception:
            try:
                text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                text = ""
        raw_lines = [re.sub(r"\s+", " ", l).strip() for l in str(text).splitlines()]
        lines = [l for l in raw_lines if l]
        return (str(text or ""), lines)

    def match_fail(tnorm: str, lines: list) -> tuple[bool, str]:
        for pat in fail:
            if pat and pat in tnorm:
                detail = next((l for l in lines if pat in l.lower()), pat)
                return True, detail
        return False, ""

    def match_missed(tnorm: str, lines: list) -> tuple[bool, str]:
        ok, detail = _match_phrase_detail(tnorm, lines, HARD_MISSED_PHRASES)
        if ok:
            return True, detail
        return False, ""

    def match_success(tnorm: str, lines: list) -> tuple[bool, str]:
        ok, detail = _match_phrase_detail(tnorm, lines, ALREADY_CLAIMED_SUCCESS_PHRASES)
        if ok:
            return True, detail
        if succ:
            # any-of substring
            for p in succ:
                if p and p in tnorm:
                    detail = next((l for l in lines if p in l.lower()), p)
                    return True, detail
            # all-in-one-line heuristic
            for l in lines:
                low = l.lower()
                if all(p in low for p in succ):
                    return True, l
        return False, ""

    # Navigate fast (browser is already warm)
    nav_timeout = int(cfg.get("goto_timeout_ms", 15000))
    page = await session.goto(url, timeout_ms=nav_timeout)

    start_t = time.time()
    poll_ms = int(poll_ms or 500)
    timeout_ms = int(timeout_ms or 15000)

    while (time.time() - start_t) * 1000 < timeout_ms:
        text, lines = await read_text(page)
        tnorm = norm(text)

        ok, detail = _match_phrase_detail(tnorm, lines, ALREADY_CLAIMED_SUCCESS_PHRASES)
        if ok:
            return ("success", detail)

        ok, detail = match_missed(tnorm, lines)
        if ok:
            return ("missed", detail)

        ok, detail = match_fail(tnorm, lines)
        if ok:
            return ("fail", detail)

        ok, detail = match_success(tnorm, lines)
        if ok:
            return ("success", detail)

        await asyncio.sleep(max(0.05, poll_ms / 1000.0))

    return ("timeout", f"no match after {int((time.time()-start_t)*1000)}ms")




def _strip_tg_prefix(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r'^\s*https?://', '', s, flags=re.I)
    s = re.sub(r'^\s*(?:t\.me|telegram\.me)/', '', s, flags=re.I)
    return s.strip()

def _invite_hash_from_channel_ref(channel_ref: str) -> Optional[str]:
    """Accepts +HASH / joinchat/HASH and their t.me links."""
    s = _strip_tg_prefix(channel_ref)
    if not s:
        return None
    if s.startswith('+'):
        return s[1:].strip()
    m = re.match(r'^(?:joinchat/)([A-Za-z0-9_-]+)$', s)
    if m:
        return m.group(1)
    return None

async def resolve_channel_entity(client: TelegramClient, channel_ref: str):
    """Resolve channel/group by @username, public link, id, or private invite link."""
    ch = (channel_ref or '').strip()
    if not ch:
        raise ValueError('empty channel')

    inv = _invite_hash_from_channel_ref(ch)
    if inv:
        # Try to join/import (idempotent). If already joined, CheckChatInvite returns the chat.
        try:
            await client(functions.messages.ImportChatInviteRequest(inv))
        except Exception:
            pass
        try:
            chk = await client(functions.messages.CheckChatInviteRequest(inv))
            chat = getattr(chk, 'chat', None)
            if chat is not None:
                return chat
        except Exception:
            pass
        # Last fallback
        return await client.get_entity(_strip_tg_prefix(ch))

    norm_ref = ch
    if re.match(r'^(?:t\.me|telegram\.me)/', norm_ref, flags=re.I) or re.match(r'^https?://', norm_ref, flags=re.I):
        norm_ref = _strip_tg_prefix(norm_ref)
    return await client.get_entity(norm_ref)



async def watch_multi(cfg: dict, *, resume: bool = False) -> None:
    """Multi-account watcher. Keeps menu visuals; shows a status table via periodic redraw."""
    api_id = int(cfg["api_id"])
    api_hash = str(cfg["api_hash"])

    channel = str(cfg.get("channel", "") or "").strip()
    if not channel:
        clear()
        status_info("Set channel first (example: @channel or t.me/+INVITE)")
        await ainput("Press Enter to return...")
        return

    launch_text = str(cfg.get("launch_button_text", "Launch"))
    pre_spec = parse_delay_spec(cfg.get("pre_open_delay_ms", 0))
    # cooldown removed
    open_only_tg = bool(cfg.get("open_only_telegram_links", True))
    force_tg_app = bool(cfg.get("force_open_in_telegram_app", True))
    headless_mode = bool(cfg.get("headless_mode", False))
    dup_window = int(cfg.get("duplicate_window_ms", 60000))

    watch_mode = str(cfg.get("watch_mode", "new")).strip().lower()  # new|old
    if watch_mode not in ("new", "old"):
        watch_mode = "new"

    # UI mode:
    # - classic: clear+redraw (old behavior)
    # - ptk: prompt_toolkit TUI with a persistent command line
    ui_mode = str(cfg.get("ui_mode", "ptk") or "ptk").strip().lower()
    if ui_mode not in ("classic", "ptk"):
        ui_mode = "ptk"

    # Monitoring mode for NEW architecture.
    # - live_only: only events.NewMessage (+ keepalive)
    # - poll_only: only staggered polling via get_messages
    # - live+poll: both
    monitor_mode = str(cfg.get("monitor_mode", "live+poll") or "live+poll").strip().lower()
    if monitor_mode in ("live+poll", "live_and_poll", "both"):
        monitor_mode = "live+poll"
    if monitor_mode not in ("live_only", "poll_only", "live+poll"):
        monitor_mode = "live+poll"

    poll_interval_sec = float(cfg.get("poll_interval_sec", 1) or 1)
    if poll_interval_sec <= 0:
        poll_interval_sec = 1
    keepalive_interval_sec = float(cfg.get("keepalive_interval_sec", 1) or 1)
    if keepalive_interval_sec <= 0:
        keepalive_interval_sec = 1

    dedup_ttl_sec = int(cfg.get("event_dedup_ttl_sec", 1800) or 1800)  # 10-30m canonical
    if dedup_ttl_sec < 600:
        dedup_ttl_sec = 600

    # accounts.csv defaults to DATA_DIR/accounts.csv (legacy root fallback supported)
    csv_raw = str(cfg.get("accounts_csv", "") or "").strip()
    csv_path = resolve_accounts_csv_path(csv_raw)
    accounts = load_accounts_csv(csv_path)

    # Auto-throttle: each account must not poll more often than once per 10s.
    # With N accounts, tick interval must be >= 10 / N seconds.
    try:
        min_tick = 10.0 / float(max(1, len(accounts)))
        if poll_interval_sec < min_tick:
            poll_interval_sec = min_tick
    except Exception:
        pass

    # OLD mode asks link at runtime (not stored in config)
    old_link = ""
    if watch_mode == "old":
        clear()
        status_info("OLD mode: paste message link (t.me/<user>/<id> or t.me/c/<id>/<msg>)")
        old_link = (await ainput("Link: ")).strip()
        if not old_link:
            status_info("No link provided. Returning...")
            await asyncio.sleep(0.5)
            return


    global _SUPPRESS_PREFLIGHT_ONCE, _RUNTIME_PREFLIGHT_DONE
    if _SUPPRESS_PREFLIGHT_ONCE or _RUNTIME_PREFLIGHT_DONE:
        resume = True
        _SUPPRESS_PREFLIGHT_ONCE = False

    if not resume:
        # PRE-FLIGHT AUTH (sequential): Telethon interactive login prompts are NOT concurrency-safe.
        # We log in accounts one-by-one (if needed) to create .session files, then run parallel watchers without prompts.
        clear()
        status_info("Pre-flight: checking Telegram sessions for all accounts...")
        for a in accounts:
            phone_pf = str(a.get("phone") or "").strip()
            label_pf = acct_label(a)
            if not phone_pf:
                status_error(f"{label_pf}: missing phone in accounts.csv")
                continue
            session_dir_pf = DATA_DIR / "sessions"
            session_dir_pf.mkdir(parents=True, exist_ok=True)
            session_file_pf = session_dir_pf / re.sub(r'[^0-9A-Za-z_\-]+', '_', phone_pf)
            try:
                status_info(f"Auth check: {label_pf}")
                c = TelegramClient(str(session_file_pf), api_id, api_hash)
                await c.connect()
                if not await c.is_user_authorized():
                    status_info(f"{label_pf}: login required. Requesting code...")
                    # This will prompt for code/password for THIS account only (sequentially).
                    await c.start(phone=phone_pf)
                # quick channel access check (gives clearer errors early)
                try:
                    await resolve_channel_entity(c, channel)
                except Exception:
                    status_error(f"{label_pf}: cannot access channel {channel} (join it / check @tag)")
                await c.disconnect()
            except Exception as e:
                try:
                    await c.disconnect()
                except Exception:
                    pass
                status_error(f"{label_pf}: auth error {type(e).__name__}: {e}")
        status_info("Pre-flight done. Starting watchers...")
        await asyncio.sleep(0.6)
        _RUNTIME_PREFLIGHT_DONE = True

    # shared UI state
    state: dict[str, dict] = {}
    default_idle_status = "MONITORING" if watch_mode != "old" else "WAITING"
    for a in accounts:
        state[acct_label(a)] = {
            "phone": a.get("phone"),
            "proxy": a.get("proxy_raw", ""),
            "status": default_idle_status,
            "detail": "",
            "ticket": "—",
        }

    # While the watch screen is running, suppress any status_* prints (log to file instead).
    global _UI_QUIET
    _UI_QUIET = True

    stop_all = asyncio.Event()
    ui_paused = asyncio.Event()
    ui_refresh = None
    ui_exit = None
    cmd_q: asyncio.Queue[str] = asyncio.Queue()
    warm_cache = _WARM_CACHE
    stop_reason: dict[str, str] = {"mode": "run"}  # run | pause | quit
    quit_all = asyncio.Event()
    gotem_lock = asyncio.Lock()

    def _looks_like_proxy_issue(msg: str) -> bool:
        s = str(msg or "").lower()
        return any(x in s for x in (
            "proxy",
            "generalproxyerror",
            "proxyconnectionerror",
            "err_proxy",
            "407",
            "socks",
        ))

    def _short_proxy_hint(msg: str) -> str:
        s = str(msg or "").lower()
        # keep it short for W_ST=18
        if "407" in s:
            return "407"
        if "reset" in s:
            return "RESET"
        if "refused" in s:
            return "REFUSED"
        if "timeout" in s or "timed out" in s:
            return "TIMEOUT"
        if "closed" in s or "0 bytes read" in s:
            return "CLOSED"
        return ""

    async def bump_gotem() -> None:
        async with gotem_lock:
            try:
                cur = int(cfg.get("gotem", 0) or 0)
            except Exception:
                cur = 0
            cfg["gotem"] = cur + 1
            try:
                save_config(cfg)
            except Exception:
                pass

    def _send_cmd(cmd: str) -> None:
        try:
            cmd_q.put_nowait(cmd)
        except Exception:
            pass

    def _request_stop(mode: str = "pause") -> None:
        try:
            stop_reason["mode"] = mode
            if mode == "pause":
                ui_paused.set()
            if mode == "quit":
                quit_all.set()
            stop_all.set()
            if mode == "quit":
                _send_cmd("quit")
            else:
                _send_cmd("stop")
        except Exception:
            pass

    # live status log (append-only)
    _logs_dir = DATA_DIR / "logs"
    try:
        _logs_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    _status_log_path = _logs_dir / "status_live.tsv"
    _status_log_q: asyncio.Queue[str] = asyncio.Queue()

    async def _status_log_writer():
        """Background writer: serializes all status updates into a live file."""
        try:
            f = open(_status_log_path, "a", encoding="utf-8")
        except Exception:
            f = None
        if f is None:
            return

        def _w(s: str) -> None:
            try:
                if f is not None:
                    f.write(s)
                    f.flush()
            except Exception:
                pass
        try:
            # header if empty
            try:
                if f.tell() == 0:
                    _w("ts\taccount\tstatus\tticket\tdetail\n")
            except Exception:
                pass
            while (not quit_all.is_set()) or (not _status_log_q.empty()):
                try:
                    line = await asyncio.wait_for(_status_log_q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                try:
                    f.write(line)
                    f.flush()
                except Exception:
                    pass
        finally:
            try:
                    f.close()
            except Exception:
                pass

    def _log_status(label: str, status: str, detail: str = "", ticket: str = "") -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        # keep it raw codes (no ANSI), best for grepping
        line = f"{ts}\t{label}\t{status}\t{ticket or ''}\t{(detail or '').replace(chr(10), ' ').replace(chr(13), ' ')}\n"
        try:
            _status_log_q.put_nowait(line)
        except Exception:
            pass


    # Fixed table widths (stable panel). Proxy width is derived from the longest proxy in accounts.csv
    # (plus small padding), then capped so the table never wraps.
    # Slightly wider panel (requested): more room for account + ticket without wrapping.
    W_ACC = 34
    W_ST  = 18   # keep focus on status
    W_TK  = 16

    _proxy_vals = []
    for _a in accounts:
        _p = str(_a.get("proxy_raw") or _a.get("proxy") or "").strip()
        if not _p:
            _p = "-"
        _proxy_vals.append(_p)
    _max_proxy = max([len("Proxy")] + [len(v) for v in _proxy_vals]) if _proxy_vals else len("Proxy")
    # +3 gives a small visual breathing room like Ticket column
    W_PR = max(24, min(_max_proxy + 3, 80))

    # Prevent wrap in typical Terminal widths.
    _table_total = W_ACC + W_ST + W_TK + W_PR + 5
    _MAX_TABLE = 170
    try:
        _term_cols = shutil.get_terminal_size((180, 32)).columns
    except Exception:
        _term_cols = 180
    _max_table = min(_MAX_TABLE, max(80, int(_term_cols) - 2))
    _min_table = W_ACC + W_ST + W_TK + 5 + 20
    if _max_table >= _min_table:
        W_PR = min(W_PR, max(20, _max_table - (W_ACC + W_ST + W_TK + 5)))
    if (W_ACC + W_ST + W_TK + W_PR + 5) > _MAX_TABLE:
        W_PR = max(20, W_PR - ((W_ACC + W_ST + W_TK + W_PR + 5) - _MAX_TABLE))


    def set_row_ui(label: str, status: str, detail: str = "", ticket: str = "", *, log: bool = True):
        """Update UI row; optionally log to status_live.tsv.

        NOTE: Some high-frequency UI statuses (e.g. POLL) should not be logged.
        """
        row = state.get(label)
        if not row:
            return
        row["status"] = status
        row["detail"] = detail
        if ticket:
            row["ticket"] = ticket
        if log:
            try:
                _log_status(label, status, detail, row.get("ticket") or ticket)
            except Exception:
                pass

    def set_row(label: str, status: str, detail: str = "", ticket: str = ""):
        # default behavior: update UI + log
        return set_row_ui(label, status, detail, ticket, log=True)

    def _set_all_rows(status: str, detail: str = "") -> None:
        for lb, row in list(state.items()):
            if lb.startswith("__"):
                continue
            try:
                set_row_ui(lb, status, detail, ticket=row.get("ticket") or "", log=False)
            except Exception:
                pass

    def _drain_queue(q: asyncio.Queue) -> None:
        try:
            while True:
                q.get_nowait()
                try:
                    q.task_done()
                except Exception:
                    pass
        except Exception:
            pass

    run_extras: set[asyncio.Task] = set()

    def _spawn_run(coro) -> None:
        """Create a run-scoped task that is cancelled on STOP."""
        try:
            t = asyncio.create_task(coro)
            run_extras.add(t)
            def _done(_t: asyncio.Task) -> None:
                run_extras.discard(_t)
                # Consume exceptions so they don't print "Task exception was never retrieved"
                # under the full-screen UI.
                try:
                    _t.exception()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            t.add_done_callback(_done)
        except Exception:
            pass

    async def _reset_status_after_global(label: str, sec: int, expect_status: str, new_status: str = "MONITORING") -> None:
        """Run-scoped status reset helper usable by shared coordinators (bus/poll)."""
        try:
            await asyncio.sleep(sec)
            if stop_all.is_set():
                return
            try:
                cur = str(state.get(label, {}).get("status") or "")
            except Exception:
                cur = ""
            if cur == expect_status and watch_mode != "old":
                set_row(label, new_status)
        except Exception:
            pass


    # -----------------------------
    # Shared Event Bus (FINAL ARCH)
    # -----------------------------
    # Runtimes are registered by watch_account once the client is connected.
    runtimes: dict[str, dict] = {}
    runtimes_ready: dict[str, asyncio.Event] = {}
    for a in accounts:
        runtimes_ready[acct_label(a)] = asyncio.Event()

    # POST_FOUND events are deduped globally (not per-account).
    post_q: asyncio.Queue = asyncio.Queue(maxsize=200)
    seen_posts: dict[tuple[int, int], int] = {}  # (chat_id, msg_id) -> ts_ms

    def _seen_cleanup(now_ms: int) -> None:
        try:
            ttl_ms = int(dedup_ttl_sec * 1000)
            for k, ts in list(seen_posts.items()):
                if now_ms - int(ts) > ttl_ms:
                    seen_posts.pop(k, None)
        except Exception:
            pass

    async def emit_post_found(detector_label: str, msg) -> None:
        """Emit POST_FOUND into shared bus once per (chat_id,msg_id).

        IMPORTANT: UI 'NEWMSG' must only appear when the shared bus ACCEPTS
        the post (i.e. it is not a duplicate). Otherwise the UI becomes
        misleading ("NEW MSG" after SUCCESS, or "NEW MSG" with no open).
        """
        try:
            if stop_all.is_set():
                return
            chat_id = int(getattr(msg, "chat_id", 0) or 0)
            msg_id = int(getattr(msg, "id", 0) or 0)
            if not chat_id or not msg_id:
                return
            now_ms = int(time.time() * 1000)
            _seen_cleanup(now_ms)
            key = (chat_id, msg_id)
            if key in seen_posts:
                return
            seen_posts[key] = now_ms

            # Truthful UI: mark NEWMSG ONLY when this post is accepted by
            # the global dedupe.
            try:
                set_row(detector_label, "NEWMSG", f"id={msg_id}")
            except Exception:
                pass
            try:
                post_q.put_nowait((detector_label, chat_id, msg_id))
            except asyncio.QueueFull:
                # Drop if overwhelmed; FCFS prefers freshness.
                pass
        except Exception:
            pass

    async def link_hunt_once(detector_label: str, chat_id: int, msg_id: int) -> tuple[Optional[str], Optional[str]]:
        """Find miniapp/launch URL for a post ONE time with retry schedule.

        Returns (url, ticket) or (None, ticket/None).
        """
        retry_ms = [0, 200, 500, 1000, 1500]
        ticket = None
        rt = runtimes.get(detector_label)
        if not rt:
            return None, None
        client = rt.get("client")
        ch_ent = rt.get("ch_ent")
        if client is None or ch_ent is None:
            return None, None

        for d in retry_ms:
            if stop_all.is_set():
                return None, ticket
            if d:
                await asyncio.sleep(d / 1000)
            try:
                m = await client.get_messages(ch_ent, ids=msg_id)
            except Exception:
                m = None
            if not m:
                continue
            try:
                txt = getattr(m, "raw_text", "") or getattr(m, "message", "") or ""
                if ticket is None:
                    ticket = extract_ticket_info(txt)
            except Exception:
                pass

            url = extract_launch_url(m, launch_text)
            if not url and cfg.get("miniapp_link_fallback", True):
                u_any, _src_any = extract_any_url(m)
                if u_any:
                    url = u_any
            if url:
                return url, ticket
        return None, ticket

    async def fanout_open(url: str, ticket: str, post_key: tuple[int, int]):
        """Broadcast OPEN to ALL accounts (warm headless)."""
        for lb, rt in list(runtimes.items()):
            oq = rt.get("open_q")
            if oq is None:
                continue
            try:
                oq.put_nowait((url, ticket or "", post_key))
            except asyncio.QueueFull:
                # If a particular account is backed up, skip it (FCFS).
                pass

    async def post_processor_loop():
        """Single consumer: POST_FOUND -> link-hunt once -> fanout OPEN."""
        while not stop_all.is_set():
            try:
                detector_label, chat_id, msg_id = await asyncio.wait_for(post_q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                # UI hint: who detected.
                set_row(detector_label, "POST", f"id={msg_id}")

                url, ticket = await link_hunt_once(detector_label, chat_id, msg_id)
                if not url:
                    set_row(detector_label, "NO_LINK", "no miniapp link", ticket=ticket or "")
                    # Return to MONITORING shortly.
                    if watch_mode != "old":
                        _spawn_run(_reset_status_after_global(detector_label, 10, "NO_LINK", "MONITORING"))
                    continue

                # One-shot broadcast.
                await fanout_open(url, ticket or "", (chat_id, msg_id))
            finally:
                try:
                    post_q.task_done()
                except Exception:
                    pass

    async def poll_scheduler_loop():
        """Staggered polling: one account per second queries latest message."""
        labels = [acct_label(a) for a in accounts]
        idx = 0
        last_seen_id: Optional[int] = None

        # Wait until at least one runtime is ready.
        while not stop_all.is_set():
            if any(ev.is_set() for ev in runtimes_ready.values()):
                break
            await asyncio.sleep(0.1)

        # Initialize baseline: take current latest message id and do NOT emit.
        for _ in range(len(labels) * 2):
            if stop_all.is_set():
                return
            lb = labels[idx % len(labels)]
            idx += 1
            ev = runtimes_ready.get(lb)
            if ev is None or not ev.is_set():
                continue
            rt = runtimes.get(lb)
            if not rt:
                continue
            client = rt.get("client")
            ch_ent = rt.get("ch_ent")
            if client is None or ch_ent is None:
                continue
            try:
                m = await client.get_messages(ch_ent, limit=1)
                if m:
                    last_seen_id = int(getattr(m[0], "id", 0) or 0)
                    break
            except Exception:
                continue
        # POLL indicator is a UI overlay (does NOT overwrite persistent statuses).
        # It is set immediately when we pick the account for this tick, and held long
        # enough to be visible in the render loop.
        min_poll_indicator_sec = 0.85

        while not stop_all.is_set():
            t0 = time.time()
            try:
                lb = labels[idx % len(labels)]
                idx += 1

                # Set truthful UI overlay: THIS account is polling right now.
                global _POLL_OVERLAY_LABEL, _POLL_OVERLAY_UNTIL
                _POLL_OVERLAY_LABEL = lb
                _POLL_OVERLAY_UNTIL = time.time() + max(min_poll_indicator_sec, float(poll_interval_sec) * 0.9)

                ev = runtimes_ready.get(lb)
                if ev is None or not ev.is_set():
                    await asyncio.sleep(0.05)
                    continue
                rt = runtimes.get(lb)
                if not rt:
                    await asyncio.sleep(0.05)
                    continue
                client = rt.get("client")
                ch_ent = rt.get("ch_ent")
                if client is None or ch_ent is None:
                    await asyncio.sleep(0.05)
                    continue

                try:
                    msgs = await client.get_messages(ch_ent, limit=1)
                except Exception:
                    msgs = None
                if msgs:
                    m0 = msgs[0]
                    mid = int(getattr(m0, "id", 0) or 0)
                    if last_seen_id is None:
                        last_seen_id = mid
                    elif mid > last_seen_id:
                        last_seen_id = mid
                        # emit exactly the latest one (FCFS target)
                        await emit_post_found(lb, m0)
                # else: nothing
            finally:
                elapsed = time.time() - t0
                sleep_s = max(0.0, float(poll_interval_sec) - elapsed)
                await asyncio.sleep(sleep_s)

    async def keepalive_loop():
        """Staggered keepalive: one account per tick makes a cheap request."""
        labels = [acct_label(a) for a in accounts]
        idx = 0
        while not stop_all.is_set():
            t0 = time.time()
            lb = labels[idx % len(labels)]
            idx += 1
            ev = runtimes_ready.get(lb)
            if ev is not None and ev.is_set():
                rt = runtimes.get(lb)
                if rt and rt.get("client") is not None:
                    client = rt["client"]
                    try:
                        # cheapest reliable keepalive
                        await client.get_me()
                    except Exception:
                        pass
            elapsed = time.time() - t0
            sleep_s = max(0.0, float(keepalive_interval_sec) - elapsed)
            await asyncio.sleep(sleep_s)



    async def ensure_joined(client: TelegramClient, ch_ent, label: str) -> bool:
        # If account is not a participant of the target channel/group, try joining it.
        try:
            # Only channels/supergroups support channels.GetParticipantRequest/JoinChannelRequest.
            if not isinstance(ch_ent, types.Channel):
                return True

            me_inp = await client.get_input_entity('me')
            try:
                await client(functions.channels.GetParticipantRequest(channel=ch_ent, participant=me_inp))
                return True
            except UserNotParticipantError:
                # Try to join once.
                set_row(label, 'NOACCESS', 'not in channel; trying to join')
                set_row(label, 'JOINING')
                try:
                    await client(functions.channels.JoinChannelRequest(channel=ch_ent))
                    set_row(label, 'JOINED')
                    await asyncio.sleep(0.5)
                    # Re-check participant status
                    await client(functions.channels.GetParticipantRequest(channel=ch_ent, participant=me_inp))
                    return True
                except Exception as e:
                    set_row(label, 'JOINFAIL', f"{type(e).__name__}: {e}")
                    return False
        except ChannelPrivateError:
            set_row(label, 'JOINFAIL', 'private/invite required')
            return False
        except Exception:
            # If we cannot check, don't block the run.
            return True
    async def auto_stop_5m():
        # Auto-stop guard for headed (non-headless) runs.
        # IMPORTANT: In OLD (test) mode the user wants the final status to stay on-screen
        # and the browser page to remain open; so we DISABLE auto-stop in OLD mode.
        if headless_mode or watch_mode == "old":
            return
        await asyncio.sleep(300)
        if not stop_all.is_set():
            # keep UI simple: just stop everything
            _request_stop("pause")

    def build_watch_text() -> str:
        """Render the watch screen into a single ANSI string.

        Used both by classic clear+redraw and by prompt_toolkit TUI.
        """
        # WATCH SCREEN: table-only layout (as requested)
        # Fixed widths (do NOT depend on terminal size) so the panel doesn't
        # "breathe" when the window size changes. Long values are clipped
        # with ellipsis so borders never break.
        # Proxy width (W_PR) is derived once from accounts.csv.

        def line_top():
            return dimBorder("┌" + "─"*W_ACC + "┬" + "─"*W_ST + "┬" + "─"*W_TK + "┬" + "─"*W_PR + "┐", bright=True)
        def line_mid():
            return dimBorder("├" + "─"*W_ACC + "┼" + "─"*W_ST + "┼" + "─"*W_TK + "┼" + "─"*W_PR + "┤")
        def line_bot():
            return dimBorder("└" + "─"*W_ACC + "┴" + "─"*W_ST + "┴" + "─"*W_TK + "┴" + "─"*W_PR + "┘")

        def _header_label(txt: str) -> str:
            return f" {theme.gray_text('▌')} {theme.gray_text(txt)}"

        b_v = dimBorder("│")

        lines: list[str] = []
        lines.append(line_top())
        header = (
            b_v + _pad_ansi(_header_label("Account"), W_ACC) +
            b_v + _pad_ansi(_header_label("Status"), W_ST) +
            b_v + _pad_ansi(_header_label("Ticket"), W_TK) +
            b_v + _pad_ansi(_header_label("Proxy"), W_PR) + b_v
        )
        lines.append(header)
        lines.append(line_mid())
        row_idx = 0
        for label, row in state.items():
            if str(label).startswith("__"):
                continue
            acc = str(label)
            proxy = str(row.get("proxy") or "-")
            ticket = str(row.get("ticket") or "-")
            if ui_paused.is_set():
                st_code = "STOPPED"
            else:
                st_code = str(row.get("status") or "WAITING")
            detail = str(row.get("detail") or "")

            # Truthful UI overlay: show 📡 POLL only for the account currently doing the poll tick.
            # Do NOT override important statuses like OPENING/SUCCESS/ERROR.
            try:
                global _POLL_OVERLAY_LABEL, _POLL_OVERLAY_UNTIL
                if (label == _POLL_OVERLAY_LABEL) and (time.time() < float(_POLL_OVERLAY_UNTIL)):
                    if str(st_code).upper() in ("WAITING", "IDLE", "MONITORING", "POLL"):
                        st_code = "POLL"
            except Exception:
                pass

            st = _status_cell(st_code, detail)
            tk_plain = "—" if (ticket.strip() in ("", "-", "—")) else _fit_cell(ticket, W_TK-1)
            tk_colored = theme.gray_text("—") if tk_plain == "—" else theme.purple_text(tk_plain)
            pr_colored = formatProxyMasked(proxy, W_PR-1)

            # Clip values (visible width) before padding
            acc_cell = _pad_ansi(" " + _fit_cell(acc, W_ACC-1, kind="email"), W_ACC)
            st_cell  = _pad_ansi(" " + st, W_ST)
            tk_cell  = _pad_ansi(" " + tk_colored, W_TK)
            pr_cell  = _pad_ansi(" " + pr_colored, W_PR)
            row_line = b_v + acc_cell + b_v + st_cell + b_v + tk_cell + b_v + pr_cell + b_v
            lines.append(zebraRow(row_idx, row_line))
            row_idx += 1
        lines.append(line_bot())
        mode_val = theme.pink_text(watch_mode.upper()) if str(watch_mode).lower() == "new" else theme.white_text(watch_mode.upper())
        if ui_mode == "ptk":
            lines.append(
                f"{theme.gray_text('Mode:')} {mode_val}  "
                f"{theme.gray_text('|')}  {theme.gray_text('Channel:')} {theme.cyan_text(channel or '—')}  "
                f"{theme.gray_text('|')}  {theme.gray_text('Commands:')} {theme.purple_text('stop, run, quit')}"
            )
        else:
            lines.append(
                f"{theme.gray_text('Mode:')} {mode_val}  "
                f"{theme.gray_text('|')}  {theme.gray_text('Channel:')} {theme.cyan_text(channel or '—')}  "
                f"{theme.gray_text('|')}  {theme.gray_text('Commands:')} {theme.purple_text('stop, run, quit')}"
            )
        return "\n".join(lines)

    async def render_loop():
        while not quit_all.is_set():
            # Advance monitoring animation deterministically per redraw.
            if not ui_paused.is_set():
                global _MONITOR_PHASE
                _MONITOR_PHASE = (_MONITOR_PHASE + 1) % 4
            clear()
            print(build_watch_text())
            await asyncio.sleep(0.7)

    async def input_loop():
        while not quit_all.is_set():
            cmd = (await ainput("")).strip().lower()
            if cmd in ("s", "stop"):
                _request_stop("pause")
                try:
                    if ui_refresh is not None:
                        ui_refresh()
                except Exception:
                    pass
                continue
            if cmd in ("r", "run"):
                ui_paused.clear()
                stop_reason["mode"] = "run"
                _send_cmd("run")
                continue
            if cmd in ("q", "quit"):
                # mark quit intent for outer loop
                _request_stop("quit")
                return

    async def ptk_ui_loop():
        """prompt_toolkit TUI with a persistent command line.

        Keeps the watch panel updating while the user types commands.
        Falls back to classic UI if prompt_toolkit isn't available.
        """
        try:
            from prompt_toolkit.application import Application
            from prompt_toolkit.formatted_text import ANSI
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.layout import HSplit, Layout
            from prompt_toolkit.layout.controls import FormattedTextControl
            from prompt_toolkit.layout import Window
            from prompt_toolkit.widgets import TextArea
        except Exception:
            # Missing dependency -> fallback to classic UI.
            r = asyncio.create_task(render_loop())
            i = asyncio.create_task(input_loop())
            try:
                await quit_all.wait()
            finally:
                try:
                    r.cancel()
                except Exception:
                    pass
                try:
                    i.cancel()
                except Exception:
                    pass
            return

        output_control = FormattedTextControl(text=ANSI(build_watch_text()))
        output_window = Window(content=output_control, wrap_lines=False, dont_extend_height=False)
        def _refresh():
            try:
                output_control.text = ANSI(build_watch_text())
                app.invalidate()
            except Exception:
                pass
        ui_refresh = _refresh

        input_box = TextArea(
            height=1,
            prompt="acrFetcher> ",
            multiline=False,
            wrap_lines=False,
        )

        async def handle_cmd(cmd: str):
            c = (cmd or "").strip().lower()
            if not c:
                return
            if c in ("s", "stop"):
                _request_stop("pause")
                try:
                    if ui_refresh is not None:
                        ui_refresh()
                except Exception:
                    pass
                return
            if c in ("r", "run"):
                ui_paused.clear()
                stop_reason["mode"] = "run"
                _send_cmd("run")
                return
            if c in ("q", "quit"):
                _request_stop("quit")
                try:
                    app.exit()
                except Exception:
                    pass
                return
            # Unknown commands are ignored on purpose (no spam).

        def accept(buff):
            txt = input_box.text
            input_box.text = ""
            asyncio.create_task(handle_cmd(txt))

        input_box.accept_handler = accept

        kb = KeyBindings()

        @kb.add("c-c")
        def _(event):
            _request_stop("quit")
            try:
                app.exit()
            except Exception:
                pass

        app = Application(
            layout=Layout(HSplit([output_window, input_box])),
            key_bindings=kb,
            full_screen=True,
        )
        try:
            nonlocal ui_exit
            ui_exit = app.exit
        except Exception:
            pass

        async def render_task():
            while True:
                if quit_all.is_set():
                    break
                if not stop_all.is_set():
                    if not ui_paused.is_set():
                        global _MONITOR_PHASE
                        _MONITOR_PHASE = (_MONITOR_PHASE + 1) % 4
                output_control.text = ANSI(build_watch_text())
                app.invalidate()
                await asyncio.sleep(0.7)

        rt = asyncio.create_task(render_task())
        try:
            await app.run_async()
        finally:
            try:
                rt.cancel()
            except Exception:
                pass

    async def watch_account(account: dict):
        label = acct_label(account)
        phone = str(account.get("phone") or "").strip()
        proxy = account.get("proxy")
        profile_dir = DATA_DIR / "profiles" / re.sub(r'[^0-9A-Za-z_\-]+', '_', phone)
        session_dir = DATA_DIR / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        session_file = session_dir / re.sub(r'[^0-9A-Za-z_\-]+', '_', phone)

        client: Optional[TelegramClient] = None
        processed: dict = {}

        # Per-account queues:
        # - msg_q: OLD mode one-shot processing pipeline (kept intact)
        # - open_q: FINAL ARCH fanout OPEN pipeline (NEW mode)
        msg_q: asyncio.Queue = asyncio.Queue(maxsize=25)
        open_q: asyncio.Queue = asyncio.Queue(maxsize=25)
        last_opened_post: Optional[tuple[int, int]] = None

        async def _reset_status_after(sec: int, expect_status: str, new_status: str = 'WAITING'):
            await asyncio.sleep(sec)
            if stop_all.is_set():
                return
            try:
                cur = str(state.get(label, {}).get('status') or '')
            except Exception:
                cur = ''
            if cur == expect_status and watch_mode != 'old':
                set_row(label, new_status)


        # Core handler used by OLD mode (paste message link and open once).
        async def handle_message(msg_in):
            try:
                msg = msg_in
                key = (msg.chat_id, msg.id)
                t = int(time.time() * 1000)

                for k, ts in list(processed.items()):
                    if t - ts > max(dup_window, 60_000):
                        processed.pop(k, None)
                if key in processed and (t - processed[key]) < dup_window:
                    return
                processed[key] = t

                url = extract_launch_url(msg, launch_text)
                if not url and cfg.get("miniapp_link_fallback", True):
                    # Fall back to any URL found in the post. This ensures the UI reacts
                    # even when the post contains a non-standard miniapp link.
                    u_any, _src_any = extract_any_url(msg)
                    if u_any:
                        url = u_any
                if not url:
                    return

                # Accept bare 't.me/...' links (no scheme) and normalize to a real URL.
                url = normalize_telegram_link(url)

                # UI: fill ticket column from message text (best-effort)
                ticket = extract_ticket_info(getattr(msg, "raw_text", "") or getattr(msg, "message", "") or "")
                set_row(label, "GOT", ticket=ticket)
                if open_only_tg and not is_telegram_link(url):
                    # Not a Telegram deep link: treat as blocked link (red) for 2 minutes, then return to WAITING in NEW.
                    set_row(label, "BADLINK", "not tg link")
                    if watch_mode != "old":
                        _spawn_run(_reset_status_after(120, "BADLINK", "MONITORING"))
                    return

                delay_ms = choose_delay_ms(pre_spec)
                if delay_ms > 0:
                    set_row(label, "DELAY", f"{delay_ms}ms")
                    await asyncio.sleep(delay_ms / 1000)

                set_row(label, "OPENING")

                # IMPORTANT: Result detection must run on the Telegram WebView URL.
                # Even in non-headless mode (when we open the Mini App for you to see),
                # we still fetch the WebView URL and pass it to Playwright.
                play_url = url
                try:
                    wurl = await get_webview_url_for_miniapp(client, url)
                    if wurl:
                        play_url = wurl
                    else:
                        set_row(label, "ERROR", "no webview url")
                        return
                except Exception as e:
                    set_row(label, "ERROR", f"webview {type(e).__name__}")
                    return
                # In headed mode, Playwright will open Chromium with a visible window.

                # keep OPENING visible while Playwright starts; waiting is handled implicitly by detection
                result_timeout_ms = int(cfg.get("result_timeout_ms", 15000))
                result_poll_ms = int(cfg.get("result_poll_ms", 500))
                success_patterns = cfg.get("success_patterns", ["you got", "ticket"])
                fail_patterns = cfg.get("fail_patterns", ["this offer has expired"])
                # In OLD + headed mode, keep Chromium open until you exit back to the menu.
                pw_keep = None
                ctx_keep = None
                if watch_mode == "old" and (not headless_mode):
                    res, detail, pw_keep, ctx_keep = await detect_result_playwright_keep_open(
                        play_url, cfg, result_timeout_ms, result_poll_ms,
                        success_patterns, fail_patterns,
                        profile_dir=profile_dir,
                        proxy=proxy,
                        headless=False
                    )
                    account["_pw_keep"] = pw_keep
                    account["_ctx_keep"] = ctx_keep
                else:
                    if warm_session is not None:
                        res, detail = await detect_result_via_warm_session(
                            warm_session,
                            play_url, cfg, result_timeout_ms, result_poll_ms,
                            success_patterns, fail_patterns,
                        )
                    else:
                        res, detail = await detect_result_via_playwright(
                            play_url, cfg, result_timeout_ms, result_poll_ms,
                            success_patterns, fail_patterns,
                            profile_dir_override=profile_dir,
                            proxy=proxy,
                            headless=headless_mode
                        )



                if res == "success":
                    set_row(label, "SUCCESS", detail)
                    await bump_gotem()
                    try:
                        if webhook_enabled():
                            await webhook_send_async(f"✅ SUCCESS ({label}): {detail}")
                    except Exception:
                        pass
                elif res == "missed":
                    # Page loaded but already claimed.
                    set_row(label, "MISSED", detail)
                elif res == "fail":
                    set_row(label, "FAIL", detail)
                elif res == "timeout":
                    set_row(label, "TIMEOUT", detail)
                elif res == "skip":
                    # blocked-domain skip should be a red, sticky status for ~2 minutes in NEW
                    if isinstance(detail, str) and ("blocked domain=" in detail or "blocked domain" in detail):
                        set_row(label, "BADLINK", detail)
                        if watch_mode != "old":
                            _spawn_run(_reset_status_after(120, "BADLINK", "MONITORING"))
                    else:
                        set_row(label, "SKIP", detail)
                elif res == "user_stop":
                    set_row(label, "STOPPED", detail or "browser closed")
                    _request_stop("pause")
                elif res == "error":
                    msg = str(detail or "")
                    if proxy and _looks_like_proxy_issue(msg):
                        set_row(label, "PROXY_WEBR", _short_proxy_hint(msg) or "ERROR")
                    else:
                        set_row(label, "ERROR", detail)
                else:
                    set_row(label, "ERROR", detail)

                # cooldown removed

                # In NEW we keep the last result visible until a newer post arrives.
                # This matches OLD behavior (final status stays on screen).
            except Exception as e:
                set_row(label, "ERROR", f"{type(e).__name__}: {e}")

        # Fanout OPEN handler (FINAL ARCH): receives resolved miniapp/launch URL
        # from the shared coordinator. Each account resolves its own WebView URL
        # (account-specific) and opens it in its warm browser.
        _last_open_key: Optional[tuple[int, int]] = None

        async def handle_open(url: str, ticket: str, post_key: tuple[int, int]):
            nonlocal _last_open_key
            try:
                if stop_all.is_set():
                    return
                if _last_open_key == post_key:
                    return
                _last_open_key = post_key

                if not url:
                    return

                # Accept bare 't.me/...' links (no scheme) and normalize to a real URL.
                url = normalize_telegram_link(url)

                # UI ticket best-effort
                if ticket:
                    set_row(label, state.get(label, {}).get("status") or "MONITORING", ticket=ticket)

                if open_only_tg and not is_telegram_link(url):
                    set_row(label, "BADLINK", "not tg link")
                    if watch_mode != "old":
                        _spawn_run(_reset_status_after(120, "BADLINK", "MONITORING"))
                    return

                delay_ms = choose_delay_ms(pre_spec)
                if delay_ms > 0:
                    set_row(label, "DELAY", f"{delay_ms}ms", ticket=ticket or "")
                    await asyncio.sleep(delay_ms / 1000)

                set_row(label, "OPENING", ticket=ticket or "")

                play_url = url
                try:
                    wurl = await get_webview_url_for_miniapp(client, url)
                    if wurl:
                        play_url = wurl
                    else:
                        set_row(label, "ERROR", "no webview url", ticket=ticket or "")
                        return
                except Exception as e:
                    set_row(label, "ERROR", f"webview {type(e).__name__}", ticket=ticket or "")
                    return

                result_timeout_ms = int(cfg.get("result_timeout_ms", 15000))
                result_poll_ms = int(cfg.get("result_poll_ms", 500))
                success_patterns = cfg.get("success_patterns", ["you got", "ticket"])
                fail_patterns = cfg.get("fail_patterns", ["this offer has expired"])

                if warm_session is not None:
                    res, detail = await detect_result_via_warm_session(
                        warm_session,
                        play_url, cfg, result_timeout_ms, result_poll_ms,
                        success_patterns, fail_patterns,
                    )
                else:
                    res, detail = await detect_result_via_playwright(
                        play_url, cfg, result_timeout_ms, result_poll_ms,
                        success_patterns, fail_patterns,
                        profile_dir_override=profile_dir,
                        proxy=proxy,
                        headless=headless_mode,
                    )

                if res == "success":
                    set_row(label, "SUCCESS", detail, ticket=ticket or "")
                    await bump_gotem()
                    try:
                        if webhook_enabled():
                            await webhook_send_async(f"✅ SUCCESS ({label}): {detail}")
                    except Exception:
                        pass
                elif res == "missed":
                    set_row(label, "MISSED", detail, ticket=ticket or "")
                elif res == "fail":
                    set_row(label, "FAIL", detail, ticket=ticket or "")
                elif res == "timeout":
                    set_row(label, "TIMEOUT", detail, ticket=ticket or "")
                elif res == "skip":
                    if isinstance(detail, str) and ("blocked domain=" in detail or "blocked domain" in detail):
                        set_row(label, "BADLINK", detail, ticket=ticket or "")
                        if watch_mode != "old":
                            _spawn_run(_reset_status_after(120, "BADLINK", "MONITORING"))
                    else:
                        set_row(label, "SKIP", detail, ticket=ticket or "")
                elif res == "user_stop":
                    set_row(label, "STOPPED", detail or "browser closed", ticket=ticket or "")
                    stop_all.set()
                elif res == "error":
                    msg = str(detail or "")
                    if proxy and _looks_like_proxy_issue(msg):
                        set_row(label, "PROXY_WEBR", _short_proxy_hint(msg) or "ERROR", ticket=ticket or "")
                    else:
                        set_row(label, "ERROR", detail, ticket=ticket or "")
                else:
                    set_row(label, "ERROR", detail, ticket=ticket or "")

            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                if proxy and _looks_like_proxy_issue(msg):
                    set_row(label, "PROXY_WEBR", _short_proxy_hint(msg) or type(e).__name__, ticket=ticket or "")
                else:
                    set_row(label, "ERROR", msg, ticket=ticket or "")

        async def worker_loop():
            while not stop_all.is_set():
                try:
                    msg_item = await msg_q.get()
                except asyncio.CancelledError:
                    break
                try:
                    await handle_message(msg_item)
                except Exception:
                    # handle_message already reports errors to UI
                    pass
                finally:
                    try:
                        msg_q.task_done()
                    except Exception:
                        pass

        worker_t: Optional[asyncio.Task] = None
        open_t: Optional[asyncio.Task] = None

        async def open_worker_loop():
            while not stop_all.is_set():
                try:
                    item = await open_q.get()
                except asyncio.CancelledError:
                    break
                try:
                    u, tk, pkey = item
                    await handle_open(u, tk, pkey)
                except Exception:
                    pass
                finally:
                    try:
                        open_q.task_done()
                    except Exception:
                        pass

        try:
            set_row(label, "LOGIN")
            tg_proxy = account.get("tg_proxy")
            client = TelegramClient(str(session_file), api_id, api_hash, proxy=tg_proxy)

            # Connect with a hard timeout + retry, so one bad proxy state doesn't kill the whole run.
            connect_timeout = float(cfg.get("tg_connect_timeout_sec", 15.0) or 15.0)
            backoff = 2.0
            while not stop_all.is_set():
                try:
                    await asyncio.wait_for(client.connect(), timeout=connect_timeout)
                    break
                except Exception as e:
                    # Don't leak proxy creds; keep message short.
                    msg = f"{type(e).__name__}: {e}"
                    if tg_proxy and _looks_like_proxy_issue(msg):
                        set_row(label, "PROXY_TGR", _short_proxy_hint(msg) or type(e).__name__)
                    else:
                        set_row(label, "ERROR", f"connect fail: {type(e).__name__}")
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    # Recreate client to avoid stuck sockets
                    try:
                        client = TelegramClient(str(session_file), api_id, api_hash, proxy=tg_proxy)
                    except Exception:
                        pass
                    await asyncio.sleep(min(backoff, 30.0))
                    backoff = min(backoff * 2.0, 30.0)
            if not await client.is_user_authorized():
                set_row(label, "ERROR", "not authorized (login required)")
                return

            try:
                ch_ent = await resolve_channel_entity(client, channel)
            except Exception:
                set_row(label, "ERROR", "no access to channel")
                return

            # If the account isn't in the channel/group, try joining it once.
            try:
                ok = await ensure_joined(client, ch_ent, label)
            except Exception:
                ok = True
            if not ok:
                return

            # Warm browser: in headless runs, prestart Chromium so that on the first
            # post we don't pay launch cost (and the OPENING -> SUCCESS transition is snappier).
            try:
                warm_session = warm_cache.get(label)
                if warm_session is None:
                    storage_mode = str(cfg.get("storage_state_mode", "off") or "off").strip().lower()
                    storage_state_path = None
                    if storage_mode in ("use", "capture"):
                        raw_sp = str(cfg.get("storage_state_path", "") or "").strip()
                        if raw_sp:
                            pth = Path(raw_sp).expanduser()
                            if not pth.is_absolute():
                                # relative paths resolve inside DATA_DIR
                                pth = (DATA_DIR / raw_sp).resolve()
                        else:
                            pth = (DATA_DIR / "storage_state.json").resolve()
                        storage_state_path = pth

                    goto_wait_until = str(cfg.get("goto_wait_until", "commit") or "commit").strip() or "commit"
                    warm_session = WarmBrowserSession(
                        profile_dir=profile_dir,
                        proxy=proxy,
                        headless=bool(headless_mode),
                        wait_until=goto_wait_until,
                        storage_state_mode=storage_mode,
                        storage_state_path=storage_state_path,
                    )
                    warm_cache[label] = warm_session
                if headless_mode and warm_session is not None:
                    _spawn_run(warm_session.start())
            except Exception:
                warm_session = None

            # Start per-account workers.
            if watch_mode == "old":
                worker_t = asyncio.create_task(worker_loop())
            else:
                open_t = asyncio.create_task(open_worker_loop())

            # Register runtime for shared bus/poll/keepalive (NEW architecture).
            try:
                runtimes[label] = {
                    "client": client,
                    "ch_ent": ch_ent,
                    "open_q": open_q,
                    "warm_session": warm_session,
                }
                ev = runtimes_ready.get(label)
                if ev is not None:
                    ev.set()
            except Exception:
                pass


            if watch_mode == "old":
                ch_ref, msg_id = parse_message_link(old_link)
                if not (ch_ref and msg_id):
                    set_row(label, "ERROR", "bad link")
                    return
                if str(ch_ref).startswith("@"):
                    peer = ch_ref
                else:
                    internal = int(str(ch_ref).split("/", 1)[1])
                    peer = int(f"-100{internal}")
                m = await client.get_messages(peer, ids=msg_id)
                if not m:
                    set_row(label, "ERROR", "msg not found")
                    return
                # One-shot enqueue, then keep the watch screen alive.
                # User will decide when to exit back to the main menu.
                await msg_q.put(m)
                # Wait until it is processed once.
                try:
                    await msg_q.join()
                except Exception:
                    pass
                while not stop_all.is_set():
                    await asyncio.sleep(0.5)
                return

            # LIVE monitor only if mode includes LIVE.
            if monitor_mode in ("live_only", "live+poll"):
                @client.on(events.NewMessage(chats=ch_ent))
                async def handler(event):
                    if stop_all.is_set():
                        return
                    try:
                        # Warm browser ASAP.
                        try:
                            if warm_session is not None:
                                _spawn_run(warm_session.start())
                        except Exception:
                            pass
                        # FINAL ARCH: emit shared POST_FOUND (deduped globally)
                        _spawn_run(emit_post_found(label, event.message))
                    except Exception:
                        pass

            set_row(label, "MONITORING")
            while not stop_all.is_set():
                await asyncio.sleep(0.5)

        except Exception as e:
            set_row(label, "ERROR", f"{type(e).__name__}: {e}")

        finally:
            try:
                if worker_t is not None:
                    worker_t.cancel()
            except Exception:
                pass
            try:
                if open_t is not None:
                    open_t.cancel()
            except Exception:
                pass
            try:
                if client:
                    await client.disconnect()
            except Exception:
                pass

            try:
                runtimes.pop(label, None)
            except Exception:
                pass

            if stop_reason.get("mode") == "quit":
                # Close any kept Chromium (OLD headed keep-open mode) when stopping.
                try:
                    ctx_keep = account.get("_ctx_keep")
                    pw_keep = account.get("_pw_keep")
                    if ctx_keep is not None:
                        await ctx_keep.close()
                    if pw_keep is not None:
                        await pw_keep.stop()
                except Exception:
                    pass

                # Close warm Chromium session (NEW/headless performance path).
                try:
                    if 'warm_session' in locals() and warm_session is not None:
                        await warm_session.close()
                    if label in warm_cache:
                        warm_cache.pop(label, None)
                except Exception:
                    pass

            if watch_mode != "old" and stop_reason.get("mode") != "quit":
                set_row(label, "STOPPED")




    async def _run_supervised(name: str, coro_factory):
        """Run a loop coroutine and restart it if it crashes."""
        while not stop_all.is_set():
            try:
                await coro_factory()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Do not print to stdout/stderr: it corrupts the full-screen UI.
                await asyncio.sleep(0.5)

    log_t = asyncio.create_task(_status_log_writer())
    render_t: Optional[asyncio.Task] = None
    input_t: Optional[asyncio.Task] = None
    ui_t: Optional[asyncio.Task] = None
    if ui_mode == "ptk":
        ui_t = asyncio.create_task(ptk_ui_loop())
    else:
        render_t = asyncio.create_task(render_loop())
        input_t = asyncio.create_task(input_loop())

    auto_t: Optional[asyncio.Task] = None
    acct_tasks: list[asyncio.Task] = []
    bus_t: Optional[asyncio.Task] = None
    poll_t: Optional[asyncio.Task] = None
    keep_t: Optional[asyncio.Task] = None

    async def _start_run() -> list[asyncio.Task]:
        nonlocal stop_all, run_extras, runtimes, runtimes_ready, post_q, seen_posts
        nonlocal auto_t, acct_tasks, bus_t, poll_t, keep_t
        stop_all = asyncio.Event()
        run_extras = set()
        runtimes = {}
        runtimes_ready = {acct_label(a): asyncio.Event() for a in accounts}
        post_q = asyncio.Queue(maxsize=200)
        seen_posts = {}
        ui_paused.clear()
        stop_reason["mode"] = "run"
        _set_all_rows(default_idle_status)
        try:
            global _POLL_OVERLAY_LABEL, _POLL_OVERLAY_UNTIL
            _POLL_OVERLAY_LABEL = None
            _POLL_OVERLAY_UNTIL = 0.0
        except Exception:
            pass

        auto_t = asyncio.create_task(auto_stop_5m())
        acct_tasks = [asyncio.create_task(watch_account(a)) for a in accounts]

        bus_t = None
        poll_t = None
        keep_t = None
        if watch_mode == "new":
            bus_t = asyncio.create_task(_run_supervised('bus', post_processor_loop))
            if monitor_mode in ("poll_only", "live+poll"):
                poll_t = asyncio.create_task(_run_supervised('poll', poll_scheduler_loop))
            if monitor_mode in ("live_only", "live+poll"):
                keep_t = asyncio.create_task(_run_supervised('keepalive', keepalive_loop))

        tasks: list[asyncio.Task] = []
        if auto_t is not None:
            tasks.append(auto_t)
        tasks.extend(acct_tasks)
        for t in (bus_t, poll_t, keep_t):
            if t is not None:
                tasks.append(t)
        return tasks

    async def _stop_run(run_tasks: list[asyncio.Task]) -> None:
        try:
            if stop_reason.get("mode") != "quit":
                stop_reason["mode"] = "pause"
                ui_paused.set()
            stop_all.set()
        except Exception:
            pass
        for t in run_tasks:
            try:
                t.cancel()
            except Exception:
                pass
        for t in list(run_extras):
            try:
                t.cancel()
            except Exception:
                pass
        try:
            await asyncio.gather(*run_tasks, *list(run_extras), return_exceptions=True)
        except Exception:
            pass
        run_extras.clear()

        try:
            _drain_queue(post_q)
            _drain_queue(_status_log_q)
        except Exception:
            pass
        try:
            for _lb, rt in list(runtimes.items()):
                oq = rt.get("open_q")
                if oq is not None:
                    _drain_queue(oq)
        except Exception:
            pass
        try:
            _set_all_rows("STOPPED")
        except Exception:
            pass

    async def _ui_exit_watcher():
        nonlocal render_t, input_t
        ui_err = None
        ui_ended = False
        try:
            if ui_t is not None:
                await ui_t
                ui_ended = True
        except Exception as e:
            ui_err = e

        if (not quit_all.is_set()) and (ui_err is not None or ui_ended):
            # If prompt_toolkit UI crashes OR exits unexpectedly, do NOT exit to the main menu.
            # Fall back to the classic clear+redraw UI and persist until quit.
            try:
                import traceback
                logs_dir = DATA_DIR / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                msg = ""
                if ui_err is not None:
                    msg = "".join(traceback.format_exception(type(ui_err), ui_err, ui_err.__traceback__))
                else:
                    msg = "ptk ui ended unexpectedly (no exception)\n"
                (logs_dir / "ui_crash.log").write_text(msg, encoding="utf-8")
            except Exception:
                pass
            try:
                render_t = asyncio.create_task(render_loop())
                input_t = asyncio.create_task(input_loop())
                await quit_all.wait()
            finally:
                for t in (render_t, input_t):
                    if t is None:
                        continue
                    try:
                        t.cancel()
                    except Exception:
                        pass
            return

        if not quit_all.is_set():
            _request_stop("quit")

    ui_exit_t: Optional[asyncio.Task] = None
    if ui_mode == "ptk":
        ui_exit_t = asyncio.create_task(_ui_exit_watcher())

    run_tasks: list[asyncio.Task] = []
    run_active = False
    cmd_task: Optional[asyncio.Task] = None
    stop_task: Optional[asyncio.Task] = None
    quit_task: Optional[asyncio.Task] = None
    try:
        run_tasks = await _start_run()
        run_active = True
        cmd_task = asyncio.create_task(cmd_q.get())
        stop_task = asyncio.create_task(stop_all.wait())
        quit_task = asyncio.create_task(quit_all.wait())
        while not quit_all.is_set():
            if run_active:
                done, _pending = await asyncio.wait(
                    [cmd_task, stop_task, quit_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
            else:
                done, _pending = await asyncio.wait(
                    [cmd_task, quit_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

            if quit_all.is_set():
                break

            if cmd_task in done:
                try:
                    cmd_raw = cmd_task.result()
                except Exception:
                    cmd_raw = ""
                cmd_task = asyncio.create_task(cmd_q.get())
                cmd = (cmd_raw or "").strip().lower()
                if cmd in ("s", "stop"):
                    if run_active:
                        _request_stop("pause")
                        await _stop_run(run_tasks)
                        run_active = False
                        if stop_task is not None:
                            try:
                                stop_task.cancel()
                            except Exception:
                                pass
                    continue
                if cmd in ("r", "run"):
                    if not run_active:
                        run_tasks = await _start_run()
                        run_active = True
                        if stop_task is not None:
                            try:
                                stop_task.cancel()
                            except Exception:
                                pass
                        stop_task = asyncio.create_task(stop_all.wait())
                    continue
                if cmd in ("q", "quit"):
                    _request_stop("quit")
                    break

            if run_active and stop_all.is_set():
                await _stop_run(run_tasks)
                run_active = False
                if stop_task is not None:
                    try:
                        stop_task.cancel()
                    except Exception:
                        pass
    finally:
        try:
            _UI_QUIET = False
        except Exception:
            pass
        try:
            if run_active:
                _request_stop("quit")
                await _stop_run(run_tasks)
        except Exception:
            pass
        for t in (log_t, render_t, input_t):
            try:
                if t is not None:
                    t.cancel()
            except Exception:
                pass
        if ui_exit is not None:
            try:
                ui_exit()
            except Exception:
                pass
        if ui_t is not None:
            try:
                await asyncio.wait_for(ui_t, timeout=1.0)
            except Exception:
                try:
                    ui_t.cancel()
                except Exception:
                    pass
        if ui_exit_t is not None:
            try:
                ui_exit_t.cancel()
            except Exception:
                pass
        if cmd_task is not None:
            try:
                cmd_task.cancel()
            except Exception:
                pass
        if stop_task is not None:
            try:
                stop_task.cancel()
            except Exception:
                pass
        if quit_task is not None:
            try:
                quit_task.cancel()
            except Exception:
                pass
        if stop_reason.get("mode") == "quit":
            try:
                for _lb, ws in list(warm_cache.items()):
                    try:
                        await ws.close()
                    except Exception:
                        pass
                warm_cache.clear()
            except Exception:
                pass
        return
async def set_channel(cfg: dict) -> None:
    clear()
    status_info("Set channel (@channel or t.me/+INVITE)")
    ch = (await ainput("")).strip()
    if ch:
        if not ch.startswith("@") and not ch.startswith("https://"):
            ch = "@" + ch
        cfg["channel"] = ch
        save_config(cfg)

async def set_delays(cfg: dict) -> None:
    clear()
    status_info("Set delay (milliseconds)")
    status_info("")
    status_info("Pre-open delay:")
    status_info("Examples: 5000  or  3000-8000")
    pre = (await ainput("")).strip()

    try:
        if pre:
            parse_delay_spec(pre)
            cfg["pre_open_delay_ms"] = pre
        save_config(cfg)
    except Exception as e:
        status_error(f"{type(e).__name__}: {e}")
        await asyncio.sleep(2)



async def set_watch_mode(cfg: dict) -> None:
    clear()
    cur = str(cfg.get("watch_mode", "new")).strip().lower()
    if cur not in ("new", "old"):
        cur = "new"
    status_info("Set watch mode")
    status_info("")
    status_info(f"Current: {cur}")
    status_info("")
    status_info("1) new  (react on new messages)")
    status_info("2) old  (paste a message link and open once)")
    status_info("")
    choice = (await ainput("Select: ")).strip()
    if choice == "1":
        cfg["watch_mode"] = "new"
        save_config(cfg)
    elif choice == "2":
        cfg["watch_mode"] = "old"
        save_config(cfg)


def parse_message_link(link: str) -> tuple[Optional[str], Optional[int]]:
    """
    Supports:
      - https://t.me/<username>/<msg_id>
      - https://t.me/c/<internal_id>/<msg_id>
    Returns (channel_ref, msg_id) where channel_ref is '@username' or 'c/<id>' token.
    """
    try:
        s = (link or "").strip()
        if not s:
            return None, None
        # normalize
        s = s.replace("http://", "https://")
        m = re.search(r"t\.me/c/(\d+)/(\d+)", s)
        if m:
            return "c/"+m.group(1), int(m.group(2))
        m = re.search(r"t\.me/([A-Za-z0-9_]+)/(\d+)", s)
        if m:
            return "@"+m.group(1), int(m.group(2))
    except Exception:
        pass
    return None, None


def parse_miniapp_direct_link(url: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parses: https://t.me/<bot>/<short_name>?startapp=<token>
    Returns (bot_username, short_name, start_param)
    """
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


async def get_webview_url_for_miniapp(client, miniapp_url: str) -> Optional[str]:
    """
    Request the real Telegram WebView URL for a bot Mini App deep link.
    Requires Telethon version that includes messages.RequestAppWebViewRequest.
    """
    bot_username, short_name, start_param = parse_miniapp_direct_link(miniapp_url)
    if not bot_username or not short_name:
        return None

    bot_peer = await client.get_input_entity(bot_username)
    inp_app = types.InputBotAppShortName(bot_id=bot_peer, short_name=short_name)

    res = await client(functions.messages.RequestAppWebViewRequest(
        peer=bot_peer,
        app=inp_app,
        platform="macos",
        write_allowed=True,
        start_param=start_param or None
    ))
    return getattr(res, "url", None)

async def main():
    while True:
        cfg = load_config()
        global _WEBHOOK_CFG
        _WEBHOOK_CFG = cfg
        cur_mode = cfg.get("watch_mode", "new")
        clear()
        print(render_menu(cfg))
        print("")
        num_col = theme.purple_text
        txt = theme.white_text
        cur_mode_col = theme.pink_text(cur_mode) if str(cur_mode).lower() == "new" else theme.purple_text(cur_mode)
        cur = f"{theme.gray_text('(current: ')}{cur_mode_col}{theme.gray_text(')')}"
        print(theme.gray_text("Select:"))
        print(f"{num_col('1')} {txt('Start watching')}")
        print(f"{num_col('2')} {txt('Set channel')}")
        print(f"{num_col('3')} {txt('Set delays')}")
        print(f"{num_col('4')} {txt('Set mode')} {cur}")
        print(f'{num_col("5")} {txt("Reset Got\'em")}')
        print(f"{num_col('6')} {txt('Webhook')}")
        print(f"{num_col('7')} {txt('Quit')}")
        print("")
        choice = (await ainput(theme.pink_text("Select: "))).strip()

        if choice == "1":
            try:
                await watch_multi(cfg)
            except SystemExit:
                raise
            except Exception as e:
                clear()
                status_error(f"{type(e).__name__}: {e}")
                await asyncio.sleep(2)

        elif choice == "2":
            await set_channel(cfg)

        elif choice == "3":
            await set_delays(cfg)

        elif choice == "4":
            await set_watch_mode(cfg)

        elif choice == "5":
            cfg["gotem"] = 0
            save_config(cfg)
            status_info("Got'em reset to 0.")
            await asyncio.sleep(0.8)

        elif choice == "6":
            clear()
            status_info("🌐 WEBHOOK")
            status_info("Add the bot to your private channel as ADMIN and make 1 fresh post.")
            print("")
            print("1) Detect chat_id (via getUpdates)")
            print("2) Test webhook message")
            print("3) Back")
            print("")
            sub = (await ainput("Select: ")).strip()
            if sub == "1":
                ok, info = webhook_detect_chat_id(seconds=20)
                if ok:
                    status_success_msg(info)
                else:
                    status_error(info)
                await asyncio.sleep(1.5)
            elif sub == "2":
                ok, err = await webhook_send_async("📣 acrFetcher webhook test ✅")
                if ok:
                    status_success_msg("webhook sent")
                else:
                    status_error(err)
                await asyncio.sleep(1.5)
            else:
                pass

        elif choice == "7":
            break

def run_cli() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        clear()
        print("Bye")


if __name__ == "__main__":
    run_cli()
