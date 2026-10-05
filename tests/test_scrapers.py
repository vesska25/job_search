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


def test_custom_api_offset_pagination_without_total_path(bank):
    pages = {0: [{"t": "A", "i": "1", "u": "/a"}, {"t": "B", "i": "2", "u": "/b"}], 2: [{"t": "C", "i": "3", "u": "/c"}], 4: []}

    class Http:
        def request(self, method, url, params=None, **kw):
            class R:
                def json(_):
                    return {"jobsList": pages[params["start"]]}
            return R()

    bank.options = {"api_url": "https://x/api", "items_path": "jobsList",
                    "fields": {"title": "t", "id": "i", "url": "u"},
                    "pagination": {"type": "offset", "param": "start", "size_param": "rows", "size": 2, "max_pages": 5}}
    jobs = CustomApiScraper(Http()).fetch_jobs(bank)
    assert [j.title for j in jobs] == ["A", "B", "C"]


def test_generic_html_selector_title_fallbacks(bank):
    html = ('<a class="job" href="/open-positions/senior-trade-finance-specialist"> </a>'
            '<a class="job" href="/karriere/team-head-export-finance" title="Team Head Export Finance (m/w/d)"> </a>'
            '<a class="job" href="/karriere/x">Leiter Risiko</a>')
    bank.jobs_url = "https://x.example/jobs"
    bank.options = {"selectors": {"item": "a.job"}}
    titles = [j.title for j in GenericHtmlScraper(None).parse(html, bank)]
    assert titles == ["Senior Trade Finance Specialist", "Team Head Export Finance (m/w/d)", "Leiter Risiko"]


def test_generic_html_title_from_attribute(bank):
    html = ('<a class="card" title="Zur Stellenbeschreibung Leitung Innovation Hub (m/w/d)" href="/jobs/detail/leitung/">'
            '<span>Leitung Innovation Hub</span><span>Köln</span><span>Vollzeit</span></a>')
    bank.jobs_url = "https://x.example/jobs/"
    bank.options = {"selectors": {"item": "a.card", "title_attr": "title", "title_strip": "^Zur Stellenbeschreibung\\s+"}}
    jobs = GenericHtmlScraper(None).parse(html, bank)
    assert [j.title for j in jobs] == ["Leitung Innovation Hub (m/w/d)"]


def test_generic_html_base_url_option(bank):
    html = '<a href="de/job-offer-list/job-detail/Teamleitung-Bauorganisation-219.html">Teamleitung (m/w/d) Bauorganisation</a>'
    bank.jobs_url = "https://sls.example/de/"
    bank.options = {"link_pattern": r"job-detail/[^/]+\.html$", "base_url": "https://sls.example/"}
    jobs = GenericHtmlScraper(None).parse(html, bank)
    assert jobs[0].url == "https://sls.example/de/job-offer-list/job-detail/Teamleitung-Bauorganisation-219.html"


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
              "custom_html", "sparkasse", "proprietary", "beesite", "sparkasse_jobmarket", "vr_jobs", "sitemap_jobs"):
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


def test_location_regex_from_title(bank):
    html = '<a href="/naspa/position-130257">Leitung Accounting / Rechnungswesen (m/w/d) in Wiesbaden</a>' \
           '<a href="/naspa/position-131943">Gewerbekundenberater (m/w/d) für unsere Standorte</a>'
    bank.jobs_url = "https://www.naspa.de/jobs"
    bank.options = {"link_pattern": r"/naspa/position-\d+", "location_regex": r"\bin ([A-ZÄÖÜ][^()]*)$"}
    jobs = GenericHtmlScraper(None).parse(html, bank)
    assert [j.location for j in jobs] == ["Wiesbaden", ""]
    assert jobs[0].url == "https://www.naspa.de/naspa/position-130257"


