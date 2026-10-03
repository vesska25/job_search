"""BeeSite search API (used by e.g. Commerzbank and Deutsche Bank career sites).

Request:  GET <api_url>/?data=<JSON>   (same call the banks' own career pages make from the browser)
Response: SearchResult.SearchResultItems[].MatchedObjectDescriptor, total in SearchResult.SearchResultCountAll

banks.yaml options:
  api_url: https://api-jobs.commerzbank.com/search      (required)
  language: DE                                           (LanguageCode, default DE)
  page_size: 50                                          (CountItem per request)
  first_item: 1                                          (index of first item; BeeSite is 1-based)
  score_threshold: 100                                   (optional, passed through)
  criteria: [{CriterionName: ..., CriterionValue: [...]}] (optional server-side filters, e.g. a country id)
  fields: [...]                                          (MatchedObjectDescriptor fields to request)
"""
from __future__ import annotations

import json
from urllib.parse import urljoin

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError

DEFAULT_FIELDS = ["PositionID", "PositionTitle", "PositionURI", "PositionLocation.CityName",
                  "JobCategory.Name", "PositionSchedule.Name", "ParentOrganizationName", "CareerLevel.Name"]


def _first_name(value, key="Name") -> str:
    if isinstance(value, list) and value:
        value = value[0]
    return str(value.get(key, "")) if isinstance(value, dict) else (str(value) if value else "")


class BeeSiteScraper(BaseScraper):
    source_type = "custom_api"

    def build_body(self, bank: Bank, first_item: int) -> dict:
        o = bank.options
        body = {
            "LanguageCode": o.get("language", "DE"),
            "SearchParameters": {
                "FirstItem": first_item,
                "CountItem": o.get("page_size", 50),
                "MatchedObjectDescriptor": o.get("fields", DEFAULT_FIELDS),
                "Sort": [{"Criterion": "PublicationStartDate", "Direction": "DESC"}],
            },
            "SearchCriteria": o.get("criteria", []),
        }
        if o.get("score_threshold") is not None:
            body["ScoreThreshold"] = o["score_threshold"]
        return body

    def fetch_jobs(self, bank: Bank) -> list:
        o = bank.options
        if not o.get("api_url"):
            raise ScraperError("beesite requires options.api_url")
        size = o.get("page_size", 50)
        pos = o.get("first_item", 1)
        jobs, seen = [], set()
        for _ in range(self.max_pages):
            body = self.build_body(bank, pos)
            resp = self.http.get(o["api_url"].rstrip("/") + "/",
                                 params={"data": json.dumps(body, separators=(",", ":"))})
            batch, total = self.parse(resp.json(), bank)
            new = [j for j in batch if j.job_id not in seen]
            seen.update(j.job_id for j in new)
            jobs.extend(new)
            pos += size
            if not batch or len(jobs) >= total:
                break
        return jobs

    def parse(self, data: dict, bank: Bank):
        result = (data or {}).get("SearchResult")
        if not isinstance(result, dict) or "SearchResultItems" not in result:
            raise ScraperError("BeeSite response lacks SearchResult.SearchResultItems")
        jobs = []
        for item in result["SearchResultItems"]:
            d = item.get("MatchedObjectDescriptor") or {}
            title, uri = d.get("PositionTitle"), d.get("PositionURI")
            if not title or not uri:
                continue
            locs = d.get("PositionLocation") or []
            cities = list(dict.fromkeys(l.get("CityName", "") for l in locs if isinstance(l, dict) and l.get("CityName")))
            country = _first_name(locs, "CountryCode") or _first_name(locs, "Country") or _first_name(locs, "CountryName")
            jobs.append(self.make_job(
                bank, title=str(title).strip(), url=urljoin(bank.jobs_url or "", str(uri)),
                location=", ".join(cities), country=country,
                department=_first_name(d.get("JobCategory")) or str(d.get("ParentOrganizationName") or ""),
                source_job_id=str(d["PositionID"]) if d.get("PositionID") else None,
                published_date=(str(d.get("PublicationStartDate") or "")[:10] or None),
            ))
        total = int(result.get("SearchResultCountAll") or result.get("SearchResultCount") or len(jobs))
        return jobs, total
