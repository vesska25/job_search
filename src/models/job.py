"""Normalized job model shared by all scrapers."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from src.utils.normalization import canonicalize_url


@dataclass
class Job:
    bank_id: str
    bank_name: str
    title: str
    url: str
    location: str = ""
    country: str = ""
    department: str = ""
    description: str = ""
    source_job_id: Optional[str] = None  # ID as given by the source, if any
    published_date: Optional[str] = None  # ISO date string if known
    source_type: str = ""
    # filled in by the filter pipeline
    matched_functions: list = field(default_factory=list)
    borderline: bool = False

    @property
    def canonical_url(self) -> str:
        return canonicalize_url(self.url)

    @property
    def job_id(self) -> str:
        """Stable source ID if available, else deterministic hash of canonical URL."""
        if self.source_job_id:
            return f"{self.bank_id}:{self.source_job_id}"
        digest = hashlib.sha1(self.canonical_url.encode("utf-8")).hexdigest()[:16]
        return f"{self.bank_id}:url-{digest}"
