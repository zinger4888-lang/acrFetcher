from __future__ import annotations

import re
from typing import Optional

from telethon import TelegramClient, functions


def _strip_tg_prefix(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^\s*https?://", "", s, flags=re.I)
    s = re.sub(r"^\s*(?:t\.me|telegram\.me)/", "", s, flags=re.I)
    return s.strip()


def invite_hash_from_channel_ref(channel_ref: str) -> Optional[str]:
    s = _strip_tg_prefix(channel_ref)
    if not s:
        return None
    if s.startswith("+"):
        return s[1:].strip()
    m = re.match(r"^(?:joinchat/)([A-Za-z0-9_-]+)$", s)
    if m:
        return m.group(1)
    return None


async def resolve_channel_entity(client: TelegramClient, channel_ref: str):
    ch = (channel_ref or "").strip()
    if not ch:
        raise ValueError("empty channel")

    inv = invite_hash_from_channel_ref(ch)
    if inv:
        try:
            await client(functions.messages.ImportChatInviteRequest(inv))
        except Exception:
            pass
        try:
            chk = await client(functions.messages.CheckChatInviteRequest(inv))
            chat = getattr(chk, "chat", None)
            if chat is not None:
                return chat
        except Exception:
            pass
        return await client.get_entity(_strip_tg_prefix(ch))

    norm_ref = ch
    if re.match(r"^(?:t\.me|telegram\.me)/", norm_ref, flags=re.I) or re.match(r"^https?://", norm_ref, flags=re.I):
        norm_ref = _strip_tg_prefix(norm_ref)
    return await client.get_entity(norm_ref)
