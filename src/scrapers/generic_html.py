"""Generic HTML adapter. Strategy order (first that yields jobs wins):
  1. schema.org JobPosting JSON-LD embedded in the page (most reliable)
  2. CSS selectors from banks.yaml options (item, title, link, location)
  3. link heuristic: anchors whose href matches options.link_pattern

Server-rendered HTML only. JavaScript-rendered career pages need a JSON endpoint (custom_api).
"""
from __future__ import annotations

import json
import re
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError
from src.utils.normalization import slugify

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
        rx = bank.options.get("location_regex")  # e.g. "\\bin ([A-ZÄÖÜ][^()]*)$" for titles like "Leiter X (m/w/d) in Wiesbaden"
        if rx:
            pattern = re.compile(rx)
            for j in jobs:
                if not j.location:
                    m = pattern.search(j.title)
                    j.location = m.group(1).strip() if m else ""
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
        """Items matched by options.selectors. Without a link element (accordion lists such as
        <details><summary>Title</summary>...</details>) set selectors.no_link: true; every job then
        gets a unique URL <jobs_url>?job=<slug-of-title> and the item's text becomes its description."""
        sel = bank.options.get("selectors")
        if not sel or not sel.get("item"):
            return []
        jobs, seen_urls = [], set()
        for item in soup.select(sel["item"]):
            title_el = item.select_one(sel["title"]) if sel.get("title") else None
            loc_el = item.select_one(sel["location"]) if sel.get("location") else None
            location = loc_el.get_text(" ", strip=True) if loc_el else ""
            if sel.get("no_link"):
                title = (title_el or item).get_text(" ", strip=True)
                if not title:
                    continue
                slug = slugify(title)[:80]
                sep = "&" if "?" in bank.jobs_url else "?"
                text = item.get_text(" ", strip=True)
                jobs.append(self.make_job(
                    bank, title=title, url=f"{bank.jobs_url}{sep}job={slug}", location=location,
                    description=text[:3000] if text != title else "", source_job_id=slug))
                continue
            link = item.select_one(sel.get("link", "a")) or (item if item.name == "a" else None)
            title_el = title_el or link
            if not link or not link.get("href") or not title_el:
                continue
            title = title_el.get_text(" ", strip=True)
            if not title:  # icon / arrow links: fall back to the title attribute, then to the URL slug
                title = (title_el.get("title") or link.get("title") or "").strip()
            if not title:
                slug = unquote(urlsplit(link["href"]).path.rstrip("/").rsplit("/", 1)[-1])
                title = re.sub(r"\s+", " ", re.sub(r"[-_]", " ", slug)).strip().title()
            tl = sel.get("text_lines")
            if tl:  # title/location are separate text nodes inside one element, e.g. <a><span>City</span><span>Title</span></a>
                lines = [x for x in item.get_text("\n", strip=True).split("\n") if x]
                if len(lines) > max(tl.values()):
                    title = lines[tl.get("title", 0)]
                    location = lines[tl["location"]] if "location" in tl else location
                else:
                    continue
            url = urljoin(bank.jobs_url, link["href"])
            if url in seen_urls:  # menus and result lists often repeat the same vacancy
                continue
            seen_urls.add(url)
            jobs.append(self.make_job(bank, title=title, url=url, location=location))
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
