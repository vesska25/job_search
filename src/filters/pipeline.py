"""Deterministic filter pipeline with optional LLM tie-breaker for borderline cases."""
from __future__ import annotations

from dataclasses import dataclass, field

from src.filters.function import match_functions, weak_function_hits
from src.filters.location import is_germany
from src.filters.seniority import classify_seniority
from src.utils.normalization import normalize_text


@dataclass
class Decision:
    germany: bool = False
    leadership: bool = False   # counted as a leadership match (strong or borderline-accepted)
    relevant: bool = False
    accepted: bool = False
    borderline: bool = False
    used_llm: bool = False
    reason: str = ""
    functions: list = field(default_factory=list)


def evaluate(job, bank, settings: dict, llm=None) -> Decision:
    fcfg, lcfg = settings["filters"], settings["location"]
    d = Decision()

    d.germany, why = is_germany(job, bank, lcfg)
    sen = classify_seniority(job.title, job.description, fcfg)
    funcs = match_functions(job, fcfg)
    d.functions = funcs
    d.relevant = bool(funcs)

    # --- Germany unknown/foreign: LLM may only rescue *unknown* locations (never override a foreign country)
    if not d.germany:
        d.reason = why
        if (llm and llm.available() and why.startswith("location unknown")
                and sen.level == "strong" and funcs):
            verdict = llm.classify(job)
            d.used_llm = True
            if verdict and verdict.accepts(llm.min_confidence):
                d.germany, d.leadership, d.accepted, d.borderline = True, True, True, True
                d.reason = f"LLM: {verdict.reason}"
        return d

    if sen.level in ("excluded", "none"):
        d.reason = sen.reason
        return d

    if sen.level == "strong":
        d.leadership = True
        if funcs:
            d.accepted = True
            d.reason = f"{sen.reason}; function: {funcs[0]}"
        elif weak_function_hits(job, fcfg) and llm and llm.available():
            verdict = llm.classify(job)
            d.used_llm = True
            if verdict and verdict.accepts(llm.min_confidence):
                d.accepted, d.relevant, d.borderline = True, True, True
                d.reason = f"LLM: {verdict.reason}"
            else:
                d.reason = "no relevant function (LLM declined)"
        else:
            d.reason = "leadership but no relevant function"
        return d

    # --- ambiguous ("Senior Manager ...") or weak ("Manager ...") seniority
    weak = sen.level == "weak"
    if fcfg.get("ambiguous_requires_title_function", True):
        funcs = match_functions(job, fcfg, title_only=True)
        d.functions, d.relevant = funcs, bool(funcs)
    if not funcs:
        d.reason = f"{sen.level} seniority and no relevant function in title"
        return d
    if sen.management_signal:
        d.leadership = d.accepted = True
        d.reason = f"{sen.reason}; management responsibility in description"
        return d
    if llm and llm.available():
        verdict = llm.classify(job)
        d.used_llm = True
        if verdict:
            d.leadership = verdict.leadership_role and verdict.confidence >= llm.min_confidence
            d.accepted = verdict.accepts(llm.min_confidence)
            d.borderline = d.accepted
            d.reason = f"LLM: {verdict.reason}"
            return d
    if weak:
        # Plain "Manager" with a Risk / IT term in the TITLE is shown as borderline (flagged), not dropped.
        wanted = {normalize_text(x) for x in fcfg.get("weak_borderline_functions", [])}
        hit = [f for f in match_functions(job, fcfg, title_only=True) if normalize_text(f) in wanted]
        if hit and fcfg.get("borderline_policy", "include") == "include":
            d.leadership = d.accepted = d.borderline = True
            d.functions = hit
            d.reason = f"{sen.reason}; borderline: {hit[0]} in title, no management signal found"
            return d
        d.reason = f"{sen.reason}; no management signal in description"
        return d
    if fcfg.get("borderline_policy", "include") == "include":
        d.leadership = d.accepted = d.borderline = True
        d.reason = f"{sen.reason}; borderline (no management signal found)"
    else:
        d.reason = f"{sen.reason}; borderline rejected by policy"
    return d
