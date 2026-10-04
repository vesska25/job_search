"""Record the real responses of each source into tests/contracts/<bank_id>.json.gz (needs network; run it from the
'Capture contract fixtures' workflow). tests/test_contracts.py replays them against the adapters.

  python -m tools.capture_fixtures                    # all enabled banks
  python -m tools.capture_fixtures --bank sparkasse_koelnbonn --bank ing
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from src.config import CONFIG_DIR, ROOT, load_banks, load_settings
from src.scrapers import get_scraper
from src.utils.http import HttpClient
from src.utils.logging import get_logger, setup_logging
from src.utils.replay import RecordingHttpClient, check_contract, load_contract, save_contract

log = get_logger("capture")
OUT = ROOT / "tests" / "contracts"
MAX_FILE = 4_000_000      # compressed bytes per bank


def capture(bank, settings, cache: dict | None = None) -> tuple[str, int, int]:
    """Returns (status, vacancies, bytes). status: saved | empty | too_big | failed: <reason>"""
    h = settings["http"]
    http = RecordingHttpClient(user_agent=h["user_agent"], timeout=h["timeout"], retries=h["retries"],
                               min_delay=h["min_delay_seconds"], respect_robots=h["respect_robots_txt"], cache=cache)
    try:
        scraper = get_scraper(bank.source_type, http, max_pages=bank.options.get("max_pages", h.get("max_pages", 30)))
        jobs = scraper.fetch_jobs(bank)
    except Exception as exc:  # noqa: BLE001
        return f"failed: {type(exc).__name__}: {exc}"[:200], 0, 0
    if not jobs:
        return "empty", 0, 0
    if http.skipped:
        return f"too_big: {http.skipped} responses over the size/entry cap", len(jobs), 0
    path = OUT / f"{bank.id}.json.gz"
    size = save_contract(path, bank.id, jobs, http.entries, date.today().isoformat())
    if size > MAX_FILE:
        path.unlink()
        return "too_big", len(jobs), size
    problems = check_contract(bank, load_contract(path), bank.options.get("max_pages", h.get("max_pages", 30)))
    if problems:                                   # a contract that does not hold is a finding, not a fixture
        path.unlink()
        return "invalid: " + "; ".join(problems)[:160], len(jobs), 0
    return "saved", len(jobs), size


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", action="append")
    args = ap.parse_args(argv)
    setup_logging("INFO")
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    banks = [b for b in load_banks() if (b.id in args.bank if args.bank else b.enabled)]
    saved, cache = 0, {}
    for b in banks:
        status, n, size = capture(b, settings, cache)
        saved += status == "saved"
        log.info("%-40s %-10s vacancies=%-4d %s", b.id, status.split(":")[0], n, f"{size/1024:.0f} KiB" if size else status)
    log.info("Saved %d of %d contracts", saved, len(banks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
