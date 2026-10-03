"""Telegram delivery and message formatting (HTML parse mode)."""
from __future__ import annotations

import html
from datetime import date, timedelta
from typing import Iterable

import requests

from src.utils.logging import get_logger

log = get_logger("telegram")
MAX_LEN = 4000  # Telegram limit is 4096; keep a margin
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


def format_digest(jobs: list, today: date | None = None, max_jobs: int = 40, failed_sources: int = 0) -> list[str]:
    """Return one or more messages (split below Telegram's length limit)."""
    if not jobs:
        return [EMPTY_TEXT]
    header = f"<b>BANK JOB MONITOR</b>\nWeek of {week_of(today)}\nNew matching vacancies: {len(jobs)}\n"
    blocks = []
    for i, j in enumerate(jobs[:max_jobs], 1):
        flag = " (borderline)" if j.borderline else ""
        blocks.append(f"\n{i}. <b>{_e(j.bank_name)}</b> — {_e(j.title)}{flag}\n{_e(j.location or 'Germany')}\n{_e(j.url)}\n")
    if len(jobs) > max_jobs:
        blocks.append(f"\n… and {len(jobs) - max_jobs} more (see the run log).\n")
    if failed_sources:
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
    return messages


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, session=None, timeout: float = 20):
        if not token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        self.token, self.chat_id, self.timeout = token, chat_id, timeout
        self.session = session or requests.Session()

    def send(self, text: str) -> None:
        resp = self.session.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage", timeout=self.timeout,
            data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": "true"},
        )
        if resp.status_code != 200:
            # Never log the URL: it contains the bot token.
            raise RuntimeError(f"Telegram API error {resp.status_code}: {resp.text[:200]}")

    def send_all(self, messages: Iterable[str]) -> None:
        for m in messages:
            self.send(m)
