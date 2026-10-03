"""Keyword profiles for additional recipients (e.g. 'Junior IT Java'): no leadership logic, just terms.

A vacancy matches when
  * it is in Germany (same location filter as the main profile),
  * the title (or department) contains at least one `tech` term,
  * the title (or department) contains at least one `level` term,
  * and the title contains none of the `exclude` terms.
With `accept_unspecified_level: true` a title with a technology term but no level word is accepted too, flagged as
borderline ("level not stated"); the `exclude` terms (Senior, Lead, ...) still remove it.
All terms are matched as whole words, case-insensitive ("Java" does not match "JavaScript").
"""
from __future__ import annotations

from src.filters.location import is_germany
from src.filters.matching import find_terms
from src.filters.pipeline import Decision
from src.utils.normalization import normalize_text


def evaluate_keywords(job, bank, settings: dict, cfg: dict) -> Decision:
    d = Decision()
    d.germany, why = is_germany(job, bank, settings["location"])
    if not d.germany:
        d.reason = why
        return d
    title = normalize_text(job.title)
    scope = normalize_text(f"{job.title} {getattr(job, 'department', '') or ''}")
    if cfg.get("level_in_description"):
        level_scope = scope + " " + normalize_text((job.description or "")[:600])
    else:
        level_scope = scope
    excluded = find_terms(cfg.get("exclude", []), title)
    if excluded:
        d.reason = f"excluded term: {excluded[0]}"
        return d
    tech = find_terms(cfg.get("tech", []), scope)
    d.relevant = bool(tech)
    if not tech:
        d.reason = "no technology term in title"
        return d
    level = find_terms(cfg.get("level", []), level_scope)
    if not level:
        if cfg.get("accept_unspecified_level"):
            # Seniority is not stated and nothing excludes the title: show it, flagged, so the reader can judge.
            d.functions = [tech[0], "level not stated"]
            d.accepted, d.borderline = True, True
            d.reason = f"technology {tech[0]}, level not stated"
            return d
        d.reason = f"technology {tech[0]} but no junior/entry-level term"
        return d
    d.functions = [tech[0], level[0]]
    d.accepted = d.leadership = True      # leadership flag only feeds the shared run statistics
    d.reason = f"{level[0]} + {tech[0]}"
    return d
