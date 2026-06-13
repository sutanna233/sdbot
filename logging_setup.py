"""
Centralized logging configuration for sdbot.

Called once at startup from SDArtistTester.__init__().
All modules obtain loggers via get_logger("module_name").
"""

import logging
import logging.handlers
import sys
from pathlib import Path

ROOT_NAME = "sdbot"


def setup_logging(config):
    """Configure the sdbot logger hierarchy.

    Args:
        config: Full config dict (logging section read from it).

    Returns:
        The root sdbot logger.
    """
    lg = config.get("logging", {}) if config else {}
    root = logging.getLogger(ROOT_NAME)

    # Clear existing handlers to avoid duplicates on re-init
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    if not lg.get("enabled", True):
        root.addHandler(logging.NullHandler())
        return root

    level = _resolve_level(lg.get("level", "DEBUG"))
    console_level = _resolve_level(lg.get("console_level", "WARNING"))

    log_dir = Path(lg.get("dir", "./logs"))
    # Resolve relative to script dir if relative
    if not log_dir.is_absolute():
        log_dir = Path(__file__).parent / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = int(lg.get("max_bytes", 10 * 1024 * 1024))
    backup_count = int(lg.get("backup_count", 30))
    fmt = lg.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    datefmt = lg.get("date_format", "%Y-%m-%d %H:%M:%S")

    formatter = logging.Formatter(fmt, datefmt=datefmt)

    # --- File handler (RotatingFileHandler) ---
    log_path = log_dir / "sdbot.log"
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(formatter)
    root.addHandler(fh)

    # --- Console handler (higher threshold, won't clutter CLI output) ---
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(console_level)
    ch.setFormatter(formatter)
    root.addHandler(ch)

    root.info("Logging initialized: file=%s level=%s console_level=%s",
              log_path, logging.getLevelName(level), logging.getLevelName(console_level))
    return root


def get_logger(name):
    """Return a logger under the sdbot namespace.

    Usage:  logger = get_logger("llm")
            # -> logging.getLogger("sdbot.llm")
    """
    if name.startswith(ROOT_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_NAME}.{name}")


def _resolve_level(name):
    return getattr(logging, name.upper(), logging.DEBUG)
