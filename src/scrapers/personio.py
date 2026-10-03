"""Personio: public XML feed at https://<company>.jobs.personio.de/xml."""
from __future__ import annotations

from urllib.parse import urlsplit

from defusedxml import ElementTree as ET

from src.config import Bank
from src.scrapers.base import BaseScraper, ScraperError
from src.utils.normalization import html_to_text


class PersonioScraper(BaseScraper):
    source_type = "personio"

    def fetch_jobs(self, bank: Bank) -> list:
        parts = urlsplit(bank.jobs_url)
        base = f"{parts.scheme}://{parts.netloc}"
        resp = self.http.get(base + "/xml", params={"language": bank.options.get("language", "de")})
        return self.parse(resp.text, bank, base)

    def parse(self, xml_text: str, bank: Bank, base: str) -> list:
        try:
            root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
        except ET.ParseError as exc:
            raise ScraperError(f"Personio XML parse error: {exc}") from exc
        if root.tag != "workzag-jobs":
            raise ScraperError(f"Unexpected Personio root element <{root.tag}>")
        jobs = []
        for pos in root.findall("position"):
            pid = (pos.findtext("id") or "").strip()
            title = (pos.findtext("name") or "").strip()
            if not pid or not title:
                continue
            desc = " ".join(
                html_to_text(d.findtext("value") or "") for d in pos.findall("jobDescriptions/jobDescription")
            )
            jobs.append(self.make_job(
                bank, title=title, url=f"{base}/job/{pid}",
                location=(pos.findtext("office") or "").strip(),
                department=(pos.findtext("department") or "").strip(),
                description=desc, source_job_id=pid,
                published_date=(pos.findtext("createdAt") or "")[:10] or None,
            ))
        return jobs
