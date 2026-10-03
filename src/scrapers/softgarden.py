"""softgarden career pages: https://<company>.softgarden.io/ with links /job/<id>/<slug>."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError

JOB_HREF = re.compile(r"/job/(\d+)")


class SoftgardenScraper(BaseScraper):
    source_type = "softgarden"

    def fetch_jobs(self, bank: Bank) -> list:
        parts = urlsplit(bank.jobs_url)
        base = f"{parts.scheme}://{parts.netloc}"
        jobs, seen = [], set()
        for page in range(1, self.max_pages + 1):
            params = {"page": page} if page > 1 else {}
            html = self.http.get(bank.jobs_url, params=params).text
            found = [j for j in self.parse(html, bank, base) if j.source_job_id not in seen]
            if not found:
                break
            seen.update(j.source_job_id for j in found)
            jobs.extend(found)
        return jobs

    def parse(self, html: str, bank: Bank, base: str) -> list:
        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        for a in soup.find_all("a", href=JOB_HREF):
            jid = JOB_HREF.search(a["href"]).group(1)
            container = a.find_parent(class_=re.compile(r"(job|result|match)", re.I)) or a.parent
            title_el = container.select_one(".matchValue.title, .title, h2, h3") or a
            loc_el = container.select_one(".matchValue.location, .location")
            title = title_el.get_text(" ", strip=True)
            if not title:
                continue
            jobs.append(self.make_job(
                bank, title=title, url=urljoin(base, a["href"]), source_job_id=jid,
                location=loc_el.get_text(" ", strip=True) if loc_el else "",
            ))
        if not jobs and "softgarden" not in html.lower() and "<a" not in html.lower():
            raise ScraperError("softgarden: page has no recognizable content")
        return jobs
