"""Weekly bank job monitor entry point.

    python -m src.main                 # normal run (Telegram + DB)
    python -m src.main --dry-run       # scrape + filter, no Telegram, no DB writes
"""
from __future__ import annotations

import argparse
from datetime import date
import copy
import html as htmllib
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from src.config import CONFIG_DIR, ROOT, Bank, env, load_banks, load_settings
from src.dedup import dedupe
from src.filters.llm import LlmClassifier
from src.filters.pipeline import evaluate
from src.filters.profile import evaluate_keywords
from src.models.job import Job
from src.notifications.telegram import TelegramNotifier, format_digest, format_job
from src.scrapers import get_scraper
from src.storage.database import JobDatabase
from src.health import HealthStore
from src.report import render_html
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
    matches_by_bank: dict = field(default_factory=dict)    # bank id -> [(title, url, location)] accepted this run

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


def resolve_location(job, bank, http) -> None:
    """Some lists carry no location. For banks with options.detail_location, open the vacancy page
    and read the location from its <title> with a regex (group 1). Called only for jobs that already
    passed the seniority and function filters, so only a handful of extra requests are made."""
    cfg = bank.options["detail_location"]
    try:
        text = http.get(job.url).text
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
        title = htmllib.unescape(m.group(1)).strip() if m else ""
        loc = re.search(cfg["regex"], title)
        if loc:
            job.location = loc.group(1).strip()
        else:
            log.debug("No location found in page title '%s' for %s", title, job.url)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not resolve location for '%s': %s", job.title, exc)


def run(banks, settings, http, db, llm, stats: Stats, extra: dict | None = None, profiles: dict | None = None,
        health=None) -> list:
    """Scrape + filter every bank. Errors in one bank never stop the run. Returns the jobs matching the main
    (leadership) profile. Every bank is scraped once; with `profiles` ({name: keyword config}) the same vacancies
    are also evaluated per extra profile and collected into `extra[name]`."""
    matches, seen_ids = [], set()
    profiles = profiles or {}
    extra = extra if extra is not None else {}
    extra_seen = {name: set() for name in profiles}
    funnel = {name: [0, 0] for name in profiles}      # [Germany vacancies with a technology term, of which junior-level]
    for name in profiles:
        extra.setdefault(name, [])
    for bank in banks:
        stats.processed += 1
        log.info("Scraping %s", bank.label)
        try:
            scraper = get_scraper(bank.source_type, http,
                                  max_pages=bank.options.get("max_pages", settings["http"].get("max_pages", 30)))
            jobs = scraper.fetch_jobs(bank)
        except Exception as exc:  # noqa: BLE001 - isolate per-bank failures by design
            stats.failed += 1
            log.error("%s scraper failed: %s: %s", bank.label, type(exc).__name__, exc)
            if health is not None:
                health.record(bank.id, bank.label, error=f"{type(exc).__name__}: {exc}")
            continue
        stats.successful += 1
        log.info("Found %d vacancies", len(jobs))
        if bank.options.get("aggregator"):
            for job in jobs:
                job.aggregator = True
        n_de = n_lead = n_rel = 0
        bank_matches = []
        for job in jobs:
            for pname, pcfg in profiles.items():
                try:
                    pd = evaluate_keywords(job, bank, settings, pcfg)
                except Exception as exc:  # noqa: BLE001
                    log.error("Profile %s filtering failed for '%s' (%s): %s", pname, job.title, bank.label, exc)
                    continue
                if pd.germany and pd.relevant:
                    funnel[pname][0] += 1
                    funnel[pname][1] += pd.accepted
                    if not pd.accepted:
                        log.debug("NEAR-MISS[%s] %s | %s | %s", pname, bank.label, job.title, pd.reason)
                if pd.accepted and job.job_id not in extra_seen[pname]:
                    extra_seen[pname].add(job.job_id)
                    pj = copy.copy(job)
                    pj.matched_functions, pj.borderline = pd.functions, pd.borderline
                    extra[pname].append(pj)
                    log.debug("MATCH[%s] %s | %s | %s", pname, bank.label, job.title, pd.reason)
            try:
                if bank.options.get("detail_location") and not job.location:
                    if evaluate(job, replace(bank, germany_only=True), settings).accepted:
                        resolve_location(job, bank, http)
                d = evaluate(job, bank, settings, llm)
            except Exception as exc:  # noqa: BLE001
                log.error("Filtering failed for '%s' (%s): %s", job.title, bank.label, exc)
                continue
            n_de += d.germany
            if not d.germany:
                log.debug("NOT-GERMANY %s | %s | %s | %s", bank.label, job.title, job.location, d.reason)
            if d.germany and not d.accepted:
                log.debug("REJECT %s | %s | %s | %s", bank.label, job.title, job.location, d.reason)
            n_lead += d.leadership and d.germany
            n_rel += d.accepted
            if d.accepted:
                bank_matches.append((job.title, job.url, job.location))
            if d.accepted and job.job_id not in seen_ids:
                seen_ids.add(job.job_id)
                job.matched_functions, job.borderline = d.functions, d.borderline
                matches.append(job)
                log.debug("MATCH %s | %s | %s", bank.label, job.title, d.reason)
        log.info("Germany vacancies: %d", n_de)
        log.info("Seniority matches: %d", n_lead)
        log.info("Relevant matches: %d", n_rel)
        if health is not None:
            health.record(bank.id, bank.label, count=len(jobs), germany=n_de, relevant=n_rel)
        stats.matches_by_bank[bank.id] = bank_matches
        stats.scanned += len(jobs)
        stats.germany += n_de
        stats.leadership += n_lead
        stats.relevant += n_rel
    for name, (with_tech, junior) in funnel.items():
        log.info("Profile %s funnel: %d Germany vacancies with a technology term in the title, %d of them junior-level",
                 name, with_tech, junior)
    before = len(matches)
    matches = dedupe(matches)
    for name in list(extra):
        extra[name] = dedupe(extra[name])
    if before != len(matches):
        log.info("Cross-source duplicates removed: %d", before - len(matches))
    return matches


