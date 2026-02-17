# acrFetcher

Terminal multi-account watcher for Telegram mini-app links.

## Run

- macOS: `./RUN.command`
- Linux/server: `./scripts/RUN.sh`

`RUN.command` is tuned for local macOS Terminal window size.  
`scripts/RUN.sh` is tuned for server/headless usage and defaults data to local `.acr_data` unless `ACRFETCHER_DATA_DIR` is set.

## Architecture map

- Entry point:
  - `acrFetcher.py` (thin compatibility launcher)
  - `acrfetcher/main.py` (runtime/menu/watch implementation)
- Core modules:
  - `acrfetcher/config_store.py` (typed config + migrations)
  - `acrfetcher/accounts_store.py` (accounts CSV parsing + proxy parsing)
  - `acrfetcher/status_codes.py` (status enum + labels)
  - `acrfetcher/webhook.py` (sync/async webhook calls)
  - `acrfetcher/detector.py` (result text classification helpers)
  - `acrfetcher/watch_runtime.py` (TaskGroup-oriented lifecycle controller)
  - `acrfetcher/ui_watch.py` (UI event reducer model)
  - `acrfetcher/telegram_runtime.py` (channel resolving helpers)
  - `acrfetcher/logging_setup.py` (runtime logging to file-only)
  - `acrfetcher/utils.py` (shared parsing/format helpers)
  - `acrfetcher/models.py` (typed dataclasses)

## Data location (`DATA_DIR`)

Runtime data is stored in `DATA_DIR` (config, sessions, logs, browser profile):

- By default:
  - all platforms: `~/Desktop/acrFetcher`
- Override with env: `ACRFETCHER_DATA_DIR=/path`
- Note: `scripts/RUN.sh` still sets `ACRFETCHER_DATA_DIR` to local `.acr_data` unless you override it.

## Monitoring modes

- `watch_mode`:
  - `new`: continuous multi-account watch mode (shared bus + dedupe + per-account open workers).
  - `old`: one-shot link mode (you paste a Telegram message link, each account processes it once).
- `monitor_mode` (used only when `watch_mode=new`):
  - `live_only`: only Telegram `events.NewMessage` stream + keepalive.
  - `poll_only`: only staggered polling (`get_messages(limit=1)`) + shared post processor.
  - `live+poll`: both live stream and polling enabled.
- Poll cadence behavior:
  - polling is round-robin (one account per tick);
  - effective `poll_interval_sec` is auto-throttled so one account is not polled more often than once per 10 seconds.

## Opening modes

- Link discovery order per post:
  - launch button (`launch_button_text`) from Telegram message markup;
  - mini-app URL from entities/web preview/text;
  - fallback to first URL only if `miniapp_link_fallback=true`.
- Link gate:
  - `open_only_telegram_links=true` blocks non-Telegram links as `BADLINK`.
- Browser execution:
  - `headless_mode=false`: headed Playwright/Chromium flow.
  - `headless_mode=true`: warm per-account browser session for faster OPENING handling.
  - `watch_mode=old` + `headless_mode=false`: page window is kept open until stop/quit.
- Session/state behavior:
  - `storage_state_mode`: `off` | `use` | `capture`.
  - `goto_wait_until`: Playwright navigation wait mode (default `commit`).
- `force_open_in_telegram_app` is kept for compatibility in config but is not used in runtime routing.

## Security rules (important)

Never publish to GitHub:

- Telegram sessions (`sessions/`, `*.session*`)
- local config with secrets (`config.json`)
- private accounts/proxies

Repository policy:

- real private account list stays local only (`DATA_DIR/accounts.csv` by default).
- use `examples/accounts.example.csv` as clean starter template.
- use `examples/config.example.json` as clean config template.

## Logs

All runtime logs should go to:

- `DATA_DIR/logs`

## Main statuses

- State: `MONITORING`, `POLL`, `OPENING`, `STOPPED`
- Result: `SUCCESS`, `MISSED`, `FAIL`, `TIMEOUT`
- Errors: `PROXY_TGR`, `PROXY_WEBR`, `ERROR`

Current classifier rules:

- `already claimed` style text is treated as `SUCCESS`
- `expired`, `no longer`, `not available` style text is treated as `MISSED`

## Tests

Run core unit + smoke tests:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

Quick syntax check:

```bash
python3 -m py_compile acrFetcher.py acrfetcher/main.py ui_theme.py
```

## Run/debug workflow

1. Set `channel` and private `DATA_DIR/accounts.csv` locally (or set explicit `accounts_csv` path in config).
2. Run watcher via `RUN.command` or `scripts/RUN.sh`.
3. For proxy/connect issues, inspect `DATA_DIR/logs/runtime.log`.
4. For live status transitions, inspect `DATA_DIR/logs/status_live.tsv`.

## Known recovery flows

- Auth preflight loop: ensure `.session` files exist in `DATA_DIR/sessions`.
- Proxy failures: rows should show `PROXY_TGR`/`PROXY_WEBR` without terminal spam.
- UI fallback: if prompt-toolkit crashes, app falls back to classic redraw mode.

## Developer refactor map

When making deeper changes:

1. Keep `acrFetcher.py` as compatibility launcher.
2. Keep config migration backward-compatible (`push_*` to `webhook_*`).
3. Keep command semantics unchanged: `stop`, `run`, `quit`.
4. Preserve local-only privacy rules for sessions/config/private accounts.
