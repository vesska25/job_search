"""Bundesagentur fuer Arbeit Jobsuche API (v6), filtered by industry (branche) and region.

banks.yaml options:
    options:
      branche: 6                         # numeric industry code (6 = banks, financial services, real estate, insurance)
      terms: [Leiter, Abteilungsleiter]  # `was` search words, one query each
      regions: [{wo: Frankfurt am Main, umkreis: 30}]
      size: 100                          # page size (API maximum 100)
      max_pages: 5                       # per term and region
      api_key: jobboerse-jobsuche        # public key published in the API documentation
The employer name from the posting is shown as the job's bank name (not as department, so employer words such as
'Personalmanagement' cannot match a function); results are de-duplicated by `refnr`.
"""
from __future__ import annotations

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError, dig

API_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
JOB_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"


class BundesagenturScraper(BaseScraper):
    source_type = "bundesagentur"

    def fetch_jobs(self, bank: Bank) -> list:
        o = bank.options
        terms, regions = o.get("terms") or [], o.get("regions") or []
        if not terms or not regions:
            raise ScraperError("bundesagentur requires options.terms and options.regions")
        size = min(int(o.get("size", 100)), 100)
        headers = {"X-API-Key": o.get("api_key", "jobboerse-jobsuche"), "Accept": "application/json"}
        jobs, seen = [], set()
        for region in regions:
            for term in terms:
                for page in range(1, min(int(o.get("max_pages", 5)), self.max_pages) + 1):
                    params = {"was": term, "wo": region["wo"], "umkreis": region.get("umkreis", 30),
                              "size": size, "page": page}
                    if o.get("branche") is not None:
                        params["branche"] = o["branche"]
                    data = self.http.request("GET", o.get("api_url", API_URL), params=params, headers=headers).json()
                    batch = self.parse(data, bank)
                    new = [j for j in batch if j.job_id not in seen]
                    seen.update(j.job_id for j in new)
                    jobs.extend(new)
                    total = int(data.get("maxErgebnisse") or 0)
                    if not batch or page * size >= total:
                        break
        return jobs

    def parse(self, data, bank: Bank) -> list:
        items = data.get("stellenangebote", data.get("ergebnisliste", [])) if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ScraperError("bundesagentur: unexpected response (no 'stellenangebote' list)")
        jobs = []
        for it in items:
            ref = it.get("refnr") or it.get("referenznummer")
            title = it.get("titel") or it.get("stellenangebotsTitel") or it.get("beruf")
            if not ref or not title:
                continue
            addr = dig(it, "arbeitsort", None) or dig(it, "stellenlokationen.0.adresse", {}) or {}
            loc = ", ".join(str(addr[k]) for k in ("ort", "region") if addr.get(k))
            employer = it.get("arbeitgeber") or it.get("firma") or ""
            job = self.make_job(
                bank, title=str(title), url=JOB_URL.format(refnr=ref), location=loc, source_job_id=str(ref),
                published_date=(str(it.get("aktuelleVeroeffentlichungsdatum") or "")[:10] or None))
            if employer:
                job.bank_name = f"{employer} (via Bundesagentur)"
            jobs.append(job)
        return jobs
