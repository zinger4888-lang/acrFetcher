from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(slots=True)
class AccountRecord:
    phone: str
    email: str
    proxy_raw: str
    proxy: Optional[dict]
    tg_proxy: Any


@dataclass(slots=True)
class RowState:
    phone: str
    proxy: str
    status: str
    detail: str = ""
    ticket: str = "-"


@dataclass(slots=True)
class UiEvent:
    kind: str
    label: str
    status: str
    detail: str = ""
    ticket: str = ""
    ts_ms: int = 0


@dataclass(slots=True)
class AppConfigModel:
    """Typed runtime config model.

    NOTE: Public dataclass is defined in config_store as AppConfig.
    This base model exists to keep type sharing across modules.
    """

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
    extras: dict[str, Any] = field(default_factory=dict)
