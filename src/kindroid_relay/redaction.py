from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_PATTERNS = (
    (re.compile(r"\bkn_[A-Za-z0-9._-]{4,}\b"), "[REDACTED_KINDROID_KEY]"),
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(r"(?i)\b(cookie|set-cookie|authorization)\s*[:=]\s*[^\s,;]+"),
        r"\1=[REDACTED]",
    ),
)


def redact_text(value: object, *, maximum: int = 2000) -> str:
    text = str(value)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    if len(text) > maximum:
        return text[: maximum - 1].rstrip() + "…"
    return text


def redact_mapping(
    value: Mapping[str, Any],
    *,
    allowed_keys: set[str] | frozenset[str],
) -> dict[str, str | int | float | bool | None]:
    result: dict[str, str | int | float | bool | None] = {}
    for key, item in value.items():
        if key not in allowed_keys:
            raise ValueError(f"activity detail field is not allowed: {key}")
        if item is None or isinstance(item, (bool, int, float)):
            result[key] = item
        elif isinstance(item, str):
            result[key] = redact_text(item, maximum=1000)
        else:
            result[key] = redact_text(item, maximum=1000)
    return result