def test_sparkasse_koelnbonn_style_cards(bank):
    html = '<div class="job__item"><div class="job__content"><h3>Teamleitung Kreditrisikocontrolling (m/w/d) bei der Sparkasse KölnBonn</h3></div>' \
           '<a href="/jobs/cb81-00020a/teamleitung-kreditrisikocontrolling/" class="button button--red">Mehr Info</a></div>' \
           '<div class="job__item"><h3>Schülerpraktikum (m/w/d)</h3><a href="/jobs/cb81-000082/schuelerpraktikum/" class="button">Mehr Info</a></div>'
    bank.jobs_url = "https://karriere.sparkasse-koelnbonn.de/jobs/"
    bank.options = {"selectors": {"item": "div.job__item", "title": "h3", "link": "a.button"}}
    jobs = GenericHtmlScraper(None).parse(html, bank)
    assert [j.title for j in jobs][0].startswith("Teamleitung Kreditrisikocontrolling") and len(jobs) == 2
    assert jobs[0].url == "https://karriere.sparkasse-koelnbonn.de/jobs/cb81-00020a/teamleitung-kreditrisikocontrolling/"


def test_altays_fragment_with_country_badge(settings, bank):
    from src.filters.pipeline import evaluate
    bank.jobs_url = "https://recrutement.altays-progiciels.com/oddo/de/offres.html"
    bank.options = {"selectors": {"item": "li.jobs__detail", "title": ".jobs__detail__title a",
                                  "link": ".jobs__detail__title a", "location": ".badges--color-2"}}
    jobs = GenericHtmlScraper(None).parse(fixture_text("altays_offers.html"), bank)
    assert [j.location for j in jobs] == ["Deutschland", "Frankreich"]
    assert jobs[0].url == "https://recrutement.altays-progiciels.com/oddo/de/offres/leiter-mwd-regulatory-reporting-frankfurt-2887001.html"
    assert evaluate(jobs[0], bank, settings).accepted               # 'Leiter' + Regulatory Reporting + Deutschland
    assert not evaluate(jobs[1], bank, settings).accepted           # German country name must be recognised as foreign


def test_sparkasse_jobmarket_parse_and_filter(bank):
    from src.scrapers.sparkasse_jobmarket import SparkasseJobMarketScraper
    bank.options = {"bank_code": "50050201"}
    jobs, total = SparkasseJobMarketScraper(None).parse(fixture_json("sparkasse_jobmarket.json"), bank)
    assert total == 3 and [j.source_job_id for j in jobs] == ["212947", "212948"]   # other bank's item dropped
    assert jobs[0].location == "Frankfurt" and jobs[0].department.startswith("Rechnungswesen")
    assert jobs[1].location == "Frankfurt am Main"                                   # falls back to the client's city
    assert jobs[0].url == "https://www.sparkasse.de/jobboerse/jobangebot/leiter-rechnungswesen-w-m-d-212947.html"
    with pytest.raises(ScraperError):
        SparkasseJobMarketScraper(None).parse({"x": 1}, bank)


def test_sparkasse_jobmarket_pagination(bank):
    from src.scrapers.sparkasse_jobmarket import SparkasseJobMarketScraper
    bank.options = {"bank_code": "1", "page_size": 2}
    mk = lambda i: {"id": i, "title": f"T{i}", "client": {"bankCode": "1"}, "addresses": [{"city": "Hanau"}]}
    pages = [{"jobs": {"count": 3, "items": [mk(1), mk(2)]}}, {"jobs": {"count": 3, "items": [mk(3)]}}]
    calls = []

    class Http:
        def get(self, url, params=None, **kw):
            calls.append(params)
            return type("R", (), {"json": lambda self_: pages[len(calls) - 1]})()

    jobs = SparkasseJobMarketScraper(Http()).fetch_jobs(bank)
    assert len(jobs) == 3 and [c["offset"] for c in calls] == [0, 2] and calls[0]["bankCode"] == "1"


