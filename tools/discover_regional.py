"""Read-only discovery: which Sparkassen / Volksbanken publish vacancies on the shared exchanges, and where do they sit.

    python -m tools.discover_regional
Sparkassen: sparkasse.de job-market API paged WITHOUT bankCode, distinct client {name, bankCode, city, zip}.
Volksbanken: vr.de jobs sitemap, distinct bank slugs (+ one vacancy page each for the JSON-LD address).
Prints one line per bank so the region (Frankfurt / Köln +-30 km) can be judged; adds nothing to banks.yaml.
"""
from __future__ import annotations

import json
import re

from src.config import load_settings
from src.scrapers.sparkasse_jobmarket import API_URL
from src.scrapers.vr_jobs import LOC, SITEMAP_URL
from src.utils.http import HttpClient



def sparkassen(http):
    seen, offset, total = {}, 0, None
    for _ in range(400):
        data = http.get(API_URL, params={"limit": 50, "offset": offset}).json()
        block = data.get("jobs") or {}
        items = block.get("items") or []
        total = block.get("count")
        for it in items:
            c = it.get("client") or {}
            a = (it.get("addresses") or [{}])[0] or c.get("address") or {}
            key = str(c.get("bankCode") or c.get("name"))
            e = seen.setdefault(key, {"name": c.get("name"), "bankCode": c.get("bankCode"), "n": 0,
                                      "city": a.get("city"), "zip": a.get("zipCode") or a.get("zip") or a.get("postalCode")})
            e["n"] += 1
        offset += 50
        if not items or (total and offset >= total):
            break
    print(f"SPARKASSEN: total={total} distinct_clients={len(seen)}")
    for e in sorted(seen.values(), key=lambda x: str(x["zip"])):
        print("SPK|", json.dumps(e, ensure_ascii=False))


def volksbanken(http):
    text = http.get(SITEMAP_URL).text
    ents = LOC.findall(text)
    print(f"VR: vacancy pages={len(ents)}")
    banks = {}
    for url, slug, jid in ents:
        # bank slug = suffix after the title slug; unknown boundary, so keep the last 4 dash parts as candidate key
        banks.setdefault("-".join(slug.split("-")[-4:]), []).append(url)
    print(f"VR: candidate bank keys={len(banks)}")
    for k, urls in sorted(banks.items()):
        print("VR|", k, len(urls), urls[0])


if __name__ == "__main__":
    h = load_settings()["http"]
    http = HttpClient(user_agent=h["user_agent"], timeout=h["timeout"], retries=1, min_delay=0.3, respect_robots=True)
    for fn in (sparkassen, volksbanken):
        try:
            fn(http)
        except Exception as exc:  # report, keep going
            print(f"{fn.__name__} FAILED: {exc!r}")
