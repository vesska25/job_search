"""Sparkassen job exchange (sparkasse.de/jobboerse): the JSON API the exchange pages call themselves.

Request:  GET https://www.sparkasse.de/api/job-market/jobs/search?bankCode=<BLZ>&limit=<n>&offset=<n>
Response: jobs.items[] (id, title, slug?, client{id,name,slug,bankCode}, addresses[], address{}), total in jobs.count

banks.yaml options:
  bank_code: "50050201"     (required; the bank's BLZ, found as client.bankCode on its sparkasse.de/jobboerse page)
  api_url: https://www.sparkasse.de/api/job-market/jobs/search   (default)
  page_size: 50
"""
from __future__ import annotations

import re

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError

API_URL = "https://www.sparkasse.de/api/job-market/jobs/search"
DETAIL_URL = "https://www.sparkasse.de/jobboerse/jobangebot/{slug}-{id}.html"
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def slugify(text: str) -> str:
    s = text.lower().translate(_UMLAUTS)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:90] or "stelle"


class SparkasseJobMarketScraper(BaseScraper):
    source_type = "sparkasse_jobmarket"

    def fetch_jobs(self, bank: Bank) -> list:
        o = bank.options
        if not o.get("bank_code"):
            raise ScraperError("sparkasse_jobmarket requires options.bank_code")
        size = o.get("page_size", 50)
        jobs, seen = [], set()
        offset = 0
        for _ in range(self.max_pages):
            resp = self.http.get(o.get("api_url", API_URL),
                                 params={"bankCode": str(o["bank_code"]), "limit": size, "offset": offset})
            batch, total = self.parse(resp.json(), bank)
            new = [j for j in batch if j.job_id not in seen]
            seen.update(j.job_id for j in new)
            jobs.extend(new)
            offset += size
            if not batch or not new or len(jobs) >= total:
                break
        return jobs

    def parse(self, data: dict, bank: Bank):
        block = (data or {}).get("jobs")
        if not isinstance(block, dict) or "items" not in block:
            raise ScraperError("Sparkassen job-market response lacks jobs.items")
        wanted = str(bank.options.get("bank_code", ""))
        jobs = []
        for item in block["items"]:
            title, jid = item.get("title"), item.get("id")
            if not title or jid is None:
                continue
            client = item.get("client") or {}
            if wanted and client.get("bankCode") and str(client["bankCode"]) != wanted:
                continue  # the API ignored the filter for this item; never report another bank's jobs
            addr = (item.get("addresses") or [{}])[0] or item.get("address") or client.get("address") or {}
            cities = list(dict.fromkeys(a.get("city", "") for a in (item.get("addresses") or []) if a.get("city")))
            location = ", ".join(cities) or addr.get("city", "")
            category = ", ".join(c.get("title", "") for c in item.get("occupationalCategories") or [])
            slug = item.get("slug") or slugify(str(title))
            jobs.append(self.make_job(
                bank, title=str(title).strip(), url=DETAIL_URL.format(slug=slug, id=jid),
                location=location, country="", department=category,
                source_job_id=str(jid),
                published_date=(str(item.get("publishedAt") or item.get("createdAt") or "")[:10] or None),
            ))
        total = int(block.get("count") or len(jobs))
        return jobs, total