VR_SITEMAP = """<urlset>
<url><loc>https://www.vr.de/karriere/jobs/leiter-rechnungswesen-m-w-d-wiesbadener-volksbank-eg-abc123.html</loc></url>
<url><loc>https://www.vr.de/karriere/jobs/initiativbewerbung-wiesbadener-volksbank-eg-zzz999.html</loc></url>
<url><loc>https://www.vr.de/karriere/jobs/berater-m-w-d-volksbank-eg-qqq111.html</loc></url>
<url><loc>https://www.vr.de/karriere/jobs/referent-m-w-d-sparda-bank-nuernberg-eg-nnn222.html</loc></url>
</urlset>"""
VR_PAGE = """<html><body><script type="application/ld+json">{"@type":"JobPosting","title":"Leiter Rechnungswesen (m/w/d)",
"datePosted":"2026-06-22T07:10:10.799Z","jobLocation":{"address":{"addressLocality":"Wiesbaden","addressCountry":"DE"}},
"url":"https://www.vr.de/karriere/jobs/leiter-rechnungswesen-m-w-d-wiesbadener-volksbank-eg-abc123.html"}</script></body></html>"""


def _vr_http(pages):
    class R:
        def __init__(self, text):
            self.text = text

    class Http:
        calls = []

        def get(self, url, **kw):
            self.calls.append(url)
            return R(pages[url])
    return Http()


def test_vr_jobs_matches_bank_by_slug_and_reads_jsonld(bank):
    from src.scrapers.vr_jobs import SITEMAP_URL, VrJobsScraper
    bank.name, bank.options = "Wiesbadener Volksbank eG", {}
    detail = "https://www.vr.de/karriere/jobs/leiter-rechnungswesen-m-w-d-wiesbadener-volksbank-eg-abc123.html"
    http = _vr_http({SITEMAP_URL: VR_SITEMAP, detail: VR_PAGE})
    jobs = VrJobsScraper(http).fetch_jobs(bank)
    assert [(j.title, j.location, j.source_job_id) for j in jobs] == [("Leiter Rechnungswesen (m/w/d)", "Wiesbaden", "abc123")]
    assert http.calls == [SITEMAP_URL, detail]          # initiativbewerbung page and other banks never fetched


def test_vr_jobs_unknown_bank_is_an_error_but_bank_without_vacancies_is_not(bank):
    from src.scrapers.vr_jobs import SITEMAP_URL, VrJobsScraper
    http = _vr_http({SITEMAP_URL: VR_SITEMAP})
    bank.name, bank.options = "Gibt Es Nicht eG", {}
    with pytest.raises(ScraperError):
        VrJobsScraper(http).fetch_jobs(bank)
    bank.options = {"vr_slug": "sparda-bank-nuernberg-eg"}      # only an explicit slug override; one referent page, no JSON-LD
    http2 = _vr_http({SITEMAP_URL: VR_SITEMAP, "https://www.vr.de/karriere/jobs/referent-m-w-d-sparda-bank-nuernberg-eg-nnn222.html": "<html></html>"})
    assert VrJobsScraper(http2).fetch_jobs(bank) == []


def test_vr_jobs_skips_expired_vacancy_pages(bank):
    import requests
    from src.scrapers.vr_jobs import SITEMAP_URL, VrJobsScraper
    bank.name, bank.options = "Wiesbadener Volksbank eG", {}

    class Http:
        def get(self, url, **kw):
            if url == SITEMAP_URL:
                return type("R", (), {"text": VR_SITEMAP})()
            resp = requests.Response()
            resp.status_code = 404
            raise requests.HTTPError("404", response=resp)

    assert VrJobsScraper(Http()).fetch_jobs(bank) == []


SM = """<urlset>
<url><loc>https://jobs.example.bank/stellenangebote/senior-risikomanager-w-m-d/</loc></url>
<url><loc>https://jobs.example.bank/stellenangebote/ausbildung-bankkaufmann-wmd-rostock/</loc></url>
<url><loc>https://jobs.example.bank/stellenangebote/leiter-compliance-in-duesseldorf/</loc></url>
<url><loc>https://jobs.example.bank/stellenangebote/abgelaufen-stelle/</loc></url>
<url><loc>https://jobs.example.bank/story/ein-artikel/</loc></url>
</urlset>"""


