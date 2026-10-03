"""Read-only diagnosis of a bank career page, using the same honest User-Agent and robots.txt rules.

    python -m tools.diagnose_source --bank abn_amro
    python -m tools.diagnose_source --bank abn_amro,bnp_paribas,commerzbank
    python -m tools.diagnose_source --url https://example.com/careers

Reports: HTTP status, robots.txt rules for the page, sitemap URLs, JSON-LD JobPosting presence,
and API-looking URLs referenced in the page source (candidates for a custom_api config).
It does NOT try to bypass blocks: a 403/429 or a robots.txt Disallow is reported and respected.
"""
from __future__ import annotations

import argparse
import re
from urllib.parse import urljoin, urlsplit

import requests

from src.config import load_banks, load_settings
from src.utils.http import HttpClient

API_HINT = re.compile(r"""["'(]((?:https?:)?//[^"'\s)]+|/[^"'\s)]*)(?:api|graphql|search|jobs|vacanc|positions)[^"'\s)]*["')]""", re.I)


def diagnose(url: str, http: HttpClient) -> None:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    print(f"== {url}")
    try:
        r = http.session.get(origin + "/robots.txt", timeout=http.timeout)
        print(f"robots.txt: HTTP {r.status_code}")
        if r.status_code == 200:
            for line in r.text.splitlines():
                if re.match(r"(?i)\s*(user-agent|disallow|allow|sitemap|crawl-delay)", line):
                    print("   ", line.strip())
    except requests.RequestException as exc:
        print("robots.txt unreachable:", exc)
    print("allowed by robots.txt for our User-Agent:", http.allowed(url))
    if not http.allowed(url):
        return
    try:
        r = http.session.get(url, timeout=http.timeout)
    except requests.RequestException as exc:
        print("request failed:", exc)
        return
    print(f"page: HTTP {r.status_code}, {len(r.text)} bytes, server={r.headers.get('server')}")
    if r.status_code >= 400:
        print("Blocked or unavailable for automated access. Not bypassing. Options: bank job-alert e-mails, "
              "ask the bank for a feed, or leave disabled.")
        return
    print("JSON-LD JobPosting present:", "JobPosting" in r.text)
    hits = sorted({urljoin(url, m.group(0).strip("\"'()")) for m in API_HINT.finditer(r.text)})
    print(f"API-looking URLs in page source ({len(hits)}):")
    for h in hits[:40]:
        print("   ", h)
    for s in re.findall(r"(?i)<script[^>]+src=[\"']([^\"']+)", r.text)[:15]:
        print("script:", urljoin(url, s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank")
    ap.add_argument("--url")
    a = ap.parse_args()
    h = load_settings()["http"]
    http = HttpClient(user_agent=h["user_agent"], timeout=h["timeout"], retries=1, min_delay=1, respect_robots=True)
    ids = {x.strip() for x in (a.bank or "").split(",") if x.strip()}
    urls = [a.url] if a.url else [b.jobs_url for b in load_banks() if b.id in ids and b.jobs_url]
    if not urls:
        raise SystemExit("Give --url or a --bank id that has a jobs_url")
    for u in urls:
        diagnose(u, http)


if __name__ == "__main__":
    main()
