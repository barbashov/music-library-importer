from __future__ import annotations

import hashlib
import json
import logging

_TEXT_LIMIT = 4096
_CONFIGURED = False


def configure_debug_logging(enabled: bool) -> None:
    """Configure package-local DEBUG logging once."""
    global _CONFIGURED
    if not enabled or _CONFIGURED:
        return

    logger = logging.getLogger("music_importer")
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    _CONFIGURED = True


def truncate_text(text: str, limit: int = _TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated {len(text) - limit} chars]"


def preview_object(payload: object, limit: int = _TEXT_LIMIT) -> str:
    try:
        rendered = json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError:
        rendered = str(payload)
    return truncate_text(rendered, limit=limit)


def summarize_binary(data: bytes, content_type: str | None = None) -> str:
    digest = hashlib.sha256(data).hexdigest()[:16]
    kind = content_type or "application/octet-stream"
    return f"type={kind} bytes={len(data)} sha256={digest}"
