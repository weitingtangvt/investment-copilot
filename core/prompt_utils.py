"""Prompt rendering helpers with max-length guards."""

from __future__ import annotations

from typing import Any


def render_prompt(template: str, max_chars: int = 16000, **kwargs: Any) -> str:
    text = template.format(**kwargs)
    if len(text) <= max_chars:
        return text
    head = text[: max_chars - 120]
    return f"{head}\n\n[TRUNCATED] prompt exceeded {max_chars} chars and was truncated."

