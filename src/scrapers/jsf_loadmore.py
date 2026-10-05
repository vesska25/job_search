"""Server-rendered lists with a JSF/PrimeFaces "load more" button (e.g. the TK Stellenmarkt).

The first GET returns the first page of vacancies as HTML plus a command link whose onclick is
`PrimeFaces.ab({s:"<link id>", f:"<form id>", u:"<ids to re-render>"})` and a hidden `javax.faces.ViewState`.
A browser click sends a partial-ajax POST to the same URL (same session cookie); the answer is XML with the re-rendered
fragments in CDATA and a fresh ViewState. This adapter repeats exactly that click until the link disappears or nothing
new arrives, and reads every fragment with the normal custom_html selectors, so a source needs the same options as
custom_html (selectors / link_pattern, base_url, regions, ...).

robots.txt is respected like everywhere else (the POST goes to the page URL that the GET already passed).

banks.yaml options (besides the custom_html ones):
  more_link_selector: 'a[id$=":mehrladen"]'   (default; the command link that loads the next page)
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from src.config import Bank
from src.scrapers.base import ScraperError
from src.scrapers.generic_html import GenericHtmlScraper

UPDATE = re.compile(r'<update id="([^"]+)"><!\[CDATA\[(.*?)\]\]></update>', re.S)
AB_PARAM = re.compile(r'\b([sfu]):"([^"]*)"')
DEFAULT_MORE = 'a[id$=":mehrladen"]'


class JsfLoadMoreScraper(GenericHtmlScraper):
    source_type = "jsf_loadmore"

    def fetch_jobs(self, bank: Bank) -> list:
        html = self.http.get(bank.jobs_url).text
        jobs, seen, state = [], set(), None
        self._collect(self._safe_parse(html, bank), jobs, seen)
        for _ in range(self.max_pages):
            request = self.more_request(html, bank, state)
            if request is None:                      # no "load more" link left: the list is complete
                break
            data = self.http.post(bank.jobs_url, data=request, headers={
                "Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/xml, text/xml, */*; q=0.01"}).text
            updates = {uid: frag for uid, frag in UPDATE.findall(data)}
            if not updates:
                raise ScraperError("JSF answer has no <update> blocks (layout/session change?)")
            fragments = [f for uid, f in updates.items() if "ViewState" not in uid]
            state = next((f.strip() for uid, f in updates.items() if "ViewState" in uid), state)
            html = "".join(fragments)               # the ViewState of the answer replaces the old one in the next click
            if not self._collect(self._safe_parse(html, bank), jobs, seen):
                break
        return jobs

    def _safe_parse(self, html: str, bank: Bank) -> list:
        try:
            return self.parse(html, bank)
        except ScraperError:
            return []                                # an empty fragment is the end of the list, not an error

    @staticmethod
    def _collect(found: list, jobs: list, seen: set) -> int:
        """Add vacancies not seen yet (fragments may repeat earlier items); return how many were new."""
        new = [j for j in found if j.job_id not in seen]
        seen.update(j.job_id for j in new)
        jobs.extend(new)
        return len(new)

    def more_request(self, html: str, bank: Bank, state: str | None = None) -> dict | None:
        """Form data of the "load more" click, or None when the link is not in `html`.
        `state` is the ViewState of the latest answer; the first click reads it from the page."""
        soup = BeautifulSoup(html, "html.parser")
        link = soup.select_one(bank.options.get("more_link_selector") or DEFAULT_MORE)
        if link is None:
            return None
        params = dict(AB_PARAM.findall(link.get("onclick") or ""))
        source, form = params.get("s") or link.get("id"), params.get("f")
        if state is None:
            tag = soup.select_one('input[name="javax.faces.ViewState"]')
            state = tag.get("value") if tag is not None else None
        if not source or not form or state is None:
            raise ScraperError("JSF load-more link lacks source/form id or ViewState (layout change?)")
        return {"javax.faces.partial.ajax": "true", "javax.faces.source": source,
                "javax.faces.partial.execute": "@all", "javax.faces.partial.render": params.get("u") or source,
                source: source, form: form, "javax.faces.ViewState": state}
