"""Shared term-matching helpers."""
from __future__ import annotations

import re

from src.utils.normalization import normalize_text


def whole_word(term: str, text: str) -> bool:
    t = re.escape(normalize_text(term))
    return re.search(rf"(?<![\w&]){t}(?![\w&])", text) is not None


def find_terms(terms, text: str) -> list[str]:
    """Whole-word matches of each term in already-normalized text."""
    return [t for t in terms if whole_word(t, text)]
