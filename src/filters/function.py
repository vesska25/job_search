"""Relevant-function (Finance / Risk / Regulatory / Treasury ...) matching."""
from __future__ import annotations

from src.filters.matching import whole_word
from src.utils.normalization import normalize_text

SHORT = 4  # terms up to this length must match as whole words


def _strip_phrases(text: str, phrases) -> str:
    for p in phrases:
        text = text.replace(normalize_text(p), " ")
    return text


def _hit(term: str, text: str) -> bool:
    t = normalize_text(term)
    if len(t) <= SHORT:
        return whole_word(term, text)
    return t in text


def match_functions(job, cfg: dict) -> list[str]:
    phrases = cfg.get("irrelevant_function_phrases", [])
    fields = cfg.get("function_fields", ["title", "department"])
    text = _strip_phrases(normalize_text(" ".join(getattr(job, f, "") for f in fields)), phrases)
    hits = [f for f in cfg["relevant_functions"] if _hit(f, text)]
    if hits:
        return hits
    desc = _strip_phrases(normalize_text(job.description), phrases)
    desc_hits = [f for f in cfg["relevant_functions"] if _hit(f, desc)]
    return desc_hits if len(desc_hits) >= cfg.get("description_min_hits", 3) else []


def weak_function_hits(job, cfg: dict) -> list[str]:
    """Description-only function hits below the threshold (used to flag borderline cases)."""
    desc = _strip_phrases(normalize_text(job.description), cfg.get("irrelevant_function_phrases", []))
    return [f for f in cfg["relevant_functions"] if _hit(f, desc)]
