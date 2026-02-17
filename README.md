# acrFetcher

Terminal multi-account watcher for Telegram mini-app links.

## Run

- macOS: `./RUN.command`
- Linux/server: `./RUN.sh`

`RUN.command` is tuned for local macOS Terminal window size.
`RUN.sh` is tuned for server/headless usage and defaults data to local `.acr_data` unless `ACRFETCHER_DATA_DIR` is set.

## Data location (`DATA_DIR`)

Runtime data is stored in `DATA_DIR` (config, sessions, logs, browser profile):

- By default:
  - macOS: `~/Library/Application Support/acrFetcher`
  - Linux: `~/.local/share/acrFetcher`
- Override with env: `ACRFETCHER_DATA_DIR=/path`

## Security rules (important)

Never publish to GitHub:

- Telegram sessions (`sessions/`, `*.session*`)
- local config with secrets (`config.json`)
- private accounts/proxies

Repository policy:

- `accounts.csv` in repo is public-safe template only.
- real private account list stays local only.
- use `accounts.example.csv` as clean starter template.

## Logs

All runtime logs should go to:

- `DATA_DIR/logs`

## Main statuses

- `MONITORING`, `POLL`, `OPENING`
- `SUCCESS`, `MISSED`, `FAIL`, `TIMEOUT`
- `PROXY_TGR`, `PROXY_WEBR`, `ERROR`
- `STOPPED`
