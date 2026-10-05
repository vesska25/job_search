"""Career pages that ship the vacancy list as JSON inside an HTML attribute (React `data-react-props`, Vue `data-config`, ...).

One GET of the page, no JavaScript needed. Field mapping is the same as custom_api (items_path, fields, url_template).

banks.yaml options:
  selector: '[data-react-init="jobSearch"]'     (CSS selector of the element that carries the JSON)
  attr: data-react-props                        (attribute holding the JSON; default data-react-props)
  items_path: initialResults
  fields: {title: title, id: id, location: location, url: url, date: lastUpdate}
"""
from __future__ import annotations

import json

from bs4 import BeautifulSoup

from src.config import Bank
from src.scrapers.base import ScraperError
from src.scrapers.custom_api import CustomApiScraper


class EmbeddedJsonScraper(CustomApiScraper):
    source_type = "embedded_json"

    def fetch_jobs(self, bank: Bank) -> list:
        o = bank.options
        if not o.get("selector") or o.get("items_path") is None:
            raise ScraperError("embedded_json requires options.selector and options.items_path")
        return self.parse_page(self.http.get(bank.jobs_url).text, bank)

    def parse_page(self, html: str, bank: Bank) -> list:
        o = bank.options
        el = BeautifulSoup(html, "html.parser").select_one(o["selector"])
        raw = el.get(o.get("attr", "data-react-props")) if el else None
        if not raw:
            raise ScraperError(f"embedded_json: no '{o.get('attr', 'data-react-props')}' on {o['selector']!r} (layout change?)")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ScraperError(f"embedded_json: attribute is not valid JSON: {exc}") from exc
        return self.parse(data, bank)
