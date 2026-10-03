import yaml

from src.config import CONFIG_DIR, load_banks
from src.main import Stats, run, main
from src.scrapers import SCRAPERS
from src.storage.database import JobDatabase
from src.utils.http import HttpClient
from tests.conftest import FIX


def test_banks_yaml_consistency():
    banks = load_banks()
    assert len(banks) == 278
    ids = {b.id for b in banks}
    for b in banks:
        if b.alias_of:
            assert b.alias_of in ids and not b.enabled
        if b.enabled:
            assert b.jobs_url and b.source_type in SCRAPERS
        if b.source_type == "unknown":
            assert not b.enabled
            if not b.alias_of and not b.jobs_url:
                assert "No official Germany-specific vacancy source verified" in b.notes
            if b.jobs_url and not b.alias_of:
                # known page, adapter still to be configured or deliberately not monitored (documented in notes)
                assert b.verification_status in {"needs_review", "js_list", "js_widget", "blocked_robots", "no_vacancies_now"}
        # nothing may claim verification without a date
        if b.verification_status == "ok":
            assert b.last_verified


def test_credit_suisse_mapped_to_ubs():
    cs = next(b for b in load_banks() if "Credit Suisse" in b.name)
    assert cs.alias_of == "ubs_europe" and not cs.enabled


class FakeResp:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status
        self.content = text.encode("utf-8")
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def json(self):
        import json
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    """Routes URL -> fixture; anything else raises like a dead host."""
    def __init__(self, routes):
        self.routes, self.headers = routes, {}

    def request(self, method, url, **kw):
        if url.endswith("/robots.txt"):
            return FakeResp("", 404)
        for key, text in self.routes.items():
            if key in url:
                return FakeResp(text)
        import requests
        raise requests.ConnectionError("blocked")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def mount(self, *a):
        pass


def test_end_to_end_with_failing_bank(settings, tmp_path):
    from src.config import Bank
    good = Bank(id="good", name="Good Bank", jobs_url="https://acme.jobs.personio.de/", source_type="personio",
                enabled=True, germany_only=True)
    dead = Bank(id="dead", name="Dead Bank", jobs_url="https://dead.example/", source_type="custom_html", enabled=True)
    session = FakeSession({"acme.jobs.personio.de/xml": (FIX / "personio.xml").read_text(encoding="utf-8")})
    http = HttpClient(session=session, min_delay=0)
    db, stats = JobDatabase(tmp_path / "t.db"), Stats()
    matches = run([dead, good], settings, http, db, None, stats)
    assert stats.processed == 2 and stats.successful == 1 and stats.failed == 1   # failure isolated
    assert stats.scanned == 2 and stats.leadership == 1
    assert [m.title for m in matches] == ["Head of Regulatory Reporting (m/w/d)"]   # Werkstudent excluded
    assert all(db.needs_notification(m) for m in matches)
    for m in matches:
        db.record(m)
    db.mark_notified(matches)
    matches2 = run([good], settings, http, db, None, Stats())
    assert not [m for m in matches2 if db.needs_notification(m)]      # second run: nothing new


def test_robots_disallow_is_respected():
    class S(FakeSession):
        def request(self, method, url, **kw):
            if url.endswith("/robots.txt"):
                return FakeResp("User-agent: *\nDisallow: /secret", 200)
            return FakeResp("ok")
    http = HttpClient(session=S({}), min_delay=0)
    assert http.get("https://a.example/public").text == "ok"
    import pytest
    from src.utils.http import RobotsDisallowed
    with pytest.raises(RobotsDisallowed):
        http.get("https://a.example/secret/x")


def test_dry_run_cli_with_no_enabled_banks(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "settings.yaml").write_text((CONFIG_DIR / "settings.yaml").read_text())
    (cfg / "banks.yaml").write_text(yaml.safe_dump({"banks": [{"id": "x", "name": "X", "enabled": False}]}))
    assert main(["--config-dir", str(cfg), "--dry-run"]) == 0


def test_detail_location_resolved_only_for_filtered_jobs(settings):
    from src.config import Bank
    bank = Bank(id="dbg", name="Deutsche Börse Group", jobs_url="https://careers.example/search", source_type="custom_html",
                enabled=True, options={"link_pattern": "offer-redirect", "detail_location": {"regex": r"\(([^()]*)\)\s*›"}})
    listing = ('<a href="/offer-redirect/?offerApiId=AAA">Head of Regulatory Reporting (f/m/d)</a>'
               '<a href="/offer-redirect/?offerApiId=BBB">Head of Risk Control (f/m/d)</a>'
               '<a href="/offer-redirect/?offerApiId=CCC">Intern - Risk (f/m/d)</a>')
    fetched = []

    class S(FakeSession):
        def request(self, method, url, **kw):
            if url.endswith("/robots.txt"):
                return FakeResp("", 404)
            if url.endswith("/search"):
                return FakeResp(listing)
            fetched.append(url)
            city = "Frankfurt am Main" if "AAA" in url else "Prague"
            return FakeResp(f"<html><title>Head of X (f/m/d) ({city}) › Deutsche Börse Group</title></html>")

    http = HttpClient(session=S({}), min_delay=0)
    matches = run([bank], settings, http, JobDatabase(":memory:"), None, Stats())
    assert [m.title for m in matches] == ["Head of Regulatory Reporting (f/m/d)"]
    assert matches[0].location == "Frankfurt am Main"
    assert len(fetched) == 2          # intern never fetched; Prague job fetched, then rejected by the Germany filter


def test_missing_charset_is_detected_not_latin1():
    """Servers without a charset must not turn 'Düsseldorf' into 'DÃ¼sseldorf'."""
    import requests
    from src.utils.http import HttpClient
    body = "<html><body>Bereichsleiter Finanzen – Düsseldorf, Köln</body></html>".encode("utf-8")
    resp = requests.Response()
    resp._content, resp.status_code = body, 200
    resp.headers["Content-Type"] = "text/html"
    resp.encoding = "ISO-8859-1"          # what requests assumes without a charset
    HttpClient._fix_encoding(resp)
    assert "Düsseldorf" in resp.text and "Köln" in resp.text
    # a declared charset is left alone
    resp2 = requests.Response()
    resp2._content, resp2.status_code = "Düsseldorf".encode("latin-1"), 200
    resp2.headers["Content-Type"] = "text/html; charset=iso-8859-1"
    resp2.encoding = "iso-8859-1"
    HttpClient._fix_encoding(resp2)
    assert resp2.text == "Düsseldorf"
