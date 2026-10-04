"""SAP SuccessFactors classic career site: /search/?q=&startrow=N (HTML table of jobs)."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError


def city_from_url(href: str) -> str:
    """Jobs2web vacancy URLs are /job/<City>-<Title>-<postal code>/<id>: the first word of the slug is the city.
    Used only when the listing itself shows no location (e.g. SMBC, Mizuho)."""
    m = re.search(r"/job/([^/]+)/\d{5,}", href)
    return m.group(1).split("-")[0] if m else ""


class SuccessFactorsScraper(BaseScraper):
    source_type = "successfactors"
    page_size = 25

    def fetch_jobs(self, bank: Bank) -> list:
        parts = urlsplit(bank.jobs_url)
        base = f"{parts.scheme}://{parts.netloc}"
        search = bank.options.get("search_url") or urljoin(base, "/search/")
        extra = bank.options.get("params", {})  # e.g. {"locationsearch": "Germany"}
        jobs, start, seen = [], 0, set()
        for _ in range(self.max_pages):
            html = self.http.get(search, params={"q": "", "startrow": start, **extra}).text
            try:
                page = self.parse(html, bank, base)
            except ScraperError:
                if jobs:   # an unrecognisable page after the first one is just the end of the list
                    break
                raise
            new = [j for j in page if j.url not in seen]
            if not new:
                break
            seen.update(j.url for j in new)
            jobs.extend(new)
            start += self.page_size
        return jobs

    @staticmethod
    def _container(link):
        """Largest ancestor that still contains only this one job link (row, tile or card)."""
        node = link
        while node.parent is not None and node.parent.name not in ("body", "html", "[document]"):
            if len(node.parent.select("a.jobTitle-link")) > 1:
                break
            node = node.parent
        return node

    def parse(self, html: str, bank: Bank, base: str) -> list:
        soup = BeautifulSoup(html, "html.parser")
        links = soup.select("a.jobTitle-link")
        if not links:
            text = soup.get_text().lower()
            if "no results" in text or "keine ergebnisse" in text or "keine stellen" in text:
                return []
            raise ScraperError("SuccessFactors: no job links found (layout changed?)")
        jobs, seen = [], set()
        for a in links:
            if not a.get("href"):
                continue
            href = urljoin(base, a["href"])
            if href in seen:   # tile layouts repeat each job for desktop/tablet/mobile
                continue
            seen.add(href)
            row = self._container(a)
            m = re.search(r"/(\d{5,})/?(?:\?|$)", href)
            loc = row.select_one(".jobLocation, .section-field.location, [class*=location]")
            dept = row.select_one(".jobDepartment, .jobFacility")
            date = row.select_one(".jobDate")
            jobs.append(self.make_job(
                bank, title=a.get_text(" ", strip=True), url=href,
                location=(loc.get_text(" ", strip=True) if loc else "") or city_from_url(href),
                department=dept.get_text(" ", strip=True) if dept else "",
                source_job_id=m.group(1) if m else None,
                published_date=date.get_text(strip=True) if date else None,
            ))
        return jobs
