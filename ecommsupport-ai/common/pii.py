"""PII redaction applied before anything is logged or sent to LangSmith.

The same rules are used in three places:
1. the LangSmith client anonymiser (traces, eval runs),
2. the application logger,
3. the guardrail audit log of blocked inputs.
"""

from __future__ import annotations

import logging
import re

from langsmith.anonymizer import create_anonymizer

# Order matters: card numbers before phone numbers, which would otherwise eat them.
PII_RULES: list[tuple[str, str]] = [
    (r"\b(?:\d[ -]?){13,19}\b", "<CARD_NUMBER>"),
    (r"[\w.+-]+@[\w-]+\.[\w.-]+", "<EMAIL>"),
    (r"(?:\+44\s?7\d{3}|\b07\d{3})\s?\d{3}\s?\d{3}\b", "<PHONE>"),
    (r"\+\d{1,3}[\s-]?\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}\b", "<PHONE>"),
    (r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", "<POSTCODE>"),
    (r"\b\d{1,4}\s+(?:[A-Z][a-z]+\s){1,3}(?:Street|St|Road|Rd|Avenue|Ave|Lane|Close|Way|Drive|Wynd)\b", "<ADDRESS>"),
    (r"\b(?:sk|pk|pcsk|lsv2)[-_][A-Za-z0-9_\-]{16,}\b", "<API_KEY>"),
]

_COMPILED = [(re.compile(p), r) for p, r in PII_RULES]


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    return text


# For LangSmith: walks nested inputs/outputs and redacts every string node.
langsmith_anonymizer = create_anonymizer([{"pattern": p, "replace": r} for p, r in PII_RULES])


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs PII from every record's message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.getMessage()))
        record.args = ()
        return True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger
