"""
Structured JSON log formatter for production environments.

Outputs one JSON object per line, suitable for ingestion by Datadog,
CloudWatch, Splunk, or any structured logging platform.

Fields included in every record:
  timestamp, level, logger, message, module, function, line,
  request_id (if available via thread-local), exc_info
"""

import json
import logging
import traceback
from datetime import UTC, datetime


class JSONFormatter(logging.Formatter):
    """Format log records as newline-delimited JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Include exception traceback if present
        if record.exc_info:
            log_entry["exc_info"] = traceback.format_exception(*record.exc_info)

        # Include any extra fields attached to the log record
        for key, value in record.__dict__.items():
            if key not in {
                "args",
                "asctime",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "id",
                "levelname",
                "levelno",
                "lineno",
                "message",
                "module",
                "msecs",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
            }:
                try:
                    json.dumps(value)  # Only include JSON-serializable extras
                    log_entry[key] = value
                except (TypeError, ValueError):
                    log_entry[key] = str(value)

        return json.dumps(log_entry, ensure_ascii=False)
