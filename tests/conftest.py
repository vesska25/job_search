import json
from pathlib import Path

import pytest

from src.config import Bank, load_settings

FIX = Path(__file__).parent / "fixtures"


def fixture_text(name):
    return (FIX / name).read_text(encoding="utf-8")


def fixture_json(name):
    return json.loads(fixture_text(name))


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture
def bank():
    return Bank(id="testbank", name="Test Bank AG", display_name="Test Bank", jobs_url="https://careers.example.bank/jobs",
                source_type="custom_html", enabled=True)


@pytest.fixture
def de_bank(bank):
    bank.germany_only = True
    return bank


def make_job(bank, title, location="Frankfurt", description="", url=None, **kw):
    from src.models.job import Job
    return Job(bank_id=bank.id, bank_name=bank.label, title=title, url=url or f"https://x.example/{abs(hash(title))}",
               location=location, description=description, **kw)
