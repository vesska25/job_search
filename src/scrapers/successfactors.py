"""SAP SuccessFactors classic career site: /search/?q=&startrow=N (HTML table of jobs)."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError


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
            page = self.parse(html, bank, base)
            new = [j for j in page if j.url not in seen]
            if not new:
                break
            seen.update(j.url for j in new)
            jobs.extend(new)
            start += self.page_size
        return jobs

    def parse(self, html: str, bank: Bank, base: str) -> list:
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("tr.data-row")
        if not rows:
            if soup.select_one("a.jobTitle-link"):
                rows = [a.find_parent("tr") or a.parent for a in soup.select("a.jobTitle-link")]
            elif "no results" in soup.get_text().lower() or "keine ergebnisse" in soup.get_text().lower():
                return []
            else:
                raise ScraperError("SuccessFactors: no job rows found (layout changed?)")
        jobs = []
        for row in rows:
            a = row.select_one("a.jobTitle-link")
            if not a or not a.get("href"):
                continue
            href = urljoin(base, a["href"])
            m = re.search(r"/(\d{5,})/?(?:\?|$)", href)
            loc = row.select_one(".jobLocation")
            dept = row.select_one(".jobDepartment, .jobFacility")
            date = row.select_one(".jobDate")
            jobs.append(self.make_job(
                bank, title=a.get_text(strip=True), url=href,
                location=loc.get_text(" ", strip=True) if loc else "",
                department=dept.get_text(" ", strip=True) if dept else "",
                source_job_id=m.group(1) if m else None,
                published_date=date.get_text(strip=True) if date else None,
            ))
        return jobs
