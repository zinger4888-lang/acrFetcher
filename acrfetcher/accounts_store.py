from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from .models import AccountRecord

try:
    import socks  # type: ignore
except Exception:
    socks = None


def parse_http_proxy(spec: str) -> Optional[dict]:
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


def parse_telethon_http_proxy(spec: str):
    s = (spec or "").strip()
    if not s:
        return None
    parts = s.split(":")
    if len(parts) < 2:
        return None
    host = parts[0].strip()
    port_s = parts[1].strip()
    if not host or not port_s.isdigit():
        return None
    port = int(port_s)

    user = ""
    pwd = ""
    if len(parts) >= 4:
        user = parts[2]
        pwd = ":".join(parts[3:])

    try:
        proxy_type = getattr(socks, "HTTP", None) if socks else None
        if proxy_type is None:
            proxy_type = "http"
        if user and pwd:
            return (proxy_type, host, port, True, user, pwd)
        return (proxy_type, host, port, True)
    except Exception:
        return None


def load_accounts_csv(csv_path: Path) -> list[AccountRecord]:
    if csv_path.is_dir():
        csv_path = csv_path / "accounts.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"accounts.csv not found: {csv_path}")

    rows: list[AccountRecord] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            phone = (r.get("phone") or r.get("Phone") or "").strip()
            email = (r.get("email") or r.get("Email") or "").strip()
            proxy_raw = (r.get("proxy") or r.get("Proxy") or "").strip()
            if not phone:
                continue
            rows.append(
                AccountRecord(
                    phone=phone,
                    email=email or phone,
                    proxy_raw=proxy_raw,
                    proxy=parse_http_proxy(proxy_raw),
                    tg_proxy=parse_telethon_http_proxy(proxy_raw),
                )
            )
    if not rows:
        raise ValueError("accounts.csv has no valid rows (need at least one with phone).")
    return rows


def acct_label(account: AccountRecord | dict) -> str:
    if isinstance(account, AccountRecord):
        return account.email or account.phone or "account"
    return str(account.get("email") or account.get("phone") or "account")
