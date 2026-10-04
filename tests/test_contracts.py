"""Contract tests: every adapter runs against REAL recorded responses of its source (tests/contracts/<bank>.json.gz,
created by tools/capture_fixtures.py). Pins the number of vacancies and the first titles; also checks basic quality
(non-empty titles, absolute URLs, unique ids). When a bank changes its page, re-capture; when an adapter change alters the
result, the test shows the difference. Tests are skipped for banks without a recorded contract."""
from pathlib import Path

import pytest

from src.config import CONFIG_DIR, load_banks, load_settings
from src.utils.replay import ReplayHttpClient, ReplayMiss, check_contract, load_contract

CONTRACTS = sorted((Path(__file__).parent / "contracts").glob("*.json.gz"))
BANKS = {b.id: b for b in load_banks()}
SETTINGS = load_settings(CONFIG_DIR / "settings.yaml")


@pytest.mark.parametrize("path", CONTRACTS, ids=[p.name.removesuffix(".json.gz") for p in CONTRACTS])
def test_adapter_against_recorded_responses(path):
    doc = load_contract(path)
    bank = BANKS.get(doc["bank_id"])
    if bank is None or not bank.enabled:
        pytest.skip("bank is disabled or no longer in banks.yaml")
    problems = check_contract(bank, doc, bank.options.get("max_pages", SETTINGS["http"].get("max_pages", 30)))
    assert not problems, f"{bank.id}: " + "; ".join(problems) + " (re-capture if the change is intended)"


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


def test_recorded_error_status_is_replayed_as_http_error():
    import requests

    from src.utils.replay import _entry, request_key
    r = requests.Response()
    r.status_code, r.encoding, r._content = 404, "utf-8", b"not found"
    http = ReplayHttpClient([_entry(r, request_key("GET", "https://x.test/missing", {}))])
    with pytest.raises(requests.HTTPError):
        http.get("https://x.test/missing")
