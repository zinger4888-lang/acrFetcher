from __future__ import annotations

import logging
from pathlib import Path


def configure_runtime_file_logging(data_dir: Path) -> Path | None:
    """Route runtime logs to DATA_DIR/logs/runtime.log and silence stream handlers."""
    try:
        logs_dir = data_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "runtime.log"

        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        root.addHandler(handler)
        root.setLevel(logging.INFO)

        try:
            for lg in list(logging.root.manager.loggerDict.values()):
                if isinstance(lg, logging.Logger):
                    for h in list(lg.handlers):
                        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                            try:
                                lg.removeHandler(h)
                            except Exception:
                                pass
        except Exception:
            pass

        for name in (
            "telethon",
            "telethon.network",
            "telethon.network.connection",
            "telethon.network.connection.connection",
            "python_socks",
            "socks",
        ):
            try:
                logger = logging.getLogger(name)
                logger.propagate = True
            except Exception:
                pass
        return log_path
    except Exception:
        return None
