from datetime import date

from src.health import HealthStore, assess
from src.notifications.telegram import format_digest


def test_assess_healthy_tracks_baseline_with_decay():
    row, kind, _ = assess({"baseline": 50}, 45, None)
    assert kind is None and row["baseline"] == 45
    row, kind, _ = assess({"baseline": 50}, 30, None)      # shrinks within ratio: healthy, baseline decays slowly
    assert kind is None and row["baseline"] == 40


def test_assess_flags_empty_drop_and_error():
    assert assess({"baseline": 12}, 0, None)[1] == "empty"
    assert assess({"baseline": 40}, 5, None)[1] == "drop"
    assert assess({"baseline": 40}, 20, None)[1] is None
    row, kind, detail = assess({"baseline": 12}, None, "HTTPError: 404 for url")
    assert kind == "error" and "404" in detail and row["baseline"] == 12


def test_assess_small_or_new_sources_are_not_flagged():
    assert assess(None, 0, None)[1] is None                # never found anything: nothing to compare with
    assert assess({"baseline": 2}, 0, None)[1] is None     # a handful of vacancies may all close
    assert assess({"baseline": 9}, 2, None)[1] is None


def test_flag_persists_and_keeps_baseline_and_since_date():
    first, kind, _ = assess({"baseline": 20}, 0, None, today="2026-10-04")
    assert kind == "empty" and first["issue_since"] == "2026-10-04" and first["baseline"] == 20
    again, kind, _ = assess(first, 0, None, today="2026-10-11")
    assert kind == "empty" and again["issue_since"] == "2026-10-04"      # still broken since the first day
    ok, kind, _ = assess(again, 18, None, today="2026-10-18")
    assert kind is None and ok["issue_kind"] is None and ok["issue_since"] is None


def test_store_roundtrip_and_dry_run_never_writes(tmp_path):
    db = tmp_path / "jobs.db"
    learn = HealthStore(db)
    learn.record("a", "Bank A", count=30)
    learn.save()
    nxt = HealthStore(db)
    issue = nxt.record("a", "Bank A", count=0)
    assert issue and issue.kind == "empty"
    nxt.save()
    assert HealthStore(db).rows["a"]["issue_kind"] == "empty"
    dry = HealthStore(db, writable=False)
    dry.record("a", "Bank A", count=40)
    dry.save()                                             # no write
    assert HealthStore(db).rows["a"]["issue_kind"] == "empty"


def test_digest_shows_health_block_also_without_new_jobs():
    from src.health import Issue
    issues = [Issue("a", "Bank A", "empty", "found 0 vacancies, usually ~30", "2026-10-04")]
    msgs = format_digest([], today=date(2026, 10, 9), health_issues=issues)
    assert "Sources need attention" in msgs[0] and "Bank A" in msgs[0]
    assert "Sources need attention" not in format_digest([], today=date(2026, 10, 9))[0]


def test_report_html_lists_problems_disabled_and_working():
    from src.config import Bank
    from src.report import render_html
    banks = [Bank(id="a", name="Bank A", enabled=True, jobs_url="https://a.test/jobs", source_type="custom_html"),
             Bank(id="b", name="Bank <B>", enabled=True, source_type="personio"),
             Bank(id="c", name="Bank C", enabled=False, verification_status="js_list", notes="List is rendered by JS."),
             Bank(id="d", name="Bank D", enabled=True)]
    rows = {"a": {"baseline": 30, "last_count": 0, "issue_kind": "empty", "issue_since": "2026-10-04", "last_error": None},
            "b": {"baseline": 12, "last_count": 12, "issue_kind": None, "issue_since": None, "last_error": None}}
    out = render_html(banks, rows, today=date(2026, 10, 9))
    assert "Needs attention" in out and "Found nothing" in out and "usually ~30" in out and "2026-10-04" in out
    assert "List is loaded by JavaScript" in out and "Bank C" in out          # disabled, with the reason
    assert "Bank &lt;B&gt;" in out and "Bank <B>" not in out                    # escaped
    assert "Enabled but not measured yet" in out and "Bank D" in out


def test_previous_run_prefers_a_week_old_row_and_falls_back(tmp_path):
    from datetime import datetime, timedelta, timezone
    import sqlite3
    from src.health import SCHEMA
    db = tmp_path / "jobs.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    now = datetime(2026, 10, 9, 15, tzinfo=timezone.utc)
    for days, found in ((14, 40), (7, 38), (1, 10)):
        conn.execute("INSERT INTO source_runs VALUES (?, ?, ?, ?, ?, NULL)",
                     ("a", (now - timedelta(days=days)).isoformat(timespec="seconds"), found, 30, 2))
    conn.commit()
    conn.close()
    h = HealthStore(db)
    assert h.previous("a", now)["found"] == 38              # a week ago, not yesterday's ad-hoc run
    assert h.previous("zzz", now) is None


def test_report_per_bank_table_shows_now_before_and_relevant():
    from src.config import Bank
    from src.report import render_html
    banks = [Bank(id="a", name="Bank A", enabled=True, jobs_url="https://a.test/jobs")]
    rows = {"a": {"baseline": 30, "last_count": 28, "issue_kind": None, "issue_since": None, "last_error": None}}

    class H:
        current = {"a": {"found": 28, "germany": 20, "relevant": 2, "error": None}}

        def previous(self, bank_id, now=None):
            return {"run_at": "2026-10-02T15:00:00+00:00", "found": 31, "germany": 22, "relevant": 3}

    out = render_html(banks, rows, today=date(2026, 10, 9), health=H(),
                      matches_by_bank={"a": [("Head of Risk", "https://a.test/1", "Frankfurt")]})
    assert "All enabled sources (1)" in out and ">28<" in out and ">31<" in out and ">-3<" in out and ">20<" in out
    assert "title='2026-10-02'" in out
    assert "Relevant vacancies by source (1)" in out and "Head of Risk" in out
