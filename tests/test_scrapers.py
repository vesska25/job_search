import pytest

from src.scrapers import SCRAPERS, get_scraper
from src.scrapers.base import ScraperError
from src.scrapers.custom_api import CustomApiScraper
from src.scrapers.generic_html import GenericHtmlScraper
from src.scrapers.personio import PersonioScraper
from src.scrapers.smartrecruiters import SmartRecruitersScraper
from src.scrapers.softgarden import SoftgardenScraper
from src.scrapers.successfactors import SuccessFactorsScraper
from src.scrapers.workday import WorkdayScraper
from tests.conftest import fixture_json, fixture_text


def test_personio(bank):
    jobs = PersonioScraper(None).parse(fixture_text("personio.xml"), bank, "https://acme.jobs.personio.de")
    assert [j.source_job_id for j in jobs] == ["1001", "1002"]
    j = jobs[0]
    assert j.title.startswith("Head of Regulatory Reporting") and j.location == "Frankfurt am Main"
    assert j.url == "https://acme.jobs.personio.de/job/1001" and j.published_date == "2026-09-28"
    assert "disziplinarische" in j.description and j.department == "Finance & Controlling"


def test_personio_rejects_garbage(bank):
    with pytest.raises(ScraperError):
        PersonioScraper(None).parse("<html>blocked</html>", bank, "https://x")
    with pytest.raises(ScraperError):
        PersonioScraper(None).parse("not xml at all <", bank, "https://x")


def test_smartrecruiters(bank):
    jobs = SmartRecruitersScraper(None).parse(fixture_json("smartrecruiters.json"), bank, "acme")
    assert jobs[0].url == "https://jobs.smartrecruiters.com/acme/743999"
    assert (jobs[0].location, jobs[0].country, jobs[0].department) == ("Köln, NW", "DE", "Risk")
    assert jobs[1].country == "NL"
    with pytest.raises(ScraperError):
        SmartRecruitersScraper(None).parse({"error": 1}, bank, "acme")


def test_smartrecruiters_company_id(bank):
    bank.jobs_url = "https://careers.smartrecruiters.com/AcmeBank/"
    assert SmartRecruitersScraper.company_id(bank) == "AcmeBank"


def test_workday(bank):
    bank.jobs_url = "https://acme.wd3.myworkdayjobs.com/en-US/Careers"
    base, api, site = WorkdayScraper.endpoints(bank)
    assert api == "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Careers/jobs" and site == "Careers"
    jobs = WorkdayScraper(None).parse(fixture_json("workday.json"), bank, base, site)
    assert jobs[0].url == "https://acme.wd3.myworkdayjobs.com/Careers/job/Frankfurt/Director-Capital-Management_R-100"
    assert jobs[0].source_job_id == "R-100" and jobs[1].location == "London"


def test_successfactors(bank):
    jobs = SuccessFactorsScraper(None).parse(fixture_text("successfactors.html"), bank, "https://career.acme.de")
    assert len(jobs) == 2
    assert jobs[0].title.startswith("Bereichsleiter Finanzen") and jobs[0].source_job_id == "1234567890"
    assert jobs[0].location == "Frankfurt am Main, DE" and jobs[0].department == "Finance"
    with pytest.raises(ScraperError):
        SuccessFactorsScraper(None).parse("<html><body><p>Something else entirely</p></body></html>", bank, "https://x")


def test_softgarden(bank):
    jobs = SoftgardenScraper(None).parse(fixture_text("softgarden.html"), bank, "https://acme.softgarden.io")
    assert [j.source_job_id for j in jobs] == ["45678901", "45678902"]
    assert jobs[0].title == "Teamleiter Treasury (m/w/d)" and jobs[0].location == "Düsseldorf"


def test_custom_api(bank):
    bank.options = {"api_url": "https://x/api", "items_path": "data.jobs",
                    "fields": {"title": "headline", "id": "ref", "location": "city", "department": "team", "date": "posted"},
                    "url_template": "https://x/job/{id}"}
    jobs = CustomApiScraper(None).parse(fixture_json("custom_api.json"), bank)
    assert jobs[0].url == "https://x/job/A1" and jobs[0].title == "Managing Director Treasury"
    assert jobs[0].published_date == "2026-09-01" and jobs[1].location == "Hamburg"
    bank.options["items_path"] = "wrong.path"
    with pytest.raises(ScraperError):
        CustomApiScraper(None).parse(fixture_json("custom_api.json"), bank)


