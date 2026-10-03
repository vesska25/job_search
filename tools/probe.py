"""Focused read-only probe of one URL (same honest User-Agent, robots.txt respected).

    python -m tools.probe --url URL --show 6000                 # print the start of the page body
    python -m tools.probe --url URL --grep 'api/job-market' --scripts 80
        # grep the page AND every script of the same registrable domain (up to 80); only scripts with hits are shown
It never tries to bypass blocks. Used to learn how a career page loads its vacancies.
"""
from __future__ import annotations

import argparse
import re
from urllib.parse import urljoin, urlsplit

import requests

from src.config import load_settings
from src.utils.http import HttpClient


def registrable(host: str) -> str:
    return ".".join(host.split(".")[-2:])


def snippets(text: str, pattern: str, ctx: int, limit: int) -> list[str]:
    out, last_end = [], -1
    for m in re.finditer(pattern, text):
        if m.start() < last_end:
            continue
        a, b = max(0, m.start() - ctx), min(len(text), m.end() + ctx)
        out.append(f"[{m.start()}] ..." + re.sub(r"\s+", " ", text[a:b]) + "...")
        last_end = b
        if len(out) >= limit:
            break
    return out


def fetch(http: HttpClient, url: str):
    if not http.allowed(url):
        print(f"  skipped by robots.txt: {url}")
        return None
    try:
        r = http.session.get(url, timeout=http.timeout)
    except requests.RequestException as exc:
        print(f"  request failed: {url}: {exc}")
        return None
    http._fix_encoding(r)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--grep", default="")
    ap.add_argument("--ctx", type=int, default=200)
    ap.add_argument("--limit", type=int, default=6, help="max snippets per file")
    ap.add_argument("--show", type=int, default=0, help="print the first N characters of the body")
    ap.add_argument("--scripts", type=int, default=0, help="also grep up to N scripts of the same registrable domain")
    a = ap.parse_args()
    h = load_settings()["http"]
    http = HttpClient(user_agent=h["user_agent"], timeout=h["timeout"], retries=1, min_delay=0.3, respect_robots=True)
    r = fetch(http, a.url)
    if r is None:
        return
    print(f"== {a.url}\nHTTP {r.status_code}, {r.headers.get('content-type')}, {len(r.text)} bytes")
    text = r.text
    if a.show:
        start = text.lower().find("<body")
        print("-- body start --")
        print(re.sub(r"\s+", " ", text[max(start, 0): max(start, 0) + a.show]))
    if a.grep:
        for s in snippets(text, a.grep, a.ctx, a.limit) or ["(no matches in page)"]:
            print("  ", s)
    if a.scripts:
        host = urlsplit(a.url).netloc
        srcs = []
        for x in re.findall(r"(?i)<(?:script|link)[^>]+(?:src|href)=[\"']([^\"']+\.js[^\"']*)", text):
            u = urljoin(a.url, x)
            if registrable(urlsplit(u).netloc) == registrable(host) and u not in srcs:
                srcs.append(u)
        print(f"-- scripts of *.{registrable(host)}: {len(srcs)} found, scanning {min(len(srcs), a.scripts)} --")
        for u in srcs[: a.scripts]:
            sr = fetch(http, u)
            if sr is None or not a.grep:
                continue
            hits = snippets(sr.text, a.grep, a.ctx, a.limit)
            if hits:
                print(f"== script {u} ({len(sr.text)} bytes): {len(hits)} hit(s)")
                for s in hits:
                    print("  ", s)


if __name__ == "__main__":
    main()
