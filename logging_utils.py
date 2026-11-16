"""Small logging setup with URL and credential redaction."""

import logging
import re
import traceback
from logging.handlers import RotatingFileHandler

from config import get_user_data_dir


_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:[a-z0-9_.-]*(?:token|secret|signature|sig|auth|key|cookie|session|password|passwd)[a-z0-9_.-]*|refresh_token|access_token|client_secret|api_key|lsig|sparams|spc|bui|n|s|expire|expires)=)[^&#\s]+"
)
_SECRET_HEADER = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def redact_sensitive_text(value: str) -> str:
    value = _SECRET_QUERY.sub(r"\1[REDACTED]", str(value))
    value = _SECRET_HEADER.sub(r"\1[REDACTED]", value)
    return _BEARER.sub("Bearer [REDACTED]", value)


class CredentialRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        if record.exc_info:
            message += "\n" + "".join(traceback.format_exception(*record.exc_info))
            record.exc_info = None
        record.msg = redact_sensitive_text(message)
        record.args = ()
        return True


def configure_application_logging(level: str = "INFO") -> str:
    """Configure a bounded app log in user data and return its path."""
    log_dir = get_user_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    if not any(getattr(handler, "_video_manager_handler", False) for handler in root.handlers):
        handler = RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
        handler.addFilter(CredentialRedactionFilter())
        handler._video_manager_handler = True
        root.addHandler(handler)
    return str(log_path)
