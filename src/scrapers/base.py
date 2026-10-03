"""Scraper interface. One adapter per ATS type; banks only supply configuration."""
from __future__ import annotations

from typing import Any, Iterable, Optional

from src.config import Bank
from src.models.job import Job
from src.utils.http import HttpClient


class ScraperError(Exception):
    """Raised when a source cannot be parsed (layout change, unexpected response)."""


class BaseScraper:
    source_type = "base"

    def __init__(self, http: HttpClient, max_pages: int = 30):
        self.http = http
        self.max_pages = max_pages

    def fetch_jobs(self, bank: Bank) -> list[Job]:
        raise NotImplementedError

    def make_job(self, bank: Bank, **kw) -> Job:
        kw.setdefault("source_type", self.source_type)
        return Job(bank_id=bank.id, bank_name=bank.label, **kw)


def dig(obj: Any, path: str, default: Any = None) -> Any:
    """Read dotted path ('a.b.0.c') from nested dict/list."""
    cur = obj
    for part in filter(None, (path or "").split(".")):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return default
        if cur is None:
            return default
    return cur


def first(items: Iterable, default: Optional[Any] = None):
    for i in items:
        if i:
            return i
    return default