def _sm_http(pages, errors=()):
    import requests

    class Http:
        calls = []

        def get(self, url, **kw):
            self.calls.append(url)
            if url in errors:
                r = requests.Response()
                r.status_code = 404
                raise requests.HTTPError("404", response=r)
            return type("R", (), {"text": pages[url]})()
    return Http()


def test_sitemap_jobs_filters_urls_and_reads_h1_or_jsonld(bank):
    from src.scrapers.sitemap_jobs import SitemapJobsScraper
    base = "https://jobs.example.bank/stellenangebote/"
    bank.options = {"sitemap_url": "https://jobs.example.bank/sm.xml", "url_regex": "/stellenangebote/",
                    "skip_slug": "^ausbildung", "location_regex": r"\bin ([A-Za-zÄÖÜäöü]+)$", "max_pages": 50}
    jsonld = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Senior Risikomanager (m/w/d)",'
              '"jobLocation":{"address":{"addressLocality":"Frankfurt"}},"url":"x"}</script>')
    http = _sm_http({"https://jobs.example.bank/sm.xml": SM,
                     base + "senior-risikomanager-w-m-d/": f"<html>{jsonld}<h1>ignored</h1></html>",
                     base + "leiter-compliance-in-duesseldorf/": "<html><h1>Leiter Compliance in Düsseldorf</h1></html>"},
                    errors={base + "abgelaufen-stelle/"})
    jobs = SitemapJobsScraper(http, max_pages=50).fetch_jobs(bank)
    assert [(j.title, j.location, j.source_job_id) for j in jobs] == [
        ("Senior Risikomanager (m/w/d)", "Frankfurt", "senior-risikomanager-w-m-d"),
        ("Leiter Compliance in Düsseldorf", "Düsseldorf", "leiter-compliance-in-duesseldorf")]
    assert jobs[0].url == base + "senior-risikomanager-w-m-d/"
    assert base + "ausbildung-bankkaufmann-wmd-rostock/" not in http.calls and not any("story" in c for c in http.calls)


def test_sitemap_jobs_without_detail_uses_slug_and_requires_sitemap(bank):
    from src.scrapers.sitemap_jobs import SitemapJobsScraper
    bank.options = {"sitemap_url": "https://jobs.example.bank/sm.xml", "url_regex": "/stellenangebote/", "detail": False}
    jobs = SitemapJobsScraper(_sm_http({"https://jobs.example.bank/sm.xml": SM})).fetch_jobs(bank)
    assert jobs[0].title == "Senior Risikomanager W M D" and len(jobs) == 4
    bank.options = {}
    with pytest.raises(ScraperError):
        SitemapJobsScraper(None).fetch_jobs(bank)


def test_sitemap_jobs_key_uses_query_when_id_is_in_query(bank):
    from src.scrapers.sitemap_jobs import SitemapJobsScraper, slug_of
    assert slug_of("https://jobs.kfw.de/index.php?ac=jobad&id=13637") == "ac=jobad&id=13637"
    assert slug_of("https://jobs.example.bank/stellenangebote/foo-bar/") == "foo-bar"
    sm = ("<urlset><url><loc>https://jobs.kfw.de/index.php?ac=jobad&amp;id=1</loc></url>"
          "<url><loc>https://jobs.kfw.de/index.php?ac=jobad&amp;id=2</loc></url>"
          "<url><loc>https://jobs.kfw.de//index.php?ac=contact&amp;language=1</loc></url></urlset>")
    bank.options = {"sitemap_url": "https://jobs.kfw.de/sitemap.xml", "url_regex": "ac=jobad"}
    pages = {"https://jobs.kfw.de/sitemap.xml": sm,
             "https://jobs.kfw.de/index.php?ac=jobad&id=1": "<h1>Referent (m/w/d) Treasury</h1>",
             "https://jobs.kfw.de/index.php?ac=jobad&id=2": "<h1>Teamleiter (m/w/d) Risiko</h1>"}
    jobs = SitemapJobsScraper(_sm_http(pages)).fetch_jobs(bank)
    assert [j.job_id.split(":", 1)[1] for j in jobs] == ["ac=jobad&id=1", "ac=jobad&id=2"] and len({j.job_id for j in jobs}) == 2


