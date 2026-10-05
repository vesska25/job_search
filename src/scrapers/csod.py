"""Cornerstone OnDemand career sites (<corp>.csod.com/ux/ats/careersite/<id>/home?c=<corp>).

The page is a JavaScript shell. It embeds the anonymous guest session of the site (csod.context: the API base `endpoints.cloud`
and a guest `token`, user -1789) and the front end then calls the job search of that API. This adapter does the same,
reading the token from the page at run time (nothing is stored in the repository) and paging through the result.

Flow: GET <jobs_url> -> csod.context token + cloud endpoint -> POST <cloud>rec-job-search/external/jobs (Bearer token).
robots.txt is respected for both hosts like everywhere else; if it stops the API host, the source is reported as failed.

banks.yaml options:
  career_site_id: 1        (the number in /careersite/<id>/; default 1)
  culture_id: 1, culture_name: en-US
  page_size: 25
  country_codes: [DE]      (optional filter sent to the search)
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, parse_qs

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError, dig

TOKEN = re.compile(r'"token"\s*:\s*"([^"]+)"')
CLOUD = re.compile(r'"cloud"\s*:\s*"(https?://[^"]+)"')


class CsodScraper(BaseScraper):
    source_type = "csod"

    def session_from_page(self, html: str):
        t, c = TOKEN.search(html), CLOUD.search(html)
        if not t or not c:
            raise ScraperError("csod page lacks csod.context token/cloud endpoint (layout change?)")
        return t.group(1), c.group(1).rstrip("/") + "/"

    def fetch_jobs(self, bank: Bank) -> list:
        o = bank.options
        token, cloud = self.session_from_page(self.http.get(bank.jobs_url).text)
        corp = (parse_qs(urlsplit(bank.jobs_url).query).get("c") or [urlsplit(bank.jobs_url).netloc.split(".")[0]])[0]
        size = int(o.get("page_size", 25))
        jobs, seen = [], set()
        for page in range(1, self.max_pages + 1):
            body = {"careerSiteId": int(o.get("career_site_id", 1)), "careerSitePageId": int(o.get("career_site_id", 1)),
                    "pageNumber": page, "pageSize": size, "cultureId": int(o.get("culture_id", 1)),
                    "searchText": "", "cultureName": o.get("culture_name", "en-US"), "states": [],
                    "countryCodes": list(o.get("country_codes") or []), "cities": [], "placeID": "", "radius": None,
                    "postingsWithinDays": None, "customFieldCheckboxKeys": [], "customFieldDropdowns": [], "customFieldRadios": []}
            data = self.http.post(cloud + "rec-job-search/external/jobs", json=body,
                                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}).json()
            batch, total = self.parse(data, bank, corp)
            new = [j for j in batch if j.job_id not in seen]
            seen.update(j.job_id for j in new)
            jobs.extend(new)
            if not batch or not new or (total and len(jobs) >= total):
                break
        return jobs

    def parse(self, data: dict, bank: Bank, corp: str):
        block = dig(data, "data") or data
        items = block.get("requisitions") if isinstance(block, dict) else None
        if not isinstance(items, list):
            raise ScraperError("csod response lacks data.requisitions (format change?)")
        cid = int(bank.options.get("career_site_id", 1))
        jobs = []
        for it in items:
            rid = it.get("requisitionId") or it.get("id")
            title = it.get("displayJobTitle") or it.get("title")
            if rid is None or not title:
                continue
            locs = it.get("locations") or []
            places = [", ".join(x for x in (l.get("city"), l.get("state"), l.get("country")) if x)
                      for l in locs if isinstance(l, dict)]
            jobs.append(self.make_job(
                bank, title=str(title).strip(), source_job_id=str(rid),
                url=f"https://{corp}.csod.com/ux/ats/careersite/{cid}/home/requisition/{rid}?c={corp}",
                location="; ".join(p for p in places if p),
                published_date=(str(it.get("postingEffectiveDate") or "")[:10] or None)))
        return jobs, int(block.get("totalCount") or block.get("total") or len(jobs))
