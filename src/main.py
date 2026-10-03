"""Weekly bank job monitor entry point.

    python -m src.main                 # normal run (Telegram + DB)
    python -m src.main --dry-run       # scrape + filter, no Telegram, no DB writes
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from src.config import CONFIG_DIR, ROOT, Bank, env, load_banks, load_settings
from src.filters.llm import LlmClassifier
from src.filters.pipeline import evaluate
from src.notifications.telegram import TelegramNotifier, format_digest, format_job
from src.scrapers import get_scraper
from src.storage.database import JobDatabase
from src.utils.http import HttpClient
from src.utils.logging import get_logger, setup_logging

log = get_logger()


@dataclass
class Stats:
    processed: int = 0
    successful: int = 0
    failed: int = 0
    scanned: int = 0
    germany: int = 0
    leadership: int = 0
    relevant: int = 0
    new: int = 0

    def summary(self) -> str:
        return (f"Banks processed: {self.processed}\nSuccessful: {self.successful}\nFailed: {self.failed}\n"
                f"Vacancies scanned: {self.scanned:,}\nGermany vacancies: {self.germany:,}\n"
                f"Leadership matches: {self.leadership}\nRelevant matches: {self.relevant}\n"
                f"New matches: {self.new}")


def select_banks(banks: list[Bank], only: list[str] | None):
    if only:
        chosen = [b for b in banks if b.id in only]
        missing = set(only) - {b.id for b in chosen}
        if missing:
            raise SystemExit(f"Unknown bank id(s): {sorted(missing)}")
        return chosen, []
    enabled = [b for b in banks if b.enabled and not b.alias_of]
    return enabled, [b for b in banks if b not in enabled]


def run(banks, settings, http, db, llm, stats: Stats) -> list:
    """Scrape + filter every bank. Errors in one bank never stop the run. Returns matching jobs."""
    matches, seen_ids = [], set()
    for bank in banks:
        stats.processed += 1
        log.info("Scraping %s", bank.label)
        try:
            scraper = get_scraper(bank.source_type, http, max_pages=settings["http"].get("max_pages", 30))
            jobs = scraper.fetch_jobs(bank)
        except Exception as exc:  # noqa: BLE001 - isolate per-bank failures by design
            stats.failed += 1
            log.error("%s scraper failed: %s: %s", bank.label, type(exc).__name__, exc)
            continue
        stats.successful += 1
        log.info("Found %d vacancies", len(jobs))
        n_de = n_lead = n_rel = 0
        for job in jobs:
            try:
                d = evaluate(job, bank, settings, llm)
            except Exception as exc:  # noqa: BLE001
                log.error("Filtering failed for '%s' (%s): %s", job.title, bank.label, exc)
                continue
            n_de += d.germany
            n_lead += d.leadership and d.germany
            n_rel += d.accepted
            if d.accepted and job.job_id not in seen_ids:
                seen_ids.add(job.job_id)
                job.matched_functions, job.borderline = d.functions, d.borderline
                matches.append(job)
                log.debug("MATCH %s | %s | %s", bank.label, job.title, d.reason)
        log.info("Germany vacancies: %d", n_de)
        log.info("Seniority matches: %d", n_lead)
        log.info("Relevant matches: %d", n_rel)
        stats.scanned += len(jobs)
        stats.germany += n_de
        stats.leadership += n_lead
        stats.relevant += n_rel
    return matches


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Weekly bank job monitor")
    ap.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    ap.add_argument("--db", type=Path, help="override database path")
    ap.add_argument("--bank", action="append", help="only run this bank id (repeatable; ignores enabled flag)")
    ap.add_argument("--dry-run", action="store_true", help="no Telegram message, no database writes")
    ap.add_argument("--baseline", action="store_true",
                    help="store all current matches as already notified (use for the very first run)")
    ap.add_argument("--no-robots", action="store_true", help="debug only; default respects robots.txt")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    setup_logging("DEBUG" if args.verbose else "INFO")
    log.info("Starting job monitor")
    settings = load_settings(args.config_dir / "settings.yaml")
    banks = load_banks(args.config_dir / "banks.yaml")
    selected, skipped = select_banks(banks, args.bank)
    log.info("Banks enabled: %d", len(selected))
    log.info("Banks skipped: %d", len(skipped))

    h = settings["http"]
    http = HttpClient(user_agent=h["user_agent"], timeout=h["timeout"], retries=h["retries"],
                      min_delay=h["min_delay_seconds"],
                      respect_robots=h["respect_robots_txt"] and not args.no_robots)
    lcfg = settings.get("llm", {})
    llm = None
    if lcfg.get("enabled"):
        llm = LlmClassifier(env("ANTHROPIC_API_KEY"), lcfg["model"], lcfg["min_confidence"],
                            lcfg["max_calls_per_run"], lcfg["timeout"])
        if not llm.api_key:
            log.warning("llm.enabled is true but ANTHROPIC_API_KEY is missing; running without LLM")
            llm = None

    db_path = args.db or ROOT / settings["database"]["path"]
    db = JobDatabase(":memory:" if args.dry_run and not args.db else db_path)
    stats = Stats()
    matches = run(selected, settings, http, db, llm, stats)

    # Deduplicate against the database: new jobs, or earlier jobs whose notification failed.
    to_notify = [j for j in matches if db.needs_notification(j)]
    stats.new = sum(1 for j in matches if db.find(j) is None)
    log.info("New vacancies: %d", stats.new)

    exit_code = 0
    tcfg = settings["telegram"]
    if args.dry_run:
        for j in to_notify:
            log.info("[dry-run] would notify: %s — %s (%s) %s", j.bank_name, j.title, j.location, j.url)
    else:
        for j in matches:
            db.record(j)
        if args.baseline:
            db.mark_notified(matches)
            log.info("Baseline stored: %d vacancies marked as notified", len(matches))
        elif to_notify or tcfg.get("send_empty_digest", True):
            try:
                notifier = TelegramNotifier(env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID"))
                if tcfg["mode"] == "individual" and to_notify:
                    notifier.send_all(format_job(j) for j in to_notify)
                else:
                    notifier.send_all(format_digest(to_notify, max_jobs=tcfg["max_jobs_in_digest"],
                                                    failed_sources=stats.failed))
                db.mark_notified(to_notify)
                log.info("Telegram notification sent")
            except Exception as exc:  # noqa: BLE001
                log.error("Telegram notification failed (jobs stay pending for next run): %s", exc)
                exit_code = 1
    db.close()
    log.info("Summary:\n%s", stats.summary())
    if stats.processed and stats.failed == stats.processed:
        log.error("Every source failed - check network access and banks.yaml")
        exit_code = exit_code or 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
