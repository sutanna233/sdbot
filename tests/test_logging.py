"""Tests for the logging system (logging_setup.py)."""

import logging
import os
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from logging_setup import setup_logging, get_logger, ROOT_NAME


def _cleanup_sdbot_loggers():
    """Remove and close all handlers on the sdbot root logger."""
    root = logging.getLogger(ROOT_NAME)
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass


class LoggingSetupTests(TestCase):

    def setUp(self):
        _cleanup_sdbot_loggers()

    def tearDown(self):
        _cleanup_sdbot_loggers()

    def test_get_logger_returns_sdbot_logger(self):
        logger = get_logger("llm")
        self.assertEqual(logger.name, "sdbot.llm")

    def test_get_logger_idempotent(self):
        l1 = get_logger("test")
        l2 = get_logger("sdbot.test")
        self.assertIs(l1, l2)

    def test_setup_logging_enabled_creates_handlers(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "logging": {
                    "enabled": True,
                    "level": "DEBUG",
                    "console_level": "WARNING",
                    "dir": tmp,
                    "max_bytes": 1048576,
                    "backup_count": 3,
                }
            }
            root = setup_logging(config)
            self.assertEqual(root.name, ROOT_NAME)
            handler_types = [type(h).__name__ for h in root.handlers]
            self.assertIn("RotatingFileHandler", handler_types)
            self.assertIn("StreamHandler", handler_types)
            _cleanup_sdbot_loggers()

    def test_setup_logging_disabled_has_null_handler(self):
        config = {"logging": {"enabled": False}}
        root = setup_logging(config)
        self.assertTrue(any(isinstance(h, logging.NullHandler) for h in root.handlers))

    def test_setup_logging_no_config_uses_defaults(self):
        root = setup_logging({})
        self.assertEqual(root.name, ROOT_NAME)

    def test_logger_writes_to_file(self):
        tmp = tempfile.mkdtemp()
        try:
            config = {
                "logging": {
                    "enabled": True,
                    "level": "DEBUG",
                    "console_level": "WARNING",
                    "dir": tmp,
                }
            }
            setup_logging(config)
            logger = get_logger("test")
            logger.info("hello world from test")
            # Close handlers before reading
            _cleanup_sdbot_loggers()
            log_path = Path(tmp) / "sdbot.log"
            self.assertTrue(log_path.exists())
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("hello world from test", content)
            self.assertIn("sdbot.test", content)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_console_level_suppresses_lower_levels(self):
        root = logging.getLogger(ROOT_NAME)
        handler = logging.StreamHandler()
        handler.setLevel(logging.WARNING)
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        logger = get_logger("console_test")
        logger.info("this should be suppressed")
        logger.warning("this should pass")

    def test_relative_log_dir_resolves_to_script_dir(self):
        config = {
            "logging": {
                "enabled": True,
                "level": "DEBUG",
                "dir": "./test_logs_tmp",
            }
        }
        root = setup_logging(config)
        self.assertIsNotNone(root)
        _cleanup_sdbot_loggers()
        log_dir = Path(__file__).parent.parent / "test_logs_tmp"
        if log_dir.exists():
            shutil.rmtree(log_dir, ignore_errors=True)
