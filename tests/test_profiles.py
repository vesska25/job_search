"""Second recipient ('sergey': junior Java): keyword filter, own dedup table, own chat."""
from argparse import Namespace

import pytest

import src.main as main_mod
from src.filters.profile import evaluate_keywords
from src.main import Stats, deliver_profile, run
from src.notifications.telegram import format_digest
from src.storage.database import JobDatabase
from src.utils.http import HttpClient
from tests.conftest import FIX, make_job
from tests.test_config_and_run import FakeSession


@pytest.fixture
def pcfg(settings):
    return settings["profiles"]["sergey"]


@pytest.mark.parametrize("title,ok", [
    ("Junior Java Developer (m/w/d)", True),
    ("Java Entwickler Berufseinsteiger (m/w/d)", True),
    ("Junior Software Engineer - Java / Spring Boot", True),
    ("Trainee Java Backend (m/w/d)", True),
    ("Senior Java Developer (m/w/d)", False),          # excluded
    ("Java Developer (m/w/d)", True),                   # level not stated: shown, flagged borderline
    ("Junior JavaScript Developer", False),            # JavaScript is not Java
    ("Junior Python Developer", False),
    ("Werkstudent Java Entwicklung", False),
    ("Junior Java Developer Lead", False),
])
def test_keyword_profile_titles(settings, pcfg, bank, title, ok):
    assert evaluate_keywords(make_job(bank, title), bank, settings, pcfg).accepted is ok


def test_unspecified_level_is_flagged_borderline(settings, pcfg, bank):
    plain = evaluate_keywords(make_job(bank, "Java Entwickler (m/w/d)"), bank, settings, pcfg)
    assert plain.accepted and plain.borderline and plain.functions[1] == "level not stated"
    junior = evaluate_keywords(make_job(bank, "Junior Java Entwickler (m/w/d)"), bank, settings, pcfg)
    assert junior.accepted and not junior.borderline
    strict = {**pcfg, "accept_unspecified_level": False}
    assert not evaluate_keywords(make_job(bank, "Java Entwickler (m/w/d)"), bank, settings, strict).accepted
    for title in ("Senior Java Entwickler", "Teamleiter Java Entwicklung", "Java Consultant (m/w/d)", "Werkstudent Java"):
        assert not evaluate_keywords(make_job(bank, title), bank, settings, pcfg).accepted, title


def test_level_word_in_description(settings, pcfg, bank):
    job = make_job(bank, "Java Entwickler (m/w/d)", description="Für Berufseinsteiger geeignet. Wir bieten ...")
    d = evaluate_keywords(job, bank, settings, pcfg)
    assert d.accepted and not d.borderline


def test_keyword_profile_needs_germany(settings, pcfg, bank):
    assert not evaluate_keywords(make_job(bank, "Junior Java Developer", location="London"), bank, settings, pcfg).accepted
    assert evaluate_keywords(make_job(bank, "Junior Java Developer", location="Köln"), bank, settings, pcfg).accepted


def test_profile_tables_are_independent(tmp_path):
    main_db = JobDatabase(tmp_path / "t.db")
    sergey = main_db.for_profile("sergey")
    assert main_db.for_profile("main") is main_db
    job = make_job(__import__("src.config", fromlist=["Bank"]).Bank(id="b", name="B"), "Junior Java Developer")
    main_db.record(job)
    main_db.mark_notified([job])
    assert not main_db.needs_notification(job)
    assert sergey.needs_notification(job)            # Sergey has not received it yet
    sergey.record(job)
    sergey.mark_notified([job])
    assert not sergey.needs_notification(job)
    assert JobDatabase(tmp_path / "t.db", "jobs_sergey").count() == 1   # persisted in the same file
    with pytest.raises(ValueError):
        JobDatabase(":memory:", "jobs; DROP TABLE x")


def test_digest_title_and_empty_text():
    assert format_digest([], title="X", empty_text="nothing") == ["nothing"]
    msg = format_digest([make_job(__import__("src.config", fromlist=["Bank"]).Bank(id="b", name="B"), "Junior Java")],
                        title="JUNIOR JAVA - BANK JOBS")[0]
    assert msg.startswith("<b>JUNIOR JAVA - BANK JOBS</b>")


