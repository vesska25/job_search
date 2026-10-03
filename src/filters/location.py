"""Germany filter."""
from __future__ import annotations

from src.filters.matching import find_terms
from src.utils.normalization import normalize_text

GERMANY_TOKENS = ("germany", "deutschland", "de")


def is_germany(job, bank, cfg: dict) -> tuple[bool, str]:
    """Return (accepted, reason). cfg is settings['location']."""
    if bank.germany_only:
        return True, "source is Germany-only"
    country = normalize_text(job.country)
    loc = normalize_text(job.location)
    german = [normalize_text(x) for x in cfg["german_locations"]]
    foreign = [normalize_text(x) for x in cfg["foreign_locations"]]
    explicit_de = ("germany", "deutschland")

    if country in GERMANY_TOKENS:
        return True, "country=Germany"
    if country and country not in GERMANY_TOKENS:
        return False, f"country={job.country}"
    # An explicit mention of Germany in the location field wins over foreign city names
    if any(find_terms([w], loc) for w in explicit_de):
        return True, "location mentions Germany"
    german_hit = find_terms(german, loc)
    foreign_hit = find_terms(foreign, loc)
    if german_hit and not foreign_hit:
        return True, f"German location: {german_hit[0]}"
    if foreign_hit and not german_hit:
        return False, f"foreign location: {foreign_hit[0]}"
    if german_hit and foreign_hit:
        # e.g. "Frankfurt / London": job explicitly lists a German site
        return True, f"German location listed: {german_hit[0]}"
    # No usable location text: look at title and the start of the description
    title_desc = normalize_text(job.title + " " + job.description[:300])
    if any(find_terms([w], title_desc) for w in explicit_de):
        return True, "title/description states Germany"
    if cfg.get("accept_unknown_location", False):
        return True, "unknown location accepted by config"
    return False, "location unknown or not in Germany"
