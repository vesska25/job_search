"""Generic HTML adapter. Strategy order (first that yields jobs wins):
  1. schema.org JobPosting JSON-LD embedded in the page (most reliable)
  2. CSS selectors from banks.yaml options (item, title, link, location)
  3. link heuristic: anchors whose href matches options.link_pattern

Server-rendered HTML only. JavaScript-rendered career pages need a JSON endpoint (custom_api).
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError

DEFAULT_LINK_PATTERN = r"/(job|jobs|stelle|stellen|stellenangebot|stellenangebote|vacanc\w*|position|positions|karriere/jobs)/[^/?#]+"


class GenericHtmlScraper(BaseScraper):
    source_type = "custom_html"

    def fetch_jobs(self, bank: Bank) -> list:
        pg = bank.options.get("pagination") or {}
        jobs, seen = [], set()
        for page in range(1, (pg.get("max_pages", 1) if pg else 1) + 1):
            params = {pg["param"]: page} if pg and page > 1 else {}
            html = self.http.get(bank.jobs_url, params=params).text
            found = [j for j in self.parse(html, bank) if j.job_id not in seen]
            if not found:
                break
            seen.update(j.job_id for j in found)
            jobs.extend(found)
        return jobs

    def parse(self, html: str, bank: Bank) -> list:
        soup = BeautifulSoup(html, "html.parser")
        jobs = self._from_jsonld(soup, bank) or self._from_selectors(soup, bank) or self._from_links(soup, bank)
        if not jobs and len(html) < 500:
            raise ScraperError("Page nearly empty (blocked or JavaScript-rendered?)")
        return jobs

    # -- strategies ---------------------------------------------------------
    def _from_jsonld(self, soup, bank):
        jobs = []
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except (ValueError, TypeError):
                continue
            for node in self._walk(data):
                if node.get("@type") != "JobPosting" or not node.get("title"):
                    continue
                loc = node.get("jobLocation")
                loc = loc[0] if isinstance(loc, list) and loc else loc
                addr = (loc or {}).get("address", {}) if isinstance(loc, dict) else {}
                country = addr.get("addressCountry", "")
                country = country.get("name", "") if isinstance(country, dict) else country
                url = node.get("url") or (node.get("identifier") or {}).get("value", "")
                if not url:
                    continue
                ident = node.get("identifier")
                jid = str(ident.get("value")) if isinstance(ident, dict) and ident.get("value") else None
                jobs.append(self.make_job(
                    bank, title=node["title"].strip(), url=urljoin(bank.jobs_url, url),
                    location=addr.get("addressLocality", ""), country=str(country or ""),
                    department=str(node.get("occupationalCategory", "") or ""),
                    description=re.sub(r"<[^>]+>", " ", node.get("description", "") or "")[:5000],
                    source_job_id=jid, published_date=(node.get("datePosted") or "")[:10] or None,
                ))
        return jobs

    @classmethod
    def _walk(cls, data):
        if isinstance(data, dict):
            yield data
            for v in data.values():
                yield from cls._walk(v)
        elif isinstance(data, list):
            for v in data:
                yield from cls._walk(v)

    def _from_selectors(self, soup, bank):
        sel = bank.options.get("selectors")
        if not sel or not sel.get("item"):
            return []
        jobs = []
        for item in soup.select(sel["item"]):
            link = item.select_one(sel.get("link", "a")) or (item if item.name == "a" else None)
            title_el = item.select_one(sel["title"]) if sel.get("title") else link
            if not link or not link.get("href") or not title_el:
                continue
            loc_el = item.select_one(sel["location"]) if sel.get("location") else None
            jobs.append(self.make_job(
                bank, title=title_el.get_text(" ", strip=True), url=urljoin(bank.jobs_url, link["href"]),
                location=loc_el.get_text(" ", strip=True) if loc_el else "",
            ))
        return jobs

    def _from_links(self, soup, bank):
        pattern = re.compile(bank.options.get("link_pattern") or DEFAULT_LINK_PATTERN, re.I)
        jobs, seen = [], set()
        for a in soup.find_all("a", href=True):
            title = a.get_text(" ", strip=True)
            href = urljoin(bank.jobs_url, a["href"])
            if len(title) < 8 or not pattern.search(href) or href in seen:
                continue
            seen.add(href)
            jobs.append(self.make_job(bank, title=title, url=href))
        return jobs