def test_run_evaluates_extra_profile_on_same_scrape(settings, tmp_path):
    from src.config import Bank
    bank = Bank(id="good", name="Good Bank", jobs_url="https://acme.jobs.personio.de/", source_type="personio",
                enabled=True, germany_only=True)
    session = FakeSession({"acme.jobs.personio.de/xml": (FIX / "personio.xml").read_text(encoding="utf-8")})
    http = HttpClient(session=session, min_delay=0)
    profiles = {"x": {"tech": ["Regulatory"], "level": ["Head"], "exclude": ["Werkstudent"]}}
    extra: dict = {}
    main_matches = run([bank], settings, http, JobDatabase(":memory:"), None, Stats(), extra=extra, profiles=profiles)
    assert [m.title for m in main_matches] == ["Head of Regulatory Reporting (m/w/d)"]
    assert [m.title for m in extra["x"]] == ["Head of Regulatory Reporting (m/w/d)"]
    assert extra["x"][0] is not main_matches[0]            # separate copy, own matched_functions
    assert extra["x"][0].matched_functions == ["Regulatory", "Head"]


class FakeNotifier:
    sent: list = []

    def __init__(self, token, chat_id, *a, **kw):
        self.chat_id = chat_id

    def send_all(self, messages):
        FakeNotifier.sent.extend((self.chat_id, m) for m in messages)


def test_deliver_profile_sends_to_own_chat_once(monkeypatch, settings, tmp_path, pcfg):
    from src.config import Bank
    monkeypatch.setattr(main_mod, "TelegramNotifier", FakeNotifier)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_SERGEY", "999")
    FakeNotifier.sent = []
    db = JobDatabase(tmp_path / "t.db")
    jobs = [make_job(Bank(id="b", name="B Bank"), "Junior Java Developer (m/w/d)", url="https://x.example/1")]
    args = Namespace(dry_run=False, db=None, baseline=False)
    assert deliver_profile("sergey", pcfg, jobs, db, args, settings["telegram"]) == 0
    assert FakeNotifier.sent[0][0] == "999" and "JUNIOR JAVA - BANK JOBS" in FakeNotifier.sent[0][1]
    assert "Junior Java Developer" in FakeNotifier.sent[0][1]
    n = len(FakeNotifier.sent)
    deliver_profile("sergey", pcfg, jobs, db, args, settings["telegram"])      # second run: nothing new
    assert "No new junior Java vacancies" in FakeNotifier.sent[n][1]
    assert db.count() == 0                                                     # main table untouched


def test_deliver_profile_without_chat_id_sends_nothing(monkeypatch, settings, tmp_path, pcfg):
    monkeypatch.setattr(main_mod, "TelegramNotifier", FakeNotifier)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_SERGEY", raising=False)
    FakeNotifier.sent = []
    args = Namespace(dry_run=False, db=None, baseline=False)
    assert deliver_profile("sergey", pcfg, [], JobDatabase(tmp_path / "t.db"), args, settings["telegram"]) == 0
    assert FakeNotifier.sent == []


def test_cli_unknown_profile(tmp_path):
    with pytest.raises(SystemExit):
        main_mod.main(["--dry-run", "--profile", "nobody"])


def test_digest_messages_never_exceed_3000_chars():
    from src.config import Bank
    b = Bank(id="b", name="A Bank")
    jobs = [make_job(b, f"Junior Java Developer {i} (m/w/d) " + "x" * 120, url=f"https://x.example/{i}" + "y" * 80)
            for i in range(60)]
    msgs = format_digest(jobs, max_jobs=60)
    assert len(msgs) > 1 and all(len(m) <= 3000 for m in msgs)


def test_resend_sends_already_known_matches(monkeypatch, settings, tmp_path, pcfg):
    from src.config import Bank
    monkeypatch.setattr(main_mod, "TelegramNotifier", FakeNotifier)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_SERGEY", "999")
    FakeNotifier.sent = []
    db = JobDatabase(tmp_path / "t.db")
    jobs = [make_job(Bank(id="b", name="B Bank"), "Junior Java Developer (m/w/d)", url="https://x.example/1")]
    base = Namespace(dry_run=False, db=None, baseline=True, resend=False)
    deliver_profile("sergey", pcfg, jobs, db, base, settings["telegram"])        # stored silently
    assert FakeNotifier.sent == []
    args = Namespace(dry_run=False, db=None, baseline=False, resend=False)
    deliver_profile("sergey", pcfg, jobs, db, args, settings["telegram"])        # nothing new
    assert "No new junior Java" in FakeNotifier.sent[-1][1]
    args.resend = True
    deliver_profile("sergey", pcfg, jobs, db, args, settings["telegram"])        # explicit resend
    assert "Junior Java Developer" in FakeNotifier.sent[-1][1]
