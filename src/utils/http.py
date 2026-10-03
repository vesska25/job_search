"""HTTP client: timeouts, retries, User-Agent, robots.txt compliance, per-host rate limiting."""
from __future__ import annotations

import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.utils.logging import get_logger

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
        self._robots: dict[str, RobotFileParser | None] = {}

    # -- robots ---------------------------------------------------------------
    def _robots_for(self, url: str):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp = RobotFileParser()
        parser = None
        try:
            resp = self.session.get(origin + "/robots.txt", timeout=self.timeout)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                parser = rp
            elif 400 <= resp.status_code < 500:
                parser = None  # no robots.txt -> everything allowed
            else:
                rp.disallow_all = True  # server error: be conservative
                parser = rp
        except requests.RequestException as exc:
            log.warning("robots.txt unreachable for %s (%s); being conservative", origin, exc)
            rp.disallow_all = True
            parser = rp
        self._robots[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parser = self._robots_for(url)
        return True if parser is None else parser.can_fetch(self.user_agent, url)

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
        return resp

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)