def test_generic_html_jsonld(bank):
    jobs = GenericHtmlScraper(None).parse(fixture_text("jsonld.html"), bank)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "Head of Finance Transformation" and j.country == "DE" and j.source_job_id == "555"
    assert j.canonical_url == "https://careers.example.bank/jobs/555"


def test_generic_html_link_heuristic(bank):
    jobs = GenericHtmlScraper(None).parse(fixture_text("links.html"), bank)
    assert [j.title for j in jobs] == ["Leiter Risikocontrolling (m/w/d)", "Werkstudent IT"]
    assert jobs[0].url == "https://careers.example.bank/stellenangebote/leiter-risikocontrolling-123"


def test_generic_html_selectors(bank):
    bank.options = {"selectors": {"item": "li", "title": "a", "link": "a"}}
    assert len(GenericHtmlScraper(None).parse(fixture_text("links.html"), bank)) == 3


def test_tiny_page_raises(bank):
    with pytest.raises(ScraperError):
        GenericHtmlScraper(None).parse("<html></html>", bank)


def test_registry_covers_all_types():
    for t in ("personio", "workday", "successfactors", "smartrecruiters", "softgarden", "custom_api",
              "custom_html", "sparkasse", "proprietary", "beesite"):
        assert t in SCRAPERS
    with pytest.raises(ScraperError):
        get_scraper("unknown", None)


def test_beesite_parse(bank):
    from src.scrapers.beesite import BeeSiteScraper
    bank.jobs_url = "https://jobs.example.bank/"
    jobs, total = BeeSiteScraper(None).parse(fixture_json("beesite.json"), bank)
    assert total == 3 and [j.source_job_id for j in jobs] == ["R-1001", "R-1002"]   # job without URI skipped
    assert jobs[0].location == "Frankfurt am Main" and jobs[0].country == "DE" and jobs[0].department == "Finance"
    assert jobs[1].url == "https://jobs.example.bank/job/1002" and jobs[1].location == "London"
    with pytest.raises(ScraperError):
        BeeSiteScraper(None).parse({"error": "x"}, bank)


def test_beesite_request_and_pagination(bank):
    import json
    from src.scrapers.beesite import BeeSiteScraper
    bank.jobs_url = "https://jobs.example.bank/"
    bank.options = {"api_url": "https://api.example.bank/search", "page_size": 2, "first_item": 1, "score_threshold": 100,
                    "criteria": [{"CriterionName": "PositionLocation.Country", "CriterionValue": ["X"]}]}
    pages = [
        {"SearchResult": {"SearchResultCountAll": 3, "SearchResultItems": [
            {"MatchedObjectDescriptor": {"PositionID": str(i), "PositionTitle": f"T{i}", "PositionURI": f"/j/{i}"}} for i in (1, 2)]}},
        {"SearchResult": {"SearchResultCountAll": 3, "SearchResultItems": [
            {"MatchedObjectDescriptor": {"PositionID": "3", "PositionTitle": "T3", "PositionURI": "/j/3"}}]}},
    ]
    calls = []

    class Http:
        def get(self, url, params=None, **kw):
            calls.append((url, json.loads(params["data"])))
            return type("R", (), {"json": lambda s: pages[len(calls) - 1]})()

    jobs = BeeSiteScraper(Http()).fetch_jobs(bank)
    assert [j.title for j in jobs] == ["T1", "T2", "T3"] and len(calls) == 2
    url, body = calls[0]
    assert url == "https://api.example.bank/search/"
    assert body["SearchParameters"]["FirstItem"] == 1 and body["SearchParameters"]["CountItem"] == 2
    assert body["ScoreThreshold"] == 100 and body["SearchCriteria"][0]["CriterionName"] == "PositionLocation.Country"
    assert calls[1][1]["SearchParameters"]["FirstItem"] == 3


def test_generic_html_accordion_without_links(bank):
    bank.jobs_url = "https://www.example.bank/karriere.html"
    bank.options = {"selectors": {"item": '[data-render-component="okp-akkordeon-tab"] details',
                                  "title": "summary.cms-title", "no_link": True}}
    jobs = GenericHtmlScraper(None).parse(fixture_text("accordion.html"), bank)
    assert [j.title for j in jobs] == ["Senior Manager Finanzcontrolling (m/w/d)", "Mitarbeiter im Kundenservicecenter (m/w/d) in Vollzeit"]
    assert jobs[0].url == "https://www.example.bank/karriere.html?job=senior_manager_finanzcontrolling_m_w_d"
    assert jobs[0].canonical_url != jobs[1].canonical_url            # distinct identity -> no dedup collision
    assert "disziplinarische" in jobs[0].description


