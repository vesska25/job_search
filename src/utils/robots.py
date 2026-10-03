"""robots.txt evaluation following RFC 9309 (wildcards `*` and `$`, longest match wins, Allow wins ties).

Python's urllib.robotparser ignores wildcards inside paths, which wrongly blocks sites that Allow
specific URL patterns and Disallow everything else (e.g. `Allow: /jobportal/*/rss` + `Disallow: /`).
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


def _pattern_to_regex(path: str):
    anchored = path.endswith("$")
    if anchored:
        path = path[:-1]
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in path)
    return re.compile("^" + rx + ("$" if anchored else ""))


class RobotsRules:
    def __init__(self, text: str = "", user_agent: str = "bank-job-monitor", disallow_all: bool = False):
        self.disallow_all = disallow_all
        self.rules: list[tuple[bool, str, re.Pattern]] = []  # (allow, pattern, regex)
        if not disallow_all:
            self._parse(text, user_agent.split("/")[0].lower())

    def _parse(self, text: str, token: str) -> None:
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if in_rules:
                    groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if value:               # empty Disallow means "allow everything"
                    rules.append((key == "allow", value))
        if agents:
            groups.append((agents, rules))
        specific = [g for g in groups if any(a != "*" and a in token for a in g[0])]
        chosen = specific or [g for g in groups if "*" in g[0]]
        for _, rs in chosen:            # groups for the same agent are merged
            for allow, pat in rs:
                self.rules.append((allow, pat, _pattern_to_regex(pat)))

    def allowed(self, url: str) -> bool:
        if self.disallow_all:
            return False
        parts = urlsplit(url)
        path = unquote(parts.path or "/") + (("?" + parts.query) if parts.query else "")
        best_len, best_allow = -1, True
        for allow, pat, rx in self.rules:
            if rx.match(path):
                length = len(pat)
                if length > best_len or (length == best_len and allow):
                    best_len, best_allow = length, allow
        return best_allow