def send_test_digest(db, banks, notifier, limit: int = 10) -> int:
    """Delivery check: send already stored vacancies as a sample digest. Changes nothing in the database."""
    labels = {b.id: b.label for b in banks}
    rows = db.conn.execute("SELECT * FROM jobs ORDER BY first_seen DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    jobs = [Job(bank_id=r["bank_id"], bank_name=labels.get(r["bank_id"], r["bank_id"]), title=r["title"],
                url=r["canonical_url"], location=r["location"] or "") for r in rows]
    messages = format_digest(jobs, max_jobs=limit)
    messages[0] = "<b>TEST MESSAGE</b> - delivery check, these are already known vacancies, nothing is new.\n\n" + messages[0]
    notifier.send_all(messages)
    return len(jobs)


def deliver_profile(name: str, pcfg: dict, matches: list, db, args, tcfg: dict) -> int:
    """Dedup, store and send the digest of one extra profile (own table, own chat). Returns an exit code."""
    pdb = JobDatabase(":memory:") if args.dry_run and not args.db else db.for_profile(name)
    try:
        to_notify = list(matches) if getattr(args, "resend", False) else [j for j in matches if pdb.needs_notification(j)]
        new = sum(1 for j in matches if pdb.find(j) is None)
        log.info("Profile %s: %d matching, %d new", name, len(matches), new)
        if args.dry_run:
            for j in to_notify:
                log.info("[dry-run][%s] would notify: %s — %s (%s) %s", name, j.bank_name, j.title, j.location, j.url)
            return 0
        for j in matches:
            pdb.record(j)
        if args.baseline:
            pdb.mark_notified(matches)
            log.info("Profile %s baseline stored: %d vacancies marked as notified", name, len(matches))
            return 0
        if not (to_notify or pcfg.get("send_empty_digest", tcfg.get("send_empty_digest", True))):
            return 0
        chat = env(pcfg.get("chat_id_env", ""))
        if not chat:
            log.warning("Profile %s: %s is not set - nothing sent, vacancies stay pending", name, pcfg.get("chat_id_env"))
            return 0
        try:
            TelegramNotifier(env("TELEGRAM_BOT_TOKEN"), chat).send_all(format_digest(
                to_notify, max_jobs=pcfg.get("max_jobs_in_digest", tcfg["max_jobs_in_digest"]),
                title=pcfg.get("title", name.upper()),
                empty_text=pcfg.get("empty_text", "No new matching vacancies this week.")))
            pdb.mark_notified(to_notify)
            log.info("Profile %s: Telegram notification sent", name)
        except Exception as exc:  # noqa: BLE001
            log.error("Profile %s: Telegram notification failed (stays pending for next run): %s", name, exc)
            return 1
        return 0
    finally:
        if pdb is not db:
            pdb.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Weekly bank job monitor")
    ap.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    ap.add_argument("--db", type=Path, help="override database path")
    ap.add_argument("--bank", action="append", help="only run this bank id (repeatable; ignores enabled flag)")
    ap.add_argument("--dry-run", action="store_true", help="no Telegram message, no database writes")
    ap.add_argument("--baseline", action="store_true",
                    help="store all current matches as already notified (use for the very first run)")
    ap.add_argument("--resend", action="store_true",
                    help="send ALL current matches of the selected profiles, not only the new ones (they are then stored as sent)")
    ap.add_argument("--test-telegram", action="store_true",
                    help="send a sample digest of already stored vacancies to Telegram and exit (no scraping, no DB changes)")
    ap.add_argument("--profile", action="append",
                    help="only this recipient profile (repeatable): 'main' (leadership) or a name from settings.yaml 'profiles'; "
                         "default: all")
    ap.add_argument("--report", type=Path, metavar="FILE",
                    help="write the source-health report (HTML) to FILE; on a full non-dry run it is also sent to the main Telegram chat")
    ap.add_argument("--learn-health", action="store_true",
                    help="dry run that still stores each source's vacancy count (health baseline); no Telegram, no vacancy records")
    ap.add_argument("--no-robots", action="store_true", help="debug only; default respects robots.txt")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.learn_health:
        args.dry_run = True

    setup_logging("DEBUG" if args.verbose else "INFO")
    log.info("Starting job monitor")
    settings = load_settings(args.config_dir / "settings.yaml")
    banks = load_banks(args.config_dir / "banks.yaml")
    selected, skipped = select_banks(banks, args.bank)
    log.info("Banks enabled: %d", len(selected))
    log.info("Banks skipped: %d", len(skipped))
    extra_cfg = {n: c for n, c in (settings.get("profiles") or {}).items() if c.get("enabled", True)}
    wanted = args.profile or ["main", *extra_cfg]
    unknown = [n for n in wanted if n != "main" and n not in extra_cfg]
    if unknown:
        raise SystemExit(f"Unknown profile(s): {unknown}; available: main, {', '.join(extra_cfg)}")
    run_main = "main" in wanted
    profiles = {n: extra_cfg[n] for n in wanted if n != "main"}
    log.info("Profiles: %s", ", ".join(wanted))

    if args.test_telegram:
        if run_main:
            db = JobDatabase(args.db or ROOT / settings["database"]["path"])
            try:
                n = send_test_digest(db, banks, TelegramNotifier(env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")))
            finally:
                db.close()
            log.info("Test message sent (%d sample vacancies)", n)
        for name, pcfg in profiles.items():
            chat = env(pcfg.get("chat_id_env", ""))
            if not chat:
                log.warning("Profile %s: %s is not set, no test message", name, pcfg.get("chat_id_env"))
                continue
            TelegramNotifier(env("TELEGRAM_BOT_TOKEN"), chat).send(
                f"<b>TEST MESSAGE</b> - delivery check for the profile \"{pcfg.get('title', name)}\". Nothing is new.")
            log.info("Test message sent to profile %s", name)
        return 0

    h = settings["http"]
    http = HttpClient(user_agent=h["user_agent"], timeout=h["timeout"], retries=h["retries"],
                      min_delay=h["min_delay_seconds"],
                      respect_robots=h["respect_robots_txt"] and not args.no_robots)
    lcfg = settings.get("llm", {})
    llm = None
    if lcfg.get("enabled") and run_main:
        llm = LlmClassifier(env("ANTHROPIC_API_KEY"), lcfg["model"], lcfg["min_confidence"],
                            lcfg["max_calls_per_run"], lcfg["timeout"])
        if not llm.api_key:
            log.warning("llm.enabled is true but ANTHROPIC_API_KEY is missing; running without LLM")
            llm = None

    db_path = args.db or ROOT / settings["database"]["path"]
    db = JobDatabase(":memory:" if args.dry_run and not args.db else db_path)
    stats = Stats()
    extra: dict = {}
    health = HealthStore(db_path, writable=not args.dry_run or args.learn_health)
    matches = run(selected, settings, http, db, llm, stats, extra=extra, profiles=profiles, health=health)
    health.save()
    for issue in health.issues:
        log.warning("SOURCE NEEDS ATTENTION: %s", issue.line())
    report_html = None
    if args.report:
        report_html = render_html(banks, health.rows, run_summary=stats.summary(), health=health,
                                  matches_by_bank=stats.matches_by_bank)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_html, encoding="utf-8")
        log.info("Health report written to %s", args.report)

    # Deduplicate against the database: new jobs, or earlier jobs whose notification failed.
    if not run_main:
        matches = []
    to_notify = list(matches) if args.resend else [j for j in matches if db.needs_notification(j)]
    stats.new = sum(1 for j in matches if db.find(j) is None)
    log.info("New vacancies: %d", stats.new)

    exit_code = 0
    tcfg = settings["telegram"]
    if not run_main:
        pass
    elif args.dry_run:
        for j in to_notify:
            log.info("[dry-run] would notify: %s — %s (%s) %s", j.bank_name, j.title, j.location, j.url)
    else:
        for j in matches:
            db.record(j)
        if args.baseline:
            db.mark_notified(matches)
            log.info("Baseline stored: %d vacancies marked as notified", len(matches))
        elif to_notify or health.issues or tcfg.get("send_empty_digest", True):
            try:
                notifier = TelegramNotifier(env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID"))
                if tcfg["mode"] == "individual" and to_notify:
                    notifier.send_all(format_job(j) for j in to_notify)
                else:
                    notifier.send_all(format_digest(to_notify, max_jobs=tcfg["max_jobs_in_digest"],
                                                    failed_sources=stats.failed, health_issues=health.issues))
                db.mark_notified(to_notify)
                log.info("Telegram notification sent")
            except Exception as exc:  # noqa: BLE001
                log.error("Telegram notification failed (jobs stay pending for next run): %s", exc)
                exit_code = 1
    if report_html and run_main and not args.dry_run and not args.bank and settings["telegram"].get("send_health_report", True):
        try:
            TelegramNotifier(env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")).send_document(
                f"source-health-{date.today().isoformat()}.html", report_html.encode("utf-8"),
                caption=f"Source health: {len(health.issues)} need attention, see the attached report.")
            log.info("Health report sent to Telegram")
        except Exception as exc:  # noqa: BLE001
            log.error("Sending the health report failed: %s", exc)
    for name, pcfg in profiles.items():
        exit_code = deliver_profile(name, pcfg, extra.get(name, []), db, args, tcfg) or exit_code
    db.close()
    log.info("Summary:\n%s\nSources needing attention: %d", stats.summary(), len(health.issues))
    if stats.processed and stats.failed == stats.processed:
        log.error("Every source failed - check network access and banks.yaml")
        exit_code = exit_code or 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
