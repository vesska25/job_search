"""Text and URL normalization helpers."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

TRACKING_PARAMS = {
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "ref", "referrer", "source",
    "src", "trk", "trkid", "sid", "sessionid", "jsessionid", "campaign", "cid",
    "_ga", "igshid", "lang_tracking", "from", "origin",
}
TRACKING_PREFIXES = ("utm_", "wt_", "pk_", "mtm_", "hsa_", "vero_")


def canonicalize_url(url: str) -> str:
    """Lowercase scheme/host, drop fragment, tracking params and trailing slash; sort params."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"
    netloc = parts.netloc.lower()
    if netloc.endswith(":443"):
        netloc = netloc[:-4]
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    query.sort()
    path = re.sub(r"/{2,}", "/", parts.path)
    # drop ";jsessionid=..." path parameters
    path = re.sub(r";jsessionid=[^/?]*", "", path, flags=re.I)
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def normalize_text(text: str) -> str:
    """Casefold, NFKC-normalize and collapse whitespace (used for matching)."""
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def html_to_text(html: str) -> str:
    if not html:
        return ""
    return re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ")).strip()


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
