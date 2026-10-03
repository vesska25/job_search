"""Generic adapter for career sites that publish every vacancy as its own page and list them in a sitemap
(WordPress `job_offer` post types, Drupal offer pages, ...), where the vacancy list itself is loaded by JavaScript.

Flow: GET <sitemap_url> -> vacancy URLs (filtered) -> GET each page -> title/location from schema.org JobPosting
JSON-LD, else from the first <h1> (or og:title / <title>). Respects robots.txt like every other adapter.

banks.yaml options:
  sitemap_url: https://jobs.example.bank/job_offer-sitemap.xml   (required)
  url_regex: "/stellenangebote/[^/]+/?$"                          (keep only matching URLs; default: all)
  skip_slug: "^(ausbildung|praktikum|student|werkstudent)"        (regex on the last path segment: pages never worth a request)
  detail: true                                                    (false: build the title from the URL slug, no page requests)
  location_regex: "\\bin ([A-ZÄÖÜ][^()]*)$"                       (applied to the title when the page has no location)
  max_pages: 250                                                  (safety cap on page requests, via the usual max_pages option)
"""
from __future__ import annotations

import html as htmllib
import re
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError
from src.scrapers.generic_html import GenericHtmlScraper

LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def slug_of(url: str) -> str:
    """Stable per-vacancy key: the last path segment, or the query string when the id lives there (index.php?ac=jobad&id=1)."""
    parts = urlsplit(url)
    if parts.query:
        return parts.query
    segs = [p for p in parts.path.split("/") if p]
    return segs[-1] if segs else ""


def title_from_slug(slug: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[-_=&]", " ", slug)).strip().title()


class SitemapJobsScraper(BaseScraper):
    source_type = "sitemap_jobs"

    def urls(self, bank: Bank) -> list:
        o = bank.options
        if not o.get("sitemap_url"):
            raise ScraperError("sitemap_jobs requires options.sitemap_url")
        text = self.http.get(o["sitemap_url"]).text
        found = [htmllib.unescape(u) for u in LOC.findall(text)]
        if not found:
            raise ScraperError("sitemap lists no URLs (layout change or blocked?)")
        keep = re.compile(o["url_regex"]) if o.get("url_regex") else None
        skip = re.compile(o["skip_slug"], re.I) if o.get("skip_slug") else None
        out = []
        for u in dict.fromkeys(found):
            if keep and not keep.search(u):
                continue
            if skip and skip.search(slug_of(u)):
                continue
            out.append(u)
        return out

    def fetch_jobs(self, bank: Bank) -> list:
        urls = self.urls(bank)[: self.max_pages if self.max_pages else None]
        if not bank.options.get("detail", True):
            return [self.make_job(bank, title=title_from_slug(slug_of(u)), url=u, source_job_id=slug_of(u)) for u in urls]
        parser = GenericHtmlScraper(self.http)
        jobs = []
        for u in urls:
            try:
                html = self.http.get(u).text
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in (404, 410):
                    continue  # sitemap may still list expired vacancies
                raise
            job = self.parse_page(html, u, bank, parser)
            if job:
                jobs.append(job)
        return jobs

    def parse_page(self, html: str, url: str, bank: Bank, parser=None):
        soup = BeautifulSoup(html, "html.parser")
        parser = parser or GenericHtmlScraper(self.http)
        ld = parser._from_jsonld(soup, bank)
        if ld:
            job = ld[0]
            job.url, job.source_job_id, job.source_type = url, slug_of(url), self.source_type
            return job
        h1 = soup.find("h1")
        og = soup.find("meta", attrs={"property": "og:title"})
        title = (h1.get_text(" ", strip=True) if h1 else "") or (og.get("content", "").strip() if og else "") \
            or (soup.title.get_text(" ", strip=True) if soup.title else "")
        if not title:
            return None
        location = ""
        rx = bank.options.get("location_regex")
        if rx:
            m = re.search(rx, title)
            location = m.group(1).strip() if m else ""
        return self.make_job(bank, title=title, url=url, location=location, source_job_id=slug_of(url))
