from datetime import date

import pytest

from src.notifications.telegram import EMPTY_TEXT, MAX_LEN, TelegramNotifier, format_digest, format_job, week_of
from tests.conftest import make_job


class FakeSession:
    def __init__(self, status=200):
        self.sent, self.status = [], status

    def post(self, url, **kw):
        self.sent.append((url, kw))
        return type("R", (), {"status_code": self.status, "text": "err"})()


def test_individual_format(bank):
    j = make_job(bank, "Head of Regulatory Reporting", location="Frankfurt", url="https://x.example/1")
    j.matched_functions = ["Regulatory Reporting", "Finance"]
    msg = format_job(j)
    for part in ("🏦 Bank: Test Bank", "📌 Head of Regulatory Reporting", "📍 Frankfurt",
                 "🔎 Relevant areas: Regulatory Reporting / Finance", "https://x.example/1", "Source: Test Bank"):
        assert part in msg


def test_digest_format_and_week(bank):
    jobs = [make_job(bank, f"Head of Risk {i}", url=f"https://x.example/{i}") for i in range(4)]
    msgs = format_digest(jobs, today=date(2026, 10, 7))   # Wednesday -> Monday 2026-10-05
    assert len(msgs) == 1
    assert "Week of 2026-10-05" in msgs[0] and "New matching vacancies: 4" in msgs[0]
    assert "1. <b>Test Bank</b> — Head of Risk 0" in msgs[0]
    assert week_of(date(2026, 10, 5)) == "2026-10-05"


def test_empty_digest():
    assert format_digest([]) == [EMPTY_TEXT]


def test_html_is_escaped(bank):
    j = make_job(bank, "Head of Risk <script> & Co", url="https://x.example/1?a=1&b=2")
    assert "<script>" not in format_job(j) and "&amp;" in format_job(j)


def test_long_digest_split(bank):
    jobs = [make_job(bank, "Head of Risk " + "x" * 150, url=f"https://x.example/{i}") for i in range(40)]
    msgs = format_digest(jobs, max_jobs=40)
    assert len(msgs) > 1 and all(len(m) <= MAX_LEN for m in msgs)


def test_sender_uses_token_without_leaking_in_errors():
    s = FakeSession()
    TelegramNotifier("TOKEN123", "42", session=s).send("hi")
    url, kw = s.sent[0]
    assert "botTOKEN123/sendMessage" in url and kw["data"]["chat_id"] == "42"
    with pytest.raises(RuntimeError) as e:
        TelegramNotifier("TOKEN123", "42", session=FakeSession(400)).send("hi")
    assert "TOKEN123" not in str(e.value)


def test_missing_credentials():
    with pytest.raises(ValueError):
        TelegramNotifier("", "")
