"""Telegram delivery and message formatting (HTML parse mode)."""
from __future__ import annotations

import html
import time
from datetime import date, timedelta
from typing import Iterable

import requests

from src.utils.logging import get_logger

log = get_logger("telegram")
MAX_LEN = 3000  # one message never exceeds 3000 characters (Telegram allows 4096)
EMPTY_TEXT = "No new matching senior banking vacancies this week."


def week_of(today: date | None = None) -> str:
    today = today or date.today()
    return (today - timedelta(days=today.weekday())).isoformat()


def _e(text: str) -> str:
    return html.escape(text or "", quote=False)


def format_job(job) -> str:
    areas = " / ".join(dict.fromkeys(job.matched_functions[:3])) or "n/a"
    flag = " (borderline)" if job.borderline else ""
    return (
        f"🏦 Bank: {_e(job.bank_name)}\n"
        f"📌 {_e(job.title)}{flag}\n"
        f"📍 {_e(job.location or 'Germany')}\n"
        f"🔎 Relevant areas: {_e(areas)}\n"
        f"🔗 Open vacancy:\n{_e(job.url)}\n"
        f"Source: {_e(job.bank_name)}"
    )


def format_health(issues: list) -> str:
    """Block listing sources whose career page probably changed (see src/health.py). Empty string if all is well."""
    if not issues:
        return ""
    lines = "\n".join(f"• {_e(i.line())}" for i in issues[:25])
    more = f"\n… and {len(issues) - 25} more (see the run log)." if len(issues) > 25 else ""
    return f"\n⚠️ <b>Sources need attention</b> (the career page may have changed):\n{lines}{more}\n"


def format_digest(jobs: list, today: date | None = None, max_jobs: int = 40, failed_sources: int = 0,
                  title: str = "BANK JOB MONITOR", empty_text: str = EMPTY_TEXT, health_issues: list | None = None) -> list[str]:
    """Return one or more messages (split below Telegram's length limit)."""
    health = format_health(health_issues or [])
    if not jobs:
        return [empty_text + ("\n" + health if health else "")]
    header = f"<b>{_e(title)}</b>\nWeek of {week_of(today)}\nNew matching vacancies: {len(jobs)}\n"
    blocks = []
    for i, j in enumerate(jobs[:max_jobs], 1):
        flag = " (borderline)" if j.borderline else ""
        blocks.append(f"\n{i}. <b>{_e(j.bank_name)}</b> — {_e(j.title)}{flag}\n{_e(j.location or 'Germany')}\n{_e(j.url)}\n")
    if len(jobs) > max_jobs:
        blocks.append(f"\n… and {len(jobs) - max_jobs} more (see the run log).\n")
    if health:
        blocks.append(health)
    elif failed_sources:
        blocks.append(f"\n⚠️ {failed_sources} source(s) failed this run; check the log.")
    return _split(header, blocks)


def _split(header: str, blocks: list[str]) -> list[str]:
    messages, current = [], header
    for b in blocks:
        if len(current) + len(b) > MAX_LEN:
            messages.append(current)
            current = ""
        current += b
    messages.append(current)
    if len(messages) > 1:     # label the parts so a missing one is noticed ("2/3" without "3/3")
        messages = [f"<i>Part {i}/{len(messages)}</i>\n{m}" for i, m in enumerate(messages, 1)]
    return messages


class TelegramNotifier:
    RETRY_DELAYS = (3, 10)        # pauses between the 3 attempts of one request

    def __init__(self, token: str, chat_id: str, session=None, timeout: float = 20, sleep=time.sleep):
        if not token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        self.token, self.chat_id, self.timeout, self._sleep = token, chat_id, timeout, sleep
        self.session = session or requests.Session()

    def _post(self, method: str, timeout: float, **kwargs):
        """POST to the Bot API; retries timeouts, connection errors, 429 and 5xx (api.telegram.org often times out
        from CI runners). The URL contains the bot token, so errors never carry the request text."""
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        attempts = len(self.RETRY_DELAYS) + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                resp = self.session.post(url, timeout=timeout, **kwargs)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if last:
                    raise RuntimeError(f"Telegram request failed after {attempts} attempts: {type(exc).__name__}") from None
                log.warning("Telegram %s: %s, retry %d/%d", method, type(exc).__name__, attempt + 1, attempts - 1)
                self._sleep(self.RETRY_DELAYS[attempt])
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and not last:
                log.warning("Telegram %s: HTTP %s, retry %d/%d", method, resp.status_code, attempt + 1, attempts - 1)
                self._sleep(self.RETRY_DELAYS[attempt])
                continue
            return resp

    def send(self, text: str) -> None:
        resp = self._post("sendMessage", self.timeout,
                          data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": "true"})
        if resp.status_code != 200:
            # Never log the URL: it contains the bot token.
            raise RuntimeError(f"Telegram API error {resp.status_code}: {resp.text[:200]}")

    def send_document(self, filename: str, data: bytes, caption: str = "", mime: str = "text/html") -> None:
        resp = self._post("sendDocument", max(self.timeout, 60),
                          data={"chat_id": self.chat_id, "caption": caption[:1000]},
                          files={"document": (filename, data, mime)})
        if resp.status_code != 200:
            raise RuntimeError(f"Telegram API error {resp.status_code}: {resp.text[:200]}")

    def send_all(self, messages: Iterable[str]) -> None:
        for m in messages:
            self.send(m)