def test_custom_api_url_template_with_own_id_field(bank):
    # Eightfold PCSX style: the item has its own 'id' and no url field -> url_template must not clash with it
    bank.options = {"api_url": "https://x/api", "items_path": "data.positions",
                    "fields": {"title": "name", "id": "id", "location": "locations", "department": "department"},
                    "url_template": "https://x/careers/job/{id}"}
    data = {"data": {"positions": [{"id": 549799566259, "name": "Treasury Capital Planning and Management - VP",
                                    "locations": ["Frankfurt, Germany"], "department": "Corporate Treasury"}]}}
    jobs = CustomApiScraper(None).parse(data, bank)
    assert jobs[0].url == "https://x/careers/job/549799566259" and jobs[0].location == "Frankfurt, Germany"


def test_successfactors_city_from_url():
    from src.scrapers.successfactors import city_from_url
    assert city_from_url("https://careersemea.smbcgroup.com/job/Frankfurt-Director-Finance-and-Regulatory-Reporting-Technology-Lead-%28mwd%29-HE-60311/1412998233/") == "Frankfurt"
    assert city_from_url("https://x.example/job/London-Loans-Trading-Executive-Director-EC2/1555555555/") == "London"
    assert city_from_url("https://x.example/search/") == ""


def test_sitemap_jobs_clean_title():
    from src.scrapers.sitemap_jobs import clean_title
    raw = ("IT-Support Spezialist*in (w/m/d) für den Bereich „IT-Service Desk“ - "
           "&lt;strong&gt;für den Bereich „IT-Service Desk“&lt;/strong&gt;&lt;br /&gt;")
    assert clean_title(raw) == "IT-Support Spezialist*in (w/m/d) für den Bereich „IT-Service Desk“"
    assert clean_title("Head of Finance - Frankfurt") == "Head of Finance - Frankfurt"
    assert clean_title("Teamleiter  Treasury (m/w/d)") == "Teamleiter Treasury (m/w/d)"


def test_bundesagentur_parse_and_pagination(bank):
    from src.scrapers.bundesagentur import BundesagenturScraper

    class Resp:
        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d

    class FakeHttp:
        calls = []

        def request(self, method, url, params=None, headers=None, **kw):
            FakeHttp.calls.append((params, headers))
            return Resp({"maxErgebnisse": 1, "stellenangebote": [
                {"refnr": "10001-1-S", "titel": "Abteilungsleiter Risikocontrolling (m/w/d)", "arbeitgeber": "Beispiel Bank AG",
                 "arbeitsort": {"ort": "Frankfurt am Main", "region": "Hessen"}}]})

    bank.options = {"branche": 6, "terms": ["Leiter", "Head"], "regions": [{"wo": "Köln", "umkreis": 30}]}
    jobs = BundesagenturScraper(FakeHttp()).fetch_jobs(bank)
    assert len(jobs) == 1                                    # same refnr from both terms is kept once
    assert jobs[0].url == "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1-S"
    assert jobs[0].bank_name == "Beispiel Bank AG (via Bundesagentur)" and jobs[0].location == "Frankfurt am Main, Hessen"
    params, headers = FakeHttp.calls[0]
    assert params["branche"] == 6 and params["wo"] == "Köln" and headers["X-API-Key"] == "jobboerse-jobsuche"


