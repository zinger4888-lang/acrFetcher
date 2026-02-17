from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


APP_NAME = "acrFetcher"

DEFAULT_CONFIG: dict[str, Any] = {
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

LEGACY_KEY_MAP: dict[str, str] = {
    "push_enabled": "webhook_enabled",
    "push_bot_token": "webhook_bot_token",
    "push_channel_invite": "webhook_channel_invite",
    "push_chat_id": "webhook_chat_id",
    "push_on_error": "webhook_on_error",
    "push_updates_offset": "webhook_updates_offset",
}


@dataclass(slots=True)
class AppConfig:
    api_id: int
    api_hash: str
    channel: str
    launch_button_text: str
    pre_open_delay_ms: str
    duplicate_window_ms: int
    open_only_telegram_links: bool
    force_open_in_telegram_app: bool
    gotem: int
    result_timeout_ms: int
    result_poll_ms: int
    success_patterns: list[str]
    fail_patterns: list[str]
    watch_mode: str
    ui_mode: str
    monitor_mode: str
    poll_interval_sec: float
    keepalive_interval_sec: float
    event_dedup_ttl_sec: int
    miniapp_link_fallback: bool
    headless_mode: bool
    browser_profile_dir: str
    browser_first_login_headed: bool
    accounts_csv: str
    webhook_enabled: bool
    webhook_bot_token: str
    webhook_chat_id: str
    webhook_on_error: bool
    storage_state_mode: str
    storage_state_path: str
    extras: dict[str, Any] = field(default_factory=dict, repr=False)


def _coerce_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v or "").strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return bool(default)


def _coerce_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _coerce_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _coerce_str_list(v: Any, default: list[str]) -> list[str]:
    if isinstance(v, list):
        out = []
        for x in v:
            sx = str(x or "").strip()
            if sx:
                out.append(sx)
        return out or list(default)
    return list(default)


def _default_data_dir() -> Path:
    return Path.home() / "Desktop" / APP_NAME


def resolve_data_dir() -> Path:
    env_raw = str(os.environ.get("ACRFETCHER_DATA_DIR", "") or "").strip()
    if env_raw:
        p = Path(env_raw).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        return p
    return _default_data_dir()


