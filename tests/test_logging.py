from __future__ import annotations

import json
import logging

from superseded.logging_utils import JsonFormatter, setup_logging


def test_setup_logging_text_writes_to_stderr(capsys):
    logging.getLogger().handlers.clear()
    setup_logging("text", "INFO")
    logging.getLogger("superseded.test").info("hello")
    captured = capsys.readouterr()
    assert "hello" in captured.err
    assert "INFO" in captured.err


def test_setup_logging_json_emits_json_line(capsys):
    logging.getLogger().handlers.clear()
    setup_logging("json", "INFO")
    logging.getLogger("superseded.test").info("structured")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "structured"
    assert payload["level"] == "INFO"
    assert "time" in payload


def test_setup_logging_is_idempotent():
    logging.getLogger().handlers.clear()
    setup_logging("text", "INFO")
    first = len(logging.getLogger().handlers)
    setup_logging("json", "WARNING")
    second = len(logging.getLogger().handlers)
    assert first == 1
    assert second == 1


def test_setup_logging_default_level_silences_info(capsys):
    logging.getLogger().handlers.clear()
    setup_logging("text")
    logging.getLogger("superseded.test").info("quiet")
    logging.getLogger("superseded.test").warning("loud")
    err = capsys.readouterr().err
    assert "quiet" not in err
    assert "loud" in err


def test_json_formatter_includes_extra_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="msg",
        args=(),
        exc_info=None,
    )
    record.request_id = "abc"
    out = formatter.format(record)
    payload = json.loads(out)
    assert payload["event"] == "msg"
    assert payload["request_id"] == "abc"


def test_json_formatter_serializes_exc_info():
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="x",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(formatter.format(record))
    assert "ValueError" in payload["exc_info"]
    assert "boom" in payload["exc_info"]
