from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from .config import AgentPaths, ensure_dirs


def configure_logging(paths: AgentPaths) -> logging.Logger:
    ensure_dirs(paths)
    logger = logging.getLogger("iru_agent")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        paths.log_path,
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if sys.stdout and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    logger.info("Logging initialized: %s", paths.log_path)
    return logger


def tail_log(log_path: Path, max_lines: int = 120) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""

