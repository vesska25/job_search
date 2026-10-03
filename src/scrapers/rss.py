"""RSS/Atom-style job feeds (e.g. Guidecom job portals expose /jobportal/<name>/rss).

banks.yaml options:
  feed_url: https://jobs.example.com/jobportal/acme/rss      (default: jobs_url)
  location_regex: "\\\\(([^()]+)\\\\)\\\\s*$"                      (optional; group 1 from title, then description)
"""
from __future__ import annotations

import re
from email.utils import parsedate_to_datetime

from defusedxml import ElementTree as ET

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError
from src.utils.normalization import html_to_text


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class RssScraper(BaseScraper):
    source_type = "rss"

    def fetch_jobs(self, bank: Bank) -> list:
        url = bank.options.get("feed_url") or bank.jobs_url
        return self.parse(self.http.get(url).content, bank)

    def parse(self, data, bank: Bank) -> list:
        try:
            root = ET.fromstring(data if isinstance(data, bytes) else data.encode("utf-8"))
        except ET.ParseError as exc:
            raise ScraperError(f"RSS parse error: {exc}") from exc
        items = [e for e in root.iter() if _local(e.tag) in ("item", "entry")]
        if not items and _local(root.tag) not in ("rss", "feed", "RDF"):
            raise ScraperError(f"Unexpected feed root <{_local(root.tag)}>")
        rx = re.compile(bank.options["location_regex"]) if bank.options.get("location_regex") else None
        jobs = []
        for it in items:
            f = {}
            for ch in it:
                name = _local(ch.tag)
                f.setdefault(name, (ch.text or "").strip() if name != "link" or ch.text else ch.attrib.get("href", ""))
            title, link = f.get("title", ""), f.get("link", "")
            if not title or not link:
                continue
            desc = html_to_text(f.get("description") or f.get("summary") or f.get("content") or "")
            loc = f.get("location", "")
            if rx and not loc:
                m = rx.search(title) or rx.search(desc)
                loc = m.group(1).strip() if m else ""
            date = ""
            raw = f.get("pubDate") or f.get("published") or f.get("updated") or ""
            if raw:
                try:
                    date = parsedate_to_datetime(raw).date().isoformat()
                except (TypeError, ValueError):
                    date = raw[:10]
            jobs.append(self.make_job(bank, title=title, url=link, location=loc, description=desc,
                                      source_job_id=f.get("guid") or None, published_date=date or None))
        return jobs
