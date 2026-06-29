from __future__ import annotations

from typing import Any, Dict, Tuple


def _count_cjk_chars(text: str) -> int:
    count = 0
    for ch in str(text or ""):
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF:
            count += 1
    return count


def _count_latin1_supplement_chars(text: str) -> int:
    count = 0
    for ch in str(text or ""):
        code = ord(ch)
        if 0x00C0 <= code <= 0x00FF:
            count += 1
    return count


def _repair_utf8_latin1_mojibake(text: str) -> str:
    value = str(text or "")
    if not value:
        return value
    before_cjk = _count_cjk_chars(value)
    before_latin1 = _count_latin1_supplement_chars(value)
    if before_latin1 < 3:
        return value
    try:
        repaired = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    after_cjk = _count_cjk_chars(repaired)
    after_latin1 = _count_latin1_supplement_chars(repaired)
    if after_cjk >= max(2, before_cjk + 2) and after_latin1 <= max(1, before_latin1 // 4):
        return repaired
    return value


def _looks_like_html_document(text: str) -> bool:
    value = str(text or "").strip().lower()
    if not value:
        return False
    if value.startswith("<!doctype") or value.startswith("<html"):
        return True
    return ("<html" in value and "</html>" in value) or ("<body" in value and "</body>" in value)


def normalize_text_response(text: str, *, return_meta: bool = False) -> str | Tuple[str, Dict[str, Any]]:
    original = str(text or "")
    repaired = _repair_utf8_latin1_mojibake(original).strip()
    html_detected = _looks_like_html_document(repaired)
    if html_detected:
        normalized = "TextFailed: API Text HTML Text. "
    else:
        normalized = repaired
    meta = {
        "html_detected": html_detected,
        "mojibake_repaired": normalized != original.strip() and not html_detected,
        "empty": not bool(normalized.strip()),
    }
    if return_meta:
        return normalized, meta
    return normalized
