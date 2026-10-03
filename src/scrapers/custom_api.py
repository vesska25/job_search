"""Config-driven adapter for any JSON endpoint behind a bank's career page.

banks.yaml options example:
    options:
      method: POST                       # GET (default) or POST
      api_url: https://example.com/search
      params: {country: DE}
      json_body: {pageSize: 50}
      items_path: data.jobs              # dotted path to the list of postings
      fields: {title: title, id: id, location: city, department: team, date: posted, url: link}
      url_template: "https://example.com/job/{id}"   # used when no 'url' field
      pagination: {type: offset|page, param: offset, size_param: limit, size: 50,
                   total_path: data.total, in: params|json_body, start: 0, max_pages: 20}
"""
from __future__ import annotations

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError, dig


class CustomApiScraper(BaseScraper):
    source_type = "custom_api"

    def fetch_jobs(self, bank: Bank) -> list:
        o = bank.options
        if not o.get("api_url") or not o.get("items_path"):
            raise ScraperError("custom_api requires options.api_url and options.items_path")
        method = o.get("method", "GET").upper()
        pg = o.get("pagination") or {}
        params, body = dict(o.get("params") or {}), dict(o.get("json_body") or {})
        jobs, seen_ids = [], set()
        pos = pg.get("start", 0 if pg.get("type") != "page" else 1)
        size = pg.get("size", 50)
        for _ in range(min(pg.get("max_pages", 1), self.max_pages) if pg else 1):
            target = body if pg.get("in") == "json_body" else params
            if pg:
                target[pg["param"]] = pos
                if pg.get("size_param"):
                    target[pg["size_param"]] = size
            kwargs = {"params": params}
            if method == "POST":
                kwargs["json"] = body
            data = self.http.request(method, o["api_url"], **kwargs).json()
            batch = self.parse(data, bank)
            new = [j for j in batch if j.job_id not in seen_ids]
            seen_ids.update(j.job_id for j in new)
            jobs.extend(new)
            if not pg or not new:
                break
            pos += size if pg.get("type") != "page" else 1
            total = dig(data, pg.get("total_path", ""), None)
            if total is not None and len(jobs) >= int(total):
                break
        return jobs

    def parse(self, data, bank: Bank) -> list:
        o = bank.options
        items = dig(data, o["items_path"])
        if not isinstance(items, list):
            raise ScraperError(f"custom_api: items_path '{o['items_path']}' is not a list")
        f = {"title": "title", "id": "id", "location": "location", "department": "department",
             "date": "date", "url": "url", "description": "description", **(o.get("fields") or {})}
        jobs = []
        for it in items:
            title, jid = dig(it, f["title"]), dig(it, f["id"])
            url = dig(it, f["url"]) or (o.get("url_template", "").format(id=jid, **{
                k: v for k, v in it.items() if isinstance(v, (str, int))}) if o.get("url_template") else None)
            if not title or not url:
                continue
            loc = dig(it, f["location"], "")
            if isinstance(loc, list):
                loc = ", ".join(str(x) for x in loc)
            jobs.append(self.make_job(
                bank, title=str(title), url=str(url), location=str(loc),
                department=str(dig(it, f["department"], "") or ""),
                description=str(dig(it, f["description"], "") or ""),
                source_job_id=str(jid) if jid is not None else None,
                published_date=(str(dig(it, f["date"], "") or "")[:10] or None),
            ))
        return jobs
