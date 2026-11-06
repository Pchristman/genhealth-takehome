import json
import logging
import sys

from app.logging_config import JsonFormatter


def record(exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("app", logging.INFO, __file__, 1, "event", None, exc_info)


def test_log_drops_unknown_fields():
    rec = record()
    rec.patient_name = "Jane Doe"
    rec.status_code = 200
    entry = json.loads(JsonFormatter().format(rec))
    assert entry["status_code"] == 200
    assert "Jane Doe" not in json.dumps(entry)


def test_exception_log_has_no_message_or_source_lines():
    try:
        raise ValueError("Jane Doe")
    except ValueError:
        exc_info = sys.exc_info()
    output = JsonFormatter().format(record(exc_info))
    entry = json.loads(output)
    assert entry["exc_type"] == "ValueError"
    assert entry["stack"]
    assert "Jane Doe" not in output
    assert "raise ValueError" not in output
