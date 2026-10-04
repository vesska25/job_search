"""Cross-source de-duplication: the same vacancy often appears on the bank's own site and on an aggregator
(Bundesagentur, executive-search agencies). The bank's own posting wins; aggregators are also de-duplicated among
themselves (first one wins).

Two postings are the same when the normalized title and the city match and, if the aggregator names the employer,
that employer shares a significant word with the primary source's bank name. Sources are marked with the bank option
`aggregator: true`."""
from __future__ import annotations

import re

from src.utils.normalization import normalize_text

_GENDER = re.compile(r"\((?:[mwdfxi*:/ _-]|gn|all genders){2,}\)|\b[mwdfx]\s*/\s*[mwdfx](?:\s*/\s*[mwdfx])?\b", re.I)
_GENERIC = {"bank", "ag", "gmbh", "se", "eg", "kg", "co", "und", "via", "bundesagentur", "group", "gruppe", "the", "of"}


def norm_title(title: str) -> str:
    t = _GENDER.sub(" ", normalize_text(title))
    t = re.sub(r"[*:]in\b", "", t)
    return re.sub(r"[^a-z0-9äöüß]+", " ", t).strip()


def norm_city(location: str) -> str:
    first = re.split(r"[,;/(]", normalize_text(location))[0]
    return re.sub(r"\s+am\s+main$|\s+\d+$", "", first).strip()


def _employer_words(name: str) -> set:
    name = re.sub(r"\(via [^)]*\)", " ", normalize_text(name))
    return {w for w in re.findall(r"[a-zäöüß0-9]{3,}", name) if w not in _GENERIC}


def _same_employer(agg, primary) -> bool:
    if "(via " not in agg.bank_name:            # employer not disclosed: title + city decide
        return True
    return bool(_employer_words(agg.bank_name) & _employer_words(primary.bank_name))


def dedupe(jobs: list) -> list:
    """Return `jobs` without cross-source duplicates; keeps order. Aggregator jobs have `aggregator` set."""
    primaries, kept, seen_agg = {}, [], {}
    for j in jobs:
        if not getattr(j, "aggregator", False):
            primaries.setdefault((norm_title(j.title), norm_city(j.location)), []).append(j)
    for j in jobs:
        if getattr(j, "aggregator", False):
            key = (norm_title(j.title), norm_city(j.location))
            if any(_same_employer(j, p) for p in primaries.get(key, [])):
                continue
            if any(_same_employer(j, o) and _same_employer(o, j) for o in seen_agg.get(key, [])):
                continue
            seen_agg.setdefault(key, []).append(j)
        kept.append(j)
    return kept
