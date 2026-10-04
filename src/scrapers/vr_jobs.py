"""Job exchange of the Volksbanken Raiffeisenbanken (www.vr.de/karriere/jobs).

The exchange publishes every vacancy as its own page with schema.org JobPosting JSON-LD and lists all of them in
https://www.vr.de/jobs.sitemap.xml. The bank's name is part of every vacancy URL:
    /karriere/jobs/<title-slug>-<bank-slug>-<6 character id>.html
robots.txt of vr.de only disallows '/*suchergebnisse*' and '/content/', so sitemap and vacancy pages are fair game.

banks.yaml options:
  vr_slug: volksbank-darmstadt-mainz-eg   (bank part of the URL; default: derived from the bank name)
  allow_empty: true                        (a bank without current vacancies is not a failure)

A bank that does not appear in the sitemap at all (not even with an 'initiativbewerbung-<bank>' page) raises a
ScraperError, so a wrong slug is reported instead of silently returning nothing.
"""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError
from src.scrapers.generic_html import GenericHtmlScraper

SITEMAP_URL = "https://www.vr.de/jobs.sitemap.xml"
LOC = re.compile(r"<loc>\s*(https://www\.vr\.de/karriere/jobs/([^<\s]+?)-([A-Za-z0-9]{6})\.html)\s*</loc>")
# Pages that are never a leadership vacancy: not worth one request each.
SKIP_PREFIX = re.compile(r"^(initiativbewerbung|talent-?pool|schuelerpraktikum|praktikum|ausbildung|auszubildende|"
                         r"duales-studium|dualer-student|werkstudent|abiturient|trainee)", re.I)
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def url_slug(name: str) -> str:
    s = name.lower().translate(_UMLAUTS)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


class VrJobsScraper(BaseScraper):
    source_type = "vr_jobs"

    def sitemap_entries(self) -> list:
        # One sitemap download per run, shared by all VR banks: the cache lives on the (per-run) HTTP client, not on the
        # class, so a fresh client (contract test, capture) never inherits another bank's download.
        cache = self.http.__dict__.setdefault("_vr_sitemap_cache", {})
        if SITEMAP_URL not in cache:
            text = self.http.get(SITEMAP_URL).text
            entries = LOC.findall(text)
            if not entries:
                raise ScraperError("vr.de jobs sitemap lists no vacancy pages (layout change?)")
            cache[SITEMAP_URL] = entries
        return cache[SITEMAP_URL]

    def bank_entries(self, bank: Bank, entries: list) -> list:
        frag = bank.options.get("vr_slug") or url_slug(bank.name)
        return [e for e in entries if e[1].endswith("-" + frag) or e[1] == frag]

    def fetch_jobs(self, bank: Bank) -> list:
        mine = self.bank_entries(bank, self.sitemap_entries())
        if not mine:
            raise ScraperError(f"bank not found in the vr.de exchange (vr_slug={bank.options.get('vr_slug') or url_slug(bank.name)!r})")
        parser = GenericHtmlScraper(self.http)
        jobs = []
        for url, slug, jid in mine:
            if SKIP_PREFIX.match(slug):
                continue
            try:
                html = self.http.get(url).text
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in (404, 410):
                    continue  # the sitemap still lists expired vacancies
                raise
            soup = BeautifulSoup(html, "html.parser")
            for job in parser._from_jsonld(soup, bank):   # JSON-LD only: link heuristics would pick up related-job teasers
                if job.url.rstrip("/") != url:
                    continue
                job.source_job_id, job.source_type = jid, self.source_type
                jobs.append(job)
        return jobs
