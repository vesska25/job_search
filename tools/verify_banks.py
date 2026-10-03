"""Verify bank sources against the live web and (optionally) discover ATS candidates.

    python -m tools.verify_banks                    # check every enabled bank, print a report
    python -m tools.verify_banks --apply            # also write results into config/banks.yaml
    python -m tools.verify_banks --discover         # probe Personio/SmartRecruiters slugs for banks without a source
    python -m tools.verify_banks --discover --apply # store candidates as notes (never auto-enables them)

Rules: robots.txt and rate limits are respected (same HttpClient as the monitor). A bank is only marked
`ok` if its scraper returned at least one job. Failing enabled banks are disabled with a note.
Discovery results are candidates: slugs can belong to a different company, so they stay disabled until
you review them.
"""
from __future__ import annotations

import argparse
import re
from datetime import date

import yaml

from src.config import CONFIG_DIR, Bank, load_banks, load_settings
from src.scrapers import get_scraper
from src.utils.http import HttpClient
from src.utils.logging import get_logger, setup_logging
from src.utils.normalization import slugify

log = get_logger("verify")
LEGAL = r"\b(AG|GmbH|eG|e\.G\.|SE|S\.A\.|N\.V\.|KG|Co\.|Aktiengesellschaft|Niederlassung|Zweigniederlassung|Deutschland|Germany|Branch|Filiale|Frankfurt|Bank)\b"


def verify(banks, http, max_pages):
    results = {}
    for b in banks:
        try:
            jobs = get_scraper(b.source_type, http, max_pages=b.options.get("max_pages", max_pages)).fetch_jobs(b)
            status = "ok" if jobs or b.options.get("allow_empty") else "empty"
            results[b.id] = (status, len(jobs), "")
            for j in jobs[:3]:
                log.info("    sample: %s | %s | %s", j.title[:70], j.location or "-", j.url[:90])
        except Exception as exc:  # noqa: BLE001
            results[b.id] = ("failed", 0, f"{type(exc).__name__}: {exc}"[:200])
        log.info("%-40s %-7s jobs=%s %s", b.id, *results[b.id][:2], results[b.id][2])
    return results


def slug_candidates(name: str) -> list[str]:
    base = re.sub(LEGAL, " ", name, flags=re.I)
    base = re.sub(r"[,.()&]", " ", base)
    words = [w for w in slugify(base).split("_") if w]
    out = []
    if words:
        out += ["".join(words), "-".join(words)]
        if len(words) > 1:
            out += [words[0]]
    return list(dict.fromkeys(s for s in out if len(s) >= 4))


def discover(banks, http):
    found = {}
    for b in banks:
        if b.alias_of or b.enabled or b.source_type != "unknown":
            continue
        for slug in slug_candidates(b.name):
            for host in (f"{slug}.jobs.personio.de", f"{slug}.jobs.personio.com"):
                try:
                    r = http.get(f"https://{host}/xml")
                    if "<workzag-jobs" in r.text[:500]:
                        found[b.id] = ("personio", f"https://{host}/")
                except Exception:  # noqa: BLE001 - probing: any failure means 'not found'
                    pass
                if b.id in found:
                    break
            if b.id in found:
                break
            try:
                r = http.get("https://api.smartrecruiters.com/v1/companies/%s/postings" % slug, params={"limit": 1})
                if int(r.json().get("totalFound", 0)) > 0:
                    found[b.id] = ("smartrecruiters", f"https://jobs.smartrecruiters.com/{slug}")
            except Exception:  # noqa: BLE001
                pass
            if b.id in found:
                break
        if b.id in found:
            log.info("CANDIDATE %s -> %s %s (review manually!)", b.id, *found[b.id])
    return found


def apply_to_yaml(path, results, candidates):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    today = date.today().isoformat()
    for rec in data["banks"]:
        if rec["id"] in results:
            status, n, err = results[rec["id"]]
            rec["last_verified"] = today
            rec["verification_status"] = "ok" if status == "ok" else "failed"
            if status != "ok":
                rec["enabled"] = False
                rec["notes"] = (f"Auto-disabled {today}: " + (err or "source returned 0 vacancies")
                                + " | " + rec.get("notes", ""))[:600]
            else:
                rec["notes"] = re.sub(r"^Auto-disabled[^|]*\| ", "", rec.get("notes", ""))
        if rec["id"] in candidates:
            st, url = candidates[rec["id"]]
            rec["notes"] = f"Discovery candidate ({today}): {st} {url} - verify it is this bank, then set source_type/jobs_url/enabled. | " + rec.get("notes", "")
            rec["verification_status"] = "needs_review"
    header = "".join(l for l in path.read_text(encoding="utf-8").splitlines(True) if l.startswith("#"))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, width=120)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--all", action="store_true", help="verify disabled banks that have a jobs_url too")
    args = ap.parse_args(argv)
    setup_logging()
    settings = load_settings()
    h = settings["http"]
    http = HttpClient(user_agent=h["user_agent"], timeout=h["timeout"], retries=h["retries"],
                      min_delay=h["min_delay_seconds"], respect_robots=h["respect_robots_txt"])
    path = CONFIG_DIR / "banks.yaml"
    banks = load_banks(path)
    results = candidates = {}
    if args.discover:
        # Probing mostly hits non-existent hosts: no retries and a short timeout keep the run fast.
        probe = HttpClient(user_agent=h["user_agent"], timeout=10, retries=0, min_delay=1.0,
                           respect_robots=h["respect_robots_txt"])
        candidates = discover(banks, probe)
        log.info("Discovery candidates: %d", len(candidates))
    else:
        todo = [b for b in banks if not b.alias_of and b.jobs_url and (b.enabled or args.all)]
        results = verify(todo, http, h.get("max_pages", 30))
        ok = sum(1 for r in results.values() if r[0] == "ok")
        log.info("Verified OK: %d / %d", ok, len(results))
    if args.apply:
        apply_to_yaml(path, results, candidates)
        log.info("banks.yaml updated")


if __name__ == "__main__":
    main()
