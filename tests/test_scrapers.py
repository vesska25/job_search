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
              "custom_html", "sparkasse", "proprietary"):
        assert t in SCRAPERS
    with pytest.raises(ScraperError):
        get_scraper("unknown", None)
