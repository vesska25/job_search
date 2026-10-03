"""HTTP client: timeouts, retries, User-Agent, robots.txt compliance, per-host rate limiting."""
from __future__ import annotations

import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.utils.logging import get_logger
from src.utils.robots import RobotsRules

log = get_logger("http")

DEFAULT_UA = "bank-job-monitor/1.0 (personal job-alert tool; weekly run; respects robots.txt)"


class RobotsDisallowed(Exception):
    pass


class HttpClient:
    def __init__(self, user_agent: str = DEFAULT_UA, timeout: float = 20, retries: int = 3,
                 min_delay: float = 1.0, respect_robots: bool = True, session=None):
        self.timeout = timeout
        self.min_delay = min_delay
        self.respect_robots = respect_robots
        self.user_agent = user_agent
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": "de,en;q=0.8"})
        if session is None:
            retry = Retry(total=retries, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=("GET", "POST"), respect_retry_after_header=True)
            adapter = HTTPAdapter(max_retries=retry)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotsRules | None] = {}

    # -- robots ---------------------------------------------------------------
    def _robots_for(self, url: str):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        try:
            resp = self.session.get(origin + "/robots.txt", timeout=self.timeout)
            if resp.status_code == 200:
                self._fix_encoding(resp)
                rules = RobotsRules(resp.text, self.user_agent)
            elif 400 <= resp.status_code < 500:
                rules = None                      # no robots.txt -> everything allowed
            else:
                rules = RobotsRules(disallow_all=True)   # server error: be conservative
        except requests.RequestException as exc:
            log.warning("robots.txt unreachable for %s (%s); being conservative", origin, exc)
            rules = RobotsRules(disallow_all=True)
        self._robots[origin] = rules
        return rules

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rules = self._robots_for(url)
        return True if rules is None else rules.allowed(url)

    # -- requests -------------------------------------------------------------
    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        wait = self.min_delay - (time.monotonic() - self._last_request.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows or could not be checked for {url}")
        self._throttle(url)
        kwargs.setdefault("timeout", self.timeout)
        resp = self.session.request(method, url, **kwargs)
        resp.raise_for_status()
        self._fix_encoding(resp)
        return resp

    @staticmethod
    def _fix_encoding(resp) -> None:
        """requests falls back to ISO-8859-1 for text/* without a charset, which garbles umlauts
        (Düsseldorf -> DÃ¼sseldorf). If the server declared no charset, detect it from the content."""
        try:
            declared = "charset" in (resp.headers.get("Content-Type", "") or "").lower()
            if not declared and getattr(resp, "content", None):
                resp.encoding = resp.apparent_encoding or "utf-8"
        except (AttributeError, TypeError):
            pass

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)
