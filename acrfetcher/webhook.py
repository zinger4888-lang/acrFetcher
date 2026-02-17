from __future__ import annotations

import asyncio
import json
import ssl
import time
import urllib.parse
import urllib.request
from typing import Any


def webhook_enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("webhook_enabled", False)) and bool(str(cfg.get("webhook_bot_token", "")).strip())


def http_ssl_context():
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return None


def webhook_api_base(cfg: dict[str, Any]) -> str:
    token = str(cfg.get("webhook_bot_token", "")).strip()
    return f"https://api.telegram.org/bot{token}"


def webhook_send(text: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    try:
        chat_id = cfg.get("webhook_chat_id", "")
        if chat_id in (None, ""):
            return (False, "webhook_chat_id is empty (run Webhook setup)")
        url = webhook_api_base(cfg) + "/sendMessage"
        payload = {
            "chat_id": str(chat_id),
            "text": text,
            "disable_web_page_preview": True,
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10, context=http_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        try:
            body = json.loads(raw or "{}")
        except Exception:
            body = {}
        if body.get("ok") is True:
            return (True, "")
        return (False, str(body.get("description") or raw)[:200])
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")


async def webhook_send_async(text: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    try:
        return await asyncio.to_thread(webhook_send, text, cfg)
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")


def webhook_delete_webhook(cfg: dict[str, Any], drop_pending_updates: bool = True) -> tuple[bool, str]:
    try:
        if not str(cfg.get("webhook_bot_token", "")).strip():
            return (False, "missing bot token")
        q = {}
        if drop_pending_updates:
            q["drop_pending_updates"] = "true"
        url = webhook_api_base(cfg) + "/deleteWebhook"
        if q:
            url += "?" + urllib.parse.urlencode(q)
        with urllib.request.urlopen(url, timeout=10, context=http_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
        if data.get("ok"):
            return (True, "")
        return (False, (data.get("description") or "deleteWebhook failed")[:200])
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")


def webhook_detect_chat_id(cfg: dict[str, Any], seconds: int = 20) -> tuple[bool, str, dict[str, Any]]:
    out_cfg = dict(cfg)
    try:
        if not webhook_enabled(cfg):
            return (False, "webhook not enabled / missing token", out_cfg)

        ok, err = webhook_delete_webhook(out_cfg, drop_pending_updates=True)
        if not ok:
            return (False, f"deleteWebhook failed: {err}", out_cfg)

        deadline = time.time() + max(3, int(seconds))
        offset = int(out_cfg.get("webhook_updates_offset", 0) or 0)
        found = None

        while time.time() < deadline:
            q = {"timeout": 0, "limit": 100, "allowed_updates": json.dumps(["message", "channel_post"])}
            if offset:
                q["offset"] = offset
            url = webhook_api_base(out_cfg) + "/getUpdates?" + urllib.parse.urlencode(q)
            with urllib.request.urlopen(url, timeout=10, context=http_ssl_context()) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")

            if not data.get("ok"):
                return (False, str(data.get("description") or "getUpdates failed")[:200], out_cfg)

            results = data.get("result", []) or []
            if results:
                offset = results[-1].get("update_id", offset) + 1
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
            return (False, "no updates found yet. Post in the channel/group then try again.", out_cfg)
        out_cfg["webhook_chat_id"] = found
        out_cfg["webhook_updates_offset"] = offset
        return (True, f"detected chat_id={found}", out_cfg)
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}", out_cfg)