def test_accordion_job_flows_through_filters(settings, de_bank):
    from src.filters.pipeline import evaluate
    de_bank.jobs_url = "https://www.example.bank/karriere.html"
    de_bank.options = {"selectors": {"item": "details", "title": "summary.cms-title", "no_link": True}}
    jobs = GenericHtmlScraper(None).parse(fixture_text("accordion.html"), de_bank)
    d = evaluate(jobs[0], de_bank, settings)
    assert d.accepted and not d.borderline            # 'Senior Manager' + Finanzcontrolling + management signal
    assert not evaluate(jobs[1], de_bank, settings).accepted


def test_generic_html_text_lines(bank):
    html = '<a href="/karriere/offene-stellen/a-1"><span>Frankfurt am Main</span><span>Gruppenleiter Payments (m/w/d)</span></a>' \
           '<a href="/karriere/offene-stellen/b-2"><span>München</span><span>Senior Accountant (m/w/d)</span></a>' \
           '<a href="/karriere/offene-stellen/c-3"><span>nur eine Zeile</span></a>'
    bank.jobs_url = "https://www.metzler.com/de/metzler/karriere/stellenangebote"
    bank.options = {"selectors": {"item": 'a[href^="/karriere/offene-stellen/"]', "text_lines": {"location": 0, "title": 1}}}
    jobs = GenericHtmlScraper(None).parse(html, bank)
    assert [(j.location, j.title) for j in jobs] == [("Frankfurt am Main", "Gruppenleiter Payments (m/w/d)"), ("München", "Senior Accountant (m/w/d)")]
    assert jobs[0].url == "https://www.metzler.com/karriere/offene-stellen/a-1"


def test_link_pattern_umantis_style(bank):
    html = '<a href="/Vacancies/1143/Description/1">Revisor (d/m/w) Prüfungsschwerpunkt Risikomanagement</a>' \
           '<a href="/Vacancies/1143/Application/CheckLogin/1">Jetzt bewerben</a>'
    bank.jobs_url = "https://recruitingapp-2764.umantis.com/Jobs/1?lang=ger"
    bank.options = {"link_pattern": r"/Vacancies/\d+/Description/"}
    jobs = GenericHtmlScraper(None).parse(html, bank)
    assert len(jobs) == 1 and jobs[0].url.endswith("/Vacancies/1143/Description/1")


def test_successfactors_tile_layout_dedupes_and_reads_location(bank):
    jobs = SuccessFactorsScraper(None).parse(fixture_text("successfactors_tiles.html"), bank, "https://karriere.nrwbank.de")
    assert [j.title for j in jobs] == ["IT-Projektmanager Risikosysteme (w/m/d)", "Spezialist IAM (w/m/d)"]
    assert jobs[0].location == "Düsseldorf, DE" and jobs[1].location == "Münster, DE"
    assert jobs[0].source_job_id == "1162119801"


def test_successfactors_empty_second_page_is_end_of_list(bank):
    class Http:
        def __init__(self):
            self.n = 0

        def get(self, url, params=None, **kw):
            self.n += 1
            text = fixture_text("successfactors_tiles.html") if self.n == 1 else "<html><body><p>Seite</p></body></html>"
            return type("R", (), {"text": text})()

    bank.jobs_url = "https://karriere.nrwbank.de/"
    jobs = SuccessFactorsScraper(Http()).fetch_jobs(bank)
    assert len(jobs) == 2          # page 1 results survive the unrecognised page 2


def test_rss_feed(bank):
    from src.scrapers.rss import RssScraper
    bank.options = {"location_regex": r"\(([^()]+)\)\s*$"}
    jobs = RssScraper(None).parse(fixture_text("jobs_feed.xml").encode("utf-8"), bank)
    assert [j.source_job_id for j in jobs] == ["2026-014", "2026-015"]      # item without title skipped
    assert jobs[0].location == "Frankfurt am Main" and jobs[1].location == "Luxemburg"
    assert jobs[0].published_date == "2026-09-29" and "disziplinarische" in jobs[0].description
    with pytest.raises(ScraperError):
        RssScraper(None).parse(b"<html><body>blocked</body></html>", bank)