def test_cross_source_dedup_primary_wins(bank):
    from src.dedup import dedupe
    from tests.conftest import make_job

    own = make_job(bank, "Abteilungsleiter ICAAP & Adressausfallrisiken (m/w/d)", location="Hanau")
    own.bank_name = "Frankfurter Volksbank"
    dup = make_job(bank, "Abteilungsleiter (m/w/d) ICAAP & Adressausfallrisiken", location="Hanau, HESSEN")
    agg = make_job(bank, "Abteilungsleiter ICAAP & Adressausfallrisiken (w/m/d)", location="Hanau, HESSEN")
    agg.aggregator, agg.bank_name = True, "Frankfurter Volksbank Rhein/Main eG (via Bundesagentur)"
    other = make_job(bank, "Abteilungsleiter ICAAP & Adressausfallrisiken (m/w/d)", location="Hanau")
    other.aggregator, other.bank_name = True, "Andere Sparkasse (via Bundesagentur)"
    agency = make_job(bank, "Head of Risk", location="Frankfurt am Main")
    agency.aggregator = True
    agency2 = make_job(bank, "Head of Risk (m/w/d)", location="Frankfurt")
    agency2.aggregator = True
    out = dedupe([own, agg, other, agency, agency2])
    assert agg not in out and other in out                # same employer words -> dropped; different employer stays
    assert agency in out and agency2 not in out           # aggregators dedupe among themselves
    assert dup is not None


def test_dedup_unknown_city_and_agency_title_noise(bank):
    from src.dedup import dedupe
    from tests.conftest import make_job

    own = make_job(bank, "Abteilungsleiter (m/w/d) Informationssicherheit & Drittparteienrisikomanagement", location="")
    agency = make_job(bank, "Mid-Senior Abteilungsleitung (m/w/d) Informationssicherheit & Drittparteienrisikomanagement Permanent", location="")
    agency.aggregator = True
    ba = make_job(bank, "IT-Service Continuity Manager (m/w/d)", location="Düsseldorf, NORDRHEIN_WESTFALEN")
    ba.aggregator, ba.bank_name = True, "Deutsche WertpapierService Bank AG (via Bundesagentur)"
    own2 = make_job(bank, "IT-Service Continuity Manager (m/w/d)", location="")
    own2.bank_name = "Deutsche WertpapierService Bank AG"
    out = dedupe([own, agency, own2, ba])
    assert out == [own, own2]


def test_sitemap_industry_filter_keeps_only_banks(bank):
    from src.scrapers.sitemap_jobs import SitemapJobsScraper
    bank.options = {"industry_filter": {"selector": "span.meta-category", "text_regex": "Banks"}}
    nav = '<a href="/executive-search/banks-building-societies/">Banks and building societies</a>'    # menu on every page
    banks_page = nav + '<span class="meta-category">Region West, Banks and building societies</span>'
    other_page = nav + '<span class="meta-category">Region West, Industrial SMEs</span>'
    assert SitemapJobsScraper.industry_ok(banks_page, bank)
    assert not SitemapJobsScraper.industry_ok(other_page, bank)              # the menu link must not count
    assert not SitemapJobsScraper.industry_ok("<p>no industry</p>", bank)
    bank.options = {}
    assert SitemapJobsScraper.industry_ok(other_page, bank)                  # no filter configured: everything passes


def test_bundesagentur_employers_query(bank):
    from src.scrapers.bundesagentur import BundesagenturScraper

    class R:
        def json(self):
            return {"maxErgebnisse": 0, "stellenangebote": []}

    class H:
        calls = []

        def request(self, method, url, params=None, headers=None, **kw):
            H.calls.append(params)
            return R()

    bank.options = {"employers": ["Sparda-Bank Hessen eG"]}
    BundesagenturScraper(H()).fetch_jobs(bank)
    assert H.calls == [{"arbeitgeber": "Sparda-Bank Hessen eG", "size": 100, "page": 1}]


