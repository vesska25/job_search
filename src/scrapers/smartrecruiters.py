"""SmartRecruiters public Posting API: api.smartrecruiters.com/v1/companies/<id>/postings."""
from __future__ import annotations

from urllib.parse import urlsplit

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError, dig

API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"


class SmartRecruitersScraper(BaseScraper):
    source_type = "smartrecruiters"
    page_size = 100

    @staticmethod
    def company_id(bank: Bank) -> str:
        if bank.options.get("company"):
            return bank.options["company"]
        segs = [s for s in urlsplit(bank.jobs_url).path.split("/") if s]
        if not segs:
            raise ScraperError("Cannot derive SmartRecruiters company id; set options.company")
        return segs[0]

    def fetch_jobs(self, bank: Bank) -> list:
        company = self.company_id(bank)
        jobs, offset = [], 0
        params = {"limit": self.page_size}
        if bank.options.get("country", "de"):
            params["country"] = bank.options.get("country", "de")
        for _ in range(self.max_pages):
            params["offset"] = offset
            data = self.http.get(API.format(company=company), params=params).json()
            jobs.extend(self.parse(data, bank, company))
            offset += self.page_size
            if offset >= int(data.get("totalFound", 0)) or not data.get("content"):
                break
        return jobs

    def parse(self, data: dict, bank: Bank, company: str) -> list:
        if "content" not in data:
            raise ScraperError("SmartRecruiters response lacks 'content'")
        jobs = []
        for p in data["content"]:
            loc = p.get("location") or {}
            jid = str(p.get("id") or p.get("uuid") or "")
            if not jid or not p.get("name"):
                continue
            jobs.append(self.make_job(
                bank, title=p["name"], url=f"https://jobs.smartrecruiters.com/{company}/{jid}",
                location=", ".join(x for x in (loc.get("city"), loc.get("region")) if x),
                country=(loc.get("country") or "").upper(),
                department=dig(p, "department.label", ""), source_job_id=jid,
                published_date=(p.get("releasedDate") or "")[:10] or None,
            ))
        return jobs
