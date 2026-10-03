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

STR_LIT = re.compile(r"""["'`]((?:https?:)?//[^"'`\s]{6,}|/[A-Za-z0-9_\-./?=&%]{3,})["'`]""")
KEYWORDS = re.compile(r"api|search|beesite|graphql|\.json|job|vacanc|position|stelle|career", re.I)
API_HINT = re.compile(r"""["'(]((?:https?:)?//[^"'\s)]+|/[^"'\s)]*)(?:api|graphql|search|jobs|vacanc|positions)[^"'\s)]*["')]""", re.I)


DEFAULT_PATTERN = r"api-jobs|/api/|ajax|fetch\(|getJSON|XMLHttpRequest|\.json|search_result|jobplatform|vacanc"


def grep_js(url: str, http: HttpClient, pattern: str, ctx: int = 220, limit: int = 15) -> None:
    """Print short snippets of a public script around `pattern` (to learn how a career page loads its data)."""
    if not http.allowed(url):
        print("script skipped by robots.txt:", url)
        return
    try:
        js = http.session.get(url, timeout=http.timeout).text
    except requests.RequestException as exc:
        print("script failed:", url, exc)
        return
    print(f"== script {url} ({len(js)} bytes), pattern /{pattern}/")
    last_end, shown = -1, 0
    for m in re.finditer(pattern, js):
        if m.start() < last_end:
            continue
        a, b = max(0, m.start() - ctx), min(len(js), m.end() + ctx)
        snippet = re.sub(r"\s+", " ", js[a:b])
        print(f"  [{m.start()}] ...{snippet}...")
        last_end, shown = b, shown + 1
        if shown >= limit:
            print("  (limit reached)")
            break
    if not shown:
        print("  no matches")


def diagnose(url: str, http: HttpClient, pattern: str = DEFAULT_PATTERN, ctx: int = 220) -> None:
    if re.search(r"\.js(\?|$)", urlsplit(url).path + ("?" if "?" in url else "")):
        grep_js(url, http, pattern, ctx)
        return
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
    probe = re.compile(r"m/w/d|w/m/d|m/f/d|Stellenanzeige|Vacancy", re.I)
    hits = list(probe.finditer(r.text))
    print(f"job-title markers (m/w/d, Stellenanzeige ...) in raw HTML: {len(hits)}")
    for m in hits[:4]:
        snippet = re.sub(r"\s+", " ", r.text[max(0, m.start() - 160): m.end() + 100])
        print("    ...", snippet, "...")
    for m in re.finditer(r"(?is)<script([^>]*type=[\"']application/(?:json|ld\+json)[\"'][^>]*)>(.*?)</script>", r.text):
        print(f"embedded JSON block: attrs={m.group(1).strip()[:80]!r} size={len(m.group(2))}")
    hits = sorted({urljoin(url, m.group(0).strip("\"'()")) for m in API_HINT.finditer(r.text)})
    print(f"API-looking URLs in page source ({len(hits)}):")
    for h in hits[:40]:
        print("   ", h)
    anchors = {}
    for m in re.finditer(r"""(?is)<a\s[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""", r.text):
        href = urljoin(url, m.group(1))
        if urlsplit(href).netloc == parts.netloc and not re.search(r"\.(css|js|png|jpg|svg|ico)(\?|$)", href):
            anchors.setdefault(href, re.sub(r"<[^>]+>|\s+", " ", m.group(2)).strip()[:60])
    print(f"same-site links ({len(anchors)}), first 40:")
    for href, text in list(anchors.items())[:40]:
        print("    ", href, "|", text)
    scripts = [urljoin(url, x) for x in re.findall(r"(?i)<script[^>]+src=[\"']([^\"']+)", r.text)]
    own = [x for x in scripts if urlsplit(x).netloc == parts.netloc][:8]
    print(f"first-party scripts scanned for endpoint strings: {len(own)}")
    for sc in own:
        if not http.allowed(sc):
            print("script skipped by robots.txt:", sc)
            continue
        try:
            js = http.session.get(sc, timeout=http.timeout).text
        except requests.RequestException as exc:
            print("script failed:", sc, exc)
            continue
        found = sorted({m.group(1) for m in STR_LIT.finditer(js) if KEYWORDS.search(m.group(1))})
        print(f"  {sc}: {len(found)} candidate strings")
        for f in found[:30]:
            print("      ", f)
        for f in found:
            nxt = urljoin(sc, f)
            if re.search(r"\.js(\?|$)", f) and urlsplit(nxt).netloc == parts.netloc and nxt not in own:
                print("  second-level script referenced:", nxt)
                grep_js(nxt, http, pattern, ctx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank")
    ap.add_argument("--url", help="one or more URLs, comma-separated (a .js URL prints snippets around --pattern)")
    ap.add_argument("--context", type=int, default=220, help="characters of context around each --pattern match")
    ap.add_argument("--pattern", default=DEFAULT_PATTERN, help="regex searched in .js files")
    a = ap.parse_args()
    h = load_settings()["http"]
    http = HttpClient(user_agent=h["user_agent"], timeout=h["timeout"], retries=1, min_delay=1, respect_robots=True)
    ids = {x.strip() for x in (a.bank or "").split(",") if x.strip()}
    urls = [u.strip() for u in a.url.split(",") if u.strip()] if a.url else [b.jobs_url for b in load_banks() if b.id in ids and b.jobs_url]
    if not urls:
        raise SystemExit("Give --url or a --bank id that has a jobs_url")
    for u in urls:
        diagnose(u, http, a.pattern, a.context)


if __name__ == "__main__":
    main()