def migrate_legacy_keys(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False
    if not isinstance(raw, dict):
        return {}, True
    out = dict(raw)
    for old, new in LEGACY_KEY_MAP.items():
        if old in out and new not in out:
            out[new] = out.get(old)
            changed = True
    for old in LEGACY_KEY_MAP:
        if old in out:
            out.pop(old, None)
            changed = True
    return out, changed


def _from_dict(raw: dict[str, Any]) -> AppConfig:
    merged = dict(DEFAULT_CONFIG)
    merged.update(raw)
    extras = {k: v for k, v in raw.items() if k not in DEFAULT_CONFIG}
    return AppConfig(
        api_id=_coerce_int(merged.get("api_id"), int(DEFAULT_CONFIG["api_id"])),
        api_hash=str(merged.get("api_hash") or ""),
        channel=str(merged.get("channel") or ""),
        launch_button_text=str(merged.get("launch_button_text") or "Launch"),
        pre_open_delay_ms=str(merged.get("pre_open_delay_ms") or "0"),
        duplicate_window_ms=_coerce_int(merged.get("duplicate_window_ms"), 60000),
        open_only_telegram_links=_coerce_bool(merged.get("open_only_telegram_links"), True),
        force_open_in_telegram_app=_coerce_bool(merged.get("force_open_in_telegram_app"), True),
        gotem=_coerce_int(merged.get("gotem"), 0),
        result_timeout_ms=_coerce_int(merged.get("result_timeout_ms"), 15000),
        result_poll_ms=_coerce_int(merged.get("result_poll_ms"), 500),
        success_patterns=_coerce_str_list(merged.get("success_patterns"), list(DEFAULT_CONFIG["success_patterns"])),
        fail_patterns=_coerce_str_list(merged.get("fail_patterns"), list(DEFAULT_CONFIG["fail_patterns"])),
        watch_mode=str(merged.get("watch_mode") or "new"),
        ui_mode=str(merged.get("ui_mode") or "ptk"),
        monitor_mode=str(merged.get("monitor_mode") or "live+poll"),
        poll_interval_sec=_coerce_float(merged.get("poll_interval_sec"), 1.0),
        keepalive_interval_sec=_coerce_float(merged.get("keepalive_interval_sec"), 1.0),
        event_dedup_ttl_sec=_coerce_int(merged.get("event_dedup_ttl_sec"), 1800),
        miniapp_link_fallback=_coerce_bool(merged.get("miniapp_link_fallback"), True),
        headless_mode=_coerce_bool(merged.get("headless_mode"), False),
        browser_profile_dir=str(merged.get("browser_profile_dir") or ""),
        browser_first_login_headed=_coerce_bool(merged.get("browser_first_login_headed"), True),
        accounts_csv=str(merged.get("accounts_csv") or ""),
        webhook_enabled=_coerce_bool(merged.get("webhook_enabled"), True),
        webhook_bot_token=str(merged.get("webhook_bot_token") or ""),
        webhook_chat_id=str(merged.get("webhook_chat_id") or ""),
        webhook_on_error=_coerce_bool(merged.get("webhook_on_error"), False),
        storage_state_mode=str(merged.get("storage_state_mode") or "off"),
        storage_state_path=str(merged.get("storage_state_path") or ""),
        extras=extras,
    )


def cfg_to_dict(cfg: AppConfig) -> dict[str, Any]:
    out = dict(DEFAULT_CONFIG)
    out.update(
        {
            "api_id": int(cfg.api_id),
            "api_hash": str(cfg.api_hash),
            "channel": str(cfg.channel),
            "launch_button_text": str(cfg.launch_button_text),
            "pre_open_delay_ms": str(cfg.pre_open_delay_ms),
            "duplicate_window_ms": int(cfg.duplicate_window_ms),
            "open_only_telegram_links": bool(cfg.open_only_telegram_links),
            "force_open_in_telegram_app": bool(cfg.force_open_in_telegram_app),
            "gotem": int(cfg.gotem),
            "result_timeout_ms": int(cfg.result_timeout_ms),
            "result_poll_ms": int(cfg.result_poll_ms),
            "success_patterns": list(cfg.success_patterns),
            "fail_patterns": list(cfg.fail_patterns),
            "watch_mode": str(cfg.watch_mode),
            "ui_mode": str(cfg.ui_mode),
            "monitor_mode": str(cfg.monitor_mode),
            "poll_interval_sec": float(cfg.poll_interval_sec),
            "keepalive_interval_sec": float(cfg.keepalive_interval_sec),
            "event_dedup_ttl_sec": int(cfg.event_dedup_ttl_sec),
            "miniapp_link_fallback": bool(cfg.miniapp_link_fallback),
            "headless_mode": bool(cfg.headless_mode),
            "browser_profile_dir": str(cfg.browser_profile_dir),
            "browser_first_login_headed": bool(cfg.browser_first_login_headed),
            "accounts_csv": str(cfg.accounts_csv),
            "webhook_enabled": bool(cfg.webhook_enabled),
            "webhook_bot_token": str(cfg.webhook_bot_token),
            "webhook_chat_id": str(cfg.webhook_chat_id),
            "webhook_on_error": bool(cfg.webhook_on_error),
            "storage_state_mode": str(cfg.storage_state_mode),
            "storage_state_path": str(cfg.storage_state_path),
        }
    )
    out.update(dict(cfg.extras or {}))
    return out


def load_config(path: Path) -> AppConfig:
    path.parent.mkdir(parents=True, exist_ok=True)
    if (not path.exists()) or path.stat().st_size == 0:
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(raw, dict):
        raw = {}
    migrated, changed = migrate_legacy_keys(raw)
    cfg = _from_dict(migrated)
    if changed:
        save_config(path, cfg)
    return cfg


def save_config(path: Path, cfg: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg_to_dict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
