"""Contract tests: every adapter runs against REAL recorded responses of its source (tests/contracts/<bank>.json.gz,
created by tools/capture_fixtures.py). Pins the number of vacancies and the first titles; also checks basic quality
(non-empty titles, absolute URLs, unique ids). When a bank changes its page, re-capture; when an adapter change alters the
result, the test shows the difference. Tests are skipped for banks without a recorded contract."""
from pathlib import Path

import pytest

from src.config import CONFIG_DIR, load_banks, load_settings
from src.scrapers import get_scraper
from src.utils.replay import ReplayHttpClient, ReplayMiss, load_contract

CONTRACTS = sorted((Path(__file__).parent / "contracts").glob("*.json.gz"))
BANKS = {b.id: b for b in load_banks()}
SETTINGS = load_settings(CONFIG_DIR / "settings.yaml")


@pytest.mark.parametrize("path", CONTRACTS, ids=[p.name.removesuffix(".json.gz") for p in CONTRACTS])
def test_adapter_against_recorded_responses(path):
    doc = load_contract(path)
    bank = BANKS.get(doc["bank_id"])
    if bank is None or not bank.enabled:
        pytest.skip("bank is disabled or no longer in banks.yaml")
    scraper = get_scraper(bank.source_type, ReplayHttpClient(doc["entries"]),
                          max_pages=bank.options.get("max_pages", SETTINGS["http"].get("max_pages", 30)))
    try:
        jobs = scraper.fetch_jobs(bank)
    except ReplayMiss as exc:
        pytest.fail(f"{bank.id}: the adapter now requests something that was not recorded (re-capture if intended): {exc}")
    assert len(jobs) == doc["count"], f"{bank.id}: {len(jobs)} vacancies, recorded {doc['count']}"
    assert [j.title for j in jobs[:3]] == doc["sample_titles"]
    assert all(len(j.title.strip()) >= 3 for j in jobs), "empty or tiny title"
    assert all(j.url.startswith("http") for j in jobs), "relative or empty URL"
    ids = [j.job_id for j in jobs]
    assert len(ids) == len(set(ids)), "duplicate job ids"


def test_replay_roundtrip_and_miss():
    import requests

    from src.utils.replay import _entry, request_key
    r = requests.Response()
    r.status_code, r.encoding, r._content = 200, "utf-8", "Düsseldorf".encode()
    r.headers["Content-Type"] = "text/html; charset=utf-8"
    key = request_key("GET", "https://x.test/a", {"params": {"p": 1}})
    http = ReplayHttpClient([_entry(r, key)])
    assert http.get("https://x.test/a", params={"p": 1}).text == "Düsseldorf"
    with pytest.raises(ReplayMiss):
        http.get("https://x.test/a", params={"p": 2})