def test_custom_api_schema_org_datafeed(bank):
    """VR Payment publishes a schema.org DataFeed (items under dataFeedElement[].item)."""
    bank.options = {"api_url": "x", "items_path": "dataFeedElement",
                    "fields": {"title": "item.title", "url": "item.url", "id": "item.identifier.value", "date": "item.datePosted"}}
    data = {"dataFeedElement": [{"@type": "DataFeedItem", "item": {
        "title": "Stabsleiter Risikomanagement (w/m/d)", "url": "https://jobs.vr-payment.de/jobs/67671672/x/",
        "datePosted": "2026-09-15T15:32:29.389+02:00", "identifier": {"value": 67671672}}}]}
    jobs = CustomApiScraper(None).parse(data, bank)
    assert [j.title for j in jobs] == ["Stabsleiter Risikomanagement (w/m/d)"]
    assert jobs[0].published_date == "2026-09-15" and jobs[0].job_id.endswith("67671672")


def test_generic_html_title_from_url(bank):
    bank.jobs_url = "https://tsi.example/ueber-uns/karriere"
    bank.options = {"link_pattern": r"Stellenausschreibung[^\"]*\.pdf", "title_url_regex": r"Stellenausschreibung_([^/]+?)\.pdf"}
    html = '<a href="/fileadmin/Job_offer/2026-07-20_TSI_Stellenausschreibung_Associate_Director.pdf">Download Job offer (PDF)</a>'
    jobs = GenericHtmlScraper(None).parse(html, bank)
    assert [j.title for j in jobs] == ["Associate Director"]


def test_generic_html_title_cut_regex_sets_location(bank):
    bank.jobs_url = "https://rsgv.example/_/joblist"
    bank.options = {"link_pattern": r"/_/jobad\?prj=", "title_cut_regex": r"\s+(Düsseldorf|Köln)\s+(?:Vollzeit|Teilzeit).*$"}
    html = '<a href="/_/jobad?prj=1"><span>Stabsstellenleitung (w/m/d) Präsidialbüro</span> Düsseldorf Vollzeit oder Teilzeit Festanstellung</a>'
    jobs = GenericHtmlScraper(None).parse(html, bank)
    assert [(j.title, j.location) for j in jobs] == [("Stabsstellenleitung (w/m/d) Präsidialbüro", "Düsseldorf")]


def test_workday_id_ignores_a_location_in_bullet_fields(bank):
    from src.scrapers.workday import WorkdayScraper
    data = {"jobPostings": [
        {"title": "Risk Manager", "externalPath": "/job/Kronberg-Office/Risk-Manager_J66174", "bulletFields": ["Kronberg Office"]},
        {"title": "Head of X", "externalPath": "/job/Kronberg-Office/Head-of-X_J66175", "bulletFields": ["Kronberg Office"]},
        {"title": "Real id", "externalPath": "/job/a/Real-id_R-1", "bulletFields": ["R-19566"]}]}
    jobs = WorkdayScraper(None).parse(data, bank, "https://w.example", "site")
    assert [j.job_id.split(":")[1] for j in jobs] == ["J66174", "J66175", "R-19566"]


def test_generic_html_page_dedupes_repeated_vacancy_links(bank):
    from unittest.mock import MagicMock
    bank.jobs_url = "https://x.example/jobs"
    bank.options = {"link_pattern": r"/jobs/\d+"}
    html = '<a href="/jobs/1">Teamleiter Risiko</a><a href="/jobs/1">Teamleiter Risiko</a><a href="/jobs/2">Abteilungsleiter Recht</a>'
    http = MagicMock()
    http.get.return_value.text = html
    assert len(GenericHtmlScraper(http).fetch_jobs(bank)) == 2


def test_sitemap_jobs_slug_strip_regex_removes_id_and_gender_suffix(bank):
    from unittest.mock import MagicMock

    from src.scrapers.sitemap_jobs import SitemapJobsScraper
    bank.options = {"sitemap_url": "https://x.example/sitemap.xml", "url_regex": "/job/\\d+-", "detail": False,
                    "slug_strip_regex": r"^\d+-|-m-w-d$"}
    http = MagicMock()
    http.get.return_value.text = "<urlset><url><loc>https://x.example/job/37596-gruppenleitung-personal-m-w-d/</loc></url></urlset>"
    jobs = SitemapJobsScraper(http).fetch_jobs(bank)
    assert [j.title for j in jobs] == ["Gruppenleitung Personal"]
