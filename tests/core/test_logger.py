"""Structured fields passed as `extra=` must survive into the JSON audit line."""

from __future__ import annotations

import json
import logging

from src.core.logger import JsonFormatter, get_logger


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(JsonFormatter().format(record))


def test_extra_fields_are_kept_in_json_lines(settings):
    logger = get_logger("probe.extra")
    capture = _Capture()
    logger.logger.addHandler(capture)
    try:
        logger.warning("engine_call_failed", extra={"engine": "GEMINI", "error": "HTTP 429"})
    finally:
        logger.logger.removeHandler(capture)
    line = json.loads(capture.lines[-1])
    assert line["message"] == "engine_call_failed"
    assert line["engine"] == "GEMINI" and line["error"] == "HTTP 429"
    assert line["level"] == "WARNING" and line["logger"] == "rankuno.probe.extra"


def test_adapter_defaults_merge_with_call_site_extra(settings):
    logger = get_logger("probe.merge")
    logger.extra = {"component": "tests", "engine": "default"}
    capture = _Capture()
    logger.logger.addHandler(capture)
    try:
        logger.info("hello", extra={"engine": "override"})
        logger.info("no_extra")
    finally:
        logger.logger.removeHandler(capture)
    first, second = (json.loads(line) for line in capture.lines[-2:])
    assert first["component"] == "tests" and first["engine"] == "override"
    assert second["component"] == "tests" and second["engine"] == "default"


def test_reserved_record_attributes_in_extra_are_renamed_not_fatal(settings):
    logger = get_logger("probe.reserved")
    capture = _Capture()
    logger.logger.addHandler(capture)
    try:
        logger.info("project_created", extra={"name": "GEP", "module": "x", "project_id": "p1"})
    finally:
        logger.logger.removeHandler(capture)
    line = json.loads(capture.lines[-1])
    assert line["ctx_name"] == "GEP" and line["ctx_module"] == "x"
    assert line["project_id"] == "p1" and line["logger"] == "rankuno.probe.reserved"
