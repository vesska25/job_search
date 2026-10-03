"""Seniority classification: strong leadership / ambiguous / none / excluded."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.filters.matching import find_terms
from src.utils.normalization import normalize_text


@dataclass
class SeniorityResult:
    level: str  # strong | ambiguous | weak | none | excluded
    matched: list = field(default_factory=list)
    reason: str = ""
    management_signal: bool = False


def _suffix_match(term: str, text: str, exclusions) -> bool:
    """Match `term` at the end of a (compound) word, e.g. 'leitung' in 'bereichsleitung'."""
    for m in re.finditer(rf"(\w*){re.escape(term)}(?![\w])", text):
        word = m.group(0)
        if not any(word == e or word.endswith(e) for e in exclusions):
            return True
    return False


def classify_seniority(title: str, description: str, cfg: dict) -> SeniorityResult:
    t = normalize_text(title)
    d = normalize_text(description)
    hard = find_terms(cfg.get("hard_exclusions", []), t)
    if hard:
        return SeniorityResult("excluded", hard, f"hard exclusion: {hard[0]}")

    excl_words = [normalize_text(x) for x in cfg.get("compound_exclusions", [])]
    strong = []
    for kw in cfg.get("seniority_keywords", []):
        k = normalize_text(kw)
        if find_terms([k], t):
            strong.append(kw)
    for kw in cfg.get("compound_suffix_keywords", []):
        k = normalize_text(kw)
        if kw not in strong and _suffix_match(k, t, excl_words):
            strong.append(kw)
    # compound exclusions should veto a suffix hit only when no whole-word hit exists (handled above)

    if strong:
        return SeniorityResult("strong", strong, f"leadership keyword: {strong[0]}")

    soft = find_terms(cfg.get("exclusions", []), t)
    if soft:
        return SeniorityResult("excluded", soft, f"exclusion: {soft[0]}")

    signals = [x for x in cfg.get("management_signals", []) if normalize_text(x) in d] if d else []
    amb = find_terms(cfg.get("ambiguous_seniority_keywords", []), t)
    if amb:
        return SeniorityResult("ambiguous", amb, f"ambiguous title: {amb[0]}", management_signal=bool(signals))
    weak = find_terms(cfg.get("weak_seniority_keywords", []), t)
    if weak:
        return SeniorityResult("weak", weak, f"weak title: {weak[0]}", management_signal=bool(signals))
    return SeniorityResult("none", [], "no leadership keyword")
