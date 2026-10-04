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

MAX_BODY = 3_000_000          # a single response larger than this is not stored
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

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.entries: list[dict] = []
        self.skipped: int = 0

    def request(self, method, url, **kwargs):
        resp = super().request(method, url, **kwargs)
        if len(resp.content) > MAX_BODY or len(self.entries) >= MAX_ENTRIES:
            self.skipped += 1                      # the replay will miss it: the capture is then not usable for this bank
        else:
            self.entries.append(_entry(resp, request_key(method, url, kwargs)))
        return resp


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


def save_contract(path: Path, bank_id: str, jobs: list, entries: list[dict], captured: str) -> int:
    doc = {"bank_id": bank_id, "captured": captured, "count": len(jobs),
           "sample_titles": [j.title for j in jobs[:3]], "entries": entries}
    path.parent.mkdir(parents=True, exist_ok=True)
    data = gzip.compress(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8"), mtime=0)
    path.write_bytes(data)
    return len(data)


def load_contract(path: Path) -> dict:
    return json.loads(gzip.decompress(Path(path).read_bytes()).decode("utf-8"))
