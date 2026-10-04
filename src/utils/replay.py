"""Record and replay HTTP traffic of one source, for contract tests.

Recording (tools/capture_fixtures.py, runs on GitHub Actions with network access) wraps the real HttpClient and keeps every
response an adapter fetched. Replaying (tests/test_contracts.py) answers the same requests from that file, so the real adapter
code runs against real, frozen ATS responses without any network. A request that was not recorded raises ReplayMiss: the adapter
now asks for something different, which is exactly the kind of change a contract test must surface.
Only URL, query/body parameters, status, content type and body are stored - never request headers or cookies.
"""
from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

import requests

from src.utils.http import HttpClient

MAX_BODY = 6_000_000          # a single response larger than this is not stored
SHARED_MIN = 400_000          # bodies at least this large are stored once in tests/contracts/_shared/<sha>.json.gz
MAX_ENTRIES = 150


class ReplayMiss(Exception):
    pass


def request_key(method: str, url: str, kwargs: dict) -> str:
    body = {k: kwargs.get(k) for k in ("params", "data", "json") if kwargs.get(k) is not None}
    return f"{method.upper()} {url} {json.dumps(body, sort_keys=True, default=str)}"


def _entry(resp: requests.Response, key: str) -> dict:
    enc = resp.encoding or "utf-8"
    e = {"key": key, "status": resp.status_code, "content_type": resp.headers.get("Content-Type", ""), "encoding": enc}
    try:
        text = resp.content.decode(enc)
        if text.encode(enc) != resp.content:
            raise ValueError
        e["text"] = text
    except (UnicodeError, LookupError, ValueError):
        e["b64"] = base64.b64encode(resp.content).decode("ascii")
    return e


def _response(e: dict, url: str) -> requests.Response:
    r = requests.Response()
    r.status_code = e["status"]
    r.url = url
    r.encoding = e["encoding"]
    r.headers["Content-Type"] = e.get("content_type", "")
    r._content = base64.b64decode(e["b64"]) if "b64" in e else e["text"].encode(e["encoding"])
    return r


class RecordingHttpClient(HttpClient):
    """Real client (robots.txt, throttling and all) that remembers what it fetched."""

    def __init__(self, *a, cache: dict | None = None, **kw):
        super().__init__(*a, **kw)
        self.entries: list[dict] = []
        self.skipped: int = 0
        self.cache = cache if cache is not None else {}     # shared across banks of one capture run: big GETs are downloaded once

    def request(self, method, url, **kwargs):
        key = request_key(method, url, kwargs)
        if method.upper() == "GET" and key in self.cache:
            e = self.cache[key]
            self.entries.append(e)
            return _response(e, url)
        try:
            resp = super().request(method, url, **kwargs)
        except requests.HTTPError as exc:          # adapters often catch a 404 and carry on: record it so the replay does the same
            if exc.response is not None:
                self._keep(exc.response, method, url, kwargs)
            raise
        self._keep(resp, method, url, kwargs)
        return resp

    def _keep(self, resp, method, url, kwargs):
        if len(resp.content) > MAX_BODY or len(self.entries) >= MAX_ENTRIES:
            self.skipped += 1                      # the replay will miss it: the capture is then not usable for this bank
        else:
            e = _entry(resp, request_key(method, url, kwargs))
            self.entries.append(e)
            if method.upper() == "GET" and resp.status_code == 200 and len(resp.content) >= SHARED_MIN:
                self.cache[e["key"]] = e


class ReplayHttpClient:
    """Drop-in for HttpClient (adapters only call get/post/request)."""

    def __init__(self, entries: list[dict]):
        self.entries = {e["key"]: e for e in entries}

    def request(self, method, url, **kwargs):
        key = request_key(method, url, kwargs)
        try:
            e = self.entries[key]
        except KeyError:
            raise ReplayMiss(f"no recorded response for {key[:300]}") from None
        r = _response(e, url)
        r.raise_for_status()
        return r

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


def _gz(doc) -> bytes:
    return gzip.compress(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8"), mtime=0)


def save_contract(path: Path, bank_id: str, jobs: list, entries: list[dict], captured: str) -> int:
    """Writes the contract; large response bodies go to <dir>/_shared/<sha1>.json.gz (identical downloads, such as the vr.de
    sitemap used by 22 banks, are stored once). Returns the size of the bank's own file."""
    import hashlib
    shared_dir = path.parent / "_shared"
    slim = []
    for e in entries:
        body = e.get("text") or e.get("b64") or ""
        if len(body) >= SHARED_MIN:
            sha = hashlib.sha1((e["key"] + body).encode("utf-8", "replace")).hexdigest()
            shared_dir.mkdir(parents=True, exist_ok=True)
            f = shared_dir / f"{sha}.json.gz"
            if not f.exists():
                f.write_bytes(_gz(e))
            slim.append({"key": e["key"], "shared": sha})
        else:
            slim.append(e)
    doc = {"bank_id": bank_id, "captured": captured, "count": len(jobs),
           "sample_titles": [j.title for j in jobs[:3]], "entries": slim}
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _gz(doc)
    path.write_bytes(data)
    return len(data)


def load_contract(path: Path) -> dict:
    path = Path(path)
    doc = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    for i, e in enumerate(doc["entries"]):
        if "shared" in e:
            doc["entries"][i] = json.loads(gzip.decompress((path.parent / "_shared" / f"{e['shared']}.json.gz").read_bytes()).decode("utf-8"))
    return doc


def check_contract(bank, doc: dict, max_pages: int) -> list[str]:
    """Replay a recorded contract through the real adapter. Returns a list of problems (empty = the contract holds)."""
    from src.scrapers import get_scraper
    scraper = get_scraper(bank.source_type, ReplayHttpClient(doc["entries"]), max_pages=max_pages)
    try:
        jobs = scraper.fetch_jobs(bank)
    except ReplayMiss as exc:
        return [f"the adapter requests something that was not recorded: {exc}"]
    except Exception as exc:  # noqa: BLE001
        return [f"the adapter fails on the recorded responses: {type(exc).__name__}: {exc}"]
    problems = []
    if len(jobs) != doc["count"]:
        problems.append(f"{len(jobs)} vacancies, recorded {doc['count']}")
    if [j.title for j in jobs[:3]] != doc["sample_titles"]:
        problems.append("the first titles differ from the recorded ones")
    if any(len(j.title.strip()) < 3 for j in jobs):
        problems.append("empty or tiny title")
    if any(not j.url.startswith("http") for j in jobs):
        problems.append("relative or empty URL")
    ids = [j.job_id for j in jobs]
    if len(ids) != len(set(ids)):
        problems.append(f"duplicate job ids ({len(ids) - len(set(ids))})")
    return problems
