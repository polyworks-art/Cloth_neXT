import logging
from logging.handlers import RotatingFileHandler

from cloth_next.core.logging import get_logger, initialize_logging, safe_context


def test_import_does_not_configure_handlers():
    logger = get_logger()
    logger.handlers.clear()
    assert logger.handlers == []


def test_explicit_file_initialization_is_rotating_and_idempotent(tmp_path):
    logger = initialize_logging(log_file=tmp_path / "cloth-next.log", level=logging.DEBUG)
    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], RotatingFileHandler)
    initialize_logging(log_file=tmp_path / "cloth-next.log")
    assert len(logger.handlers) == 1


def test_sensitive_context_is_redacted_and_large_values_are_bounded():
    context = safe_context({"token": "abc", "payload": b"binary", "detail": "x" * 1000})
    assert context["token"] == "<redacted>"
    assert context["payload"] == "<redacted>"
    assert len(context["detail"]) <= 512

