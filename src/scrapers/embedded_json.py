"""Career pages that ship the vacancy list as JSON inside an HTML attribute (React `data-react-props`, Vue `data-config`, ...).

One GET of the page, no JavaScript needed. Field mapping is the same as custom_api (items_path, fields, url_template).

banks.yaml options:
  selector: '[data-react-init="jobSearch"]'     (CSS selector of the element that carries the JSON)
  attr: data-react-props                        (attribute holding the JSON; default data-react-props)
  items_path: initialResults
  fields: {title: title, id: id, location: location, url: url, date: lastUpdate}

Alternative for JSON that sits in a <script> as plain text (Phenom: ..."eagerLoadRefineSearch":{"hits":7,"data":{"jobs":[...]}}):
  json_after: '"eagerLoadRefineSearch":'        (the JSON value is parsed right after the first usable occurrence of this text;
                                                  replaces selector/attr, items_path is relative to that value, e.g. data.jobs)
  page_param: from, page_size: 10, total_path: totalHits, max_pages: 10
                                                (json_after only: the page is fetched again with &from=<offset> until total_path
                                                 is reached; total_path is relative to the same JSON value)
"""
from __future__ import annotations

import json

from bs4 import BeautifulSoup

from src.config import Bank
from src.scrapers.base import ScraperError, dig
from src.scrapers.custom_api import CustomApiScraper


class EmbeddedJsonScraper(CustomApiScraper):
    source_type = "embedded_json"

    def fetch_jobs(self, bank: Bank) -> list:
        o = bank.options
        if not (o.get("selector") or o.get("json_after")) or o.get("items_path") is None:
            raise ScraperError("embedded_json requires options.items_path and options.selector (or options.json_after)")
        if not (o.get("json_after") and o.get("page_param")):
            return self.parse_page(self.http.get(bank.jobs_url).text, bank)
        size, offset, jobs, seen = int(o.get("page_size", 10)), 0, [], set()
        for _ in range(min(int(o.get("max_pages", 10)), self.max_pages or 10)):
            sep = "&" if "?" in bank.jobs_url else "?"
            data = self._json_after(self.http.get(f"{bank.jobs_url}{sep}{o['page_param']}={offset}").text, o["json_after"])
            batch = [j for j in self.parse(data, bank) if j.job_id not in seen]
            seen.update(j.job_id for j in batch)
            jobs.extend(batch)
            total = dig(data, o["total_path"], None) if o.get("total_path") else None
            offset += size
            if not batch or (total is not None and offset >= int(total)):
                break
        return jobs

    def parse_page(self, html: str, bank: Bank) -> list:
        o = bank.options
        if o.get("json_after"):
            return self.parse(self._json_after(html, o["json_after"]), bank)
        el = BeautifulSoup(html, "html.parser").select_one(o["selector"])
        raw = el.get(o.get("attr", "data-react-props")) if el else None
        if not raw:
            raise ScraperError(f"embedded_json: no '{o.get('attr', 'data-react-props')}' on {o['selector']!r} (layout change?)")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ScraperError(f"embedded_json: attribute is not valid JSON: {exc}") from exc
        return self.parse(data, bank)

    @staticmethod
    def _json_after(html: str, marker: str):
        decoder, pos = json.JSONDecoder(), html.find(marker)
        while pos != -1:
            start = pos + len(marker)
            while start < len(html) and html[start].isspace():
                start += 1
            try:
                return decoder.raw_decode(html, start)[0]
            except ValueError:
                pos = html.find(marker, pos + 1)
        raise ScraperError(f"embedded_json: no JSON after {marker!r} (layout change?)")
