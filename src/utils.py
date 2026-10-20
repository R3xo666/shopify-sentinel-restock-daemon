"""Utility helpers: logging, rate limiting backoff math, and Discord payload sanitization."""

from __future__ import annotations

import logging
import re
import secrets

from rich.logging import RichHandler

WEBHOOK_URL_PATTERN = re.compile(r"https://discord(?:app)?\.com/api/webhooks/(\d+)/([A-Za-z0-9_-]+)")
PROXY_CREDENTIAL_PATTERN = re.compile(r"://([^:@]+):([^@]+)@")


class SensitiveDataFilter(logging.Filter):
    """Filter that masks Discord webhook tokens and proxy credentials from logs (CWE-522)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_sensitive_strings(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: mask_sensitive_strings(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(mask_sensitive_strings(str(a)) for a in record.args)
        return True


def mask_sensitive_strings(text: str) -> str:
    """Mask sensitive tokens in URLs and connection strings."""
    if not isinstance(text, str):
        return str(text)
    # Mask webhook token
    text = WEBHOOK_URL_PATTERN.sub(r"https://discord.com/api/webhooks/\1/********", text)
    # Mask proxy credentials
    text = PROXY_CREDENTIAL_PATTERN.sub(r"://\1:********@", text)
    return text


def sanitize_discord_text(text: str | None, max_len: int = 500) -> str:
    """Sanitize user-provided text to prevent Discord mention injection (CWE-116).

    Replaces `@everyone` and `@here` with zero-width space variants,
    strips out raw mention formatting, escapes markdown brackets, and truncates length.
    """
    if not text:
        return ""

    # Neutralize @everyone and @here by inserting zero-width space
    sanitized = text.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")

    # Neutralize role/user mentions like <@&12345> or <@12345>
    sanitized = re.sub(r"<@&?(\d+)>", r"@\1", sanitized)

    # Escape markdown link brackets to prevent link injection / spoofing
    sanitized = sanitized.replace("[", "\\[").replace("]", "\\]")

    sanitized = sanitized.strip()
    if len(sanitized) > max_len:
        sanitized = sanitized[: max_len - 1] + "…"

    return sanitized


def calculate_exponential_backoff(
    attempt: int,
    base: float = 2.0,
    max_backoff: float = 60.0,
    jitter_max: float = 3.0,
) -> float:
    """Calculate exponential backoff duration with randomized full jitter.

    Formula: min(max_backoff, base * 2**attempt + random(0, jitter_max))
    Uses secrets.SystemRandom() to satisfy secure random generation scanners (CWE-330).
    """
    crypto_rand = secrets.SystemRandom()
    exponential = base * (2 ** max(0, attempt))
    jitter = crypto_rand.uniform(0.0, jitter_max)
    return min(max_backoff, exponential + jitter)


def setup_logger(name: str = "shopify_sentinel", log_level: str = "INFO") -> logging.Logger:
    """Configure and return a standard logger equipped with Rich formatting and sensitive data masking."""
    logger = logging.getLogger(name)
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    # Avoid adding duplicate handlers on multiple calls
    if not logger.handlers:
        handler = RichHandler(
            rich_tracebacks=True,
            show_time=True,
            show_path=False,
            markup=True,
        )
        handler.addFilter(SensitiveDataFilter())
        formatter = logging.Formatter("%(message)s", datefmt="[%X]")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
