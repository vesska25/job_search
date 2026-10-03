"""Workday: POST <host>/wday/cxs/<tenant>/<site>/jobs (the JSON endpoint behind the career site)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError


class WorkdayScraper(BaseScraper):
    source_type = "workday"
    page_size = 20

    @staticmethod
    def endpoints(bank: Bank):
        parts = urlsplit(bank.jobs_url)
        tenant = bank.options.get("tenant") or parts.netloc.split(".")[0]
        segs = [s for s in parts.path.split("/") if s and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", s)]
        site = bank.options.get("site") or (segs[0] if segs else "")
        if not site:
            raise ScraperError("Cannot derive Workday site; set options.site")
        base = f"{parts.scheme}://{parts.netloc}"
        return base, f"{base}/wday/cxs/{tenant}/{site}/jobs", site

    def fetch_jobs(self, bank: Bank) -> list:
        base, api, site = self.endpoints(bank)
        facets = bank.options.get("applied_facets", {})  # e.g. {"locationCountry": ["<id>"]}
        jobs, offset, total = [], 0, None
        for _ in range(self.max_pages):
            body = {"appliedFacets": facets, "limit": self.page_size, "offset": offset,
                    "searchText": bank.options.get("search_text", "")}
            data = self.http.post(api, json=body).json()
            jobs.extend(self.parse(data, bank, base, site))
            total = data.get("total", total or 0) or total or 0
            offset += self.page_size
            if offset >= (total or 0) or not data.get("jobPostings"):
                break
        return jobs

    def parse(self, data: dict, bank: Bank, base: str, site: str) -> list:
        if "jobPostings" not in data:
            raise ScraperError("Workday response lacks 'jobPostings'")
        jobs = []
        for p in data["jobPostings"]:
            path = p.get("externalPath") or ""
            if not path or not p.get("title"):
                continue
            ids = p.get("bulletFields") or []
            jobs.append(self.make_job(
                bank, title=p["title"], url=f"{base}/{site}{path}",
                location=p.get("locationsText", ""),
                source_job_id=str(ids[0]) if ids else path.rsplit("_", 1)[-1],
                published_date=None,
            ))
        return jobs
