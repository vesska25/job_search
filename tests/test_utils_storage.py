import pytest

from src.models.job import Job
from src.storage.database import JobDatabase
from src.utils.normalization import canonicalize_url


@pytest.mark.parametrize("raw,expected", [
    ("https://Example.com/jobs/1/?utm_source=a&utm_medium=b#frag", "https://example.com/jobs/1"),
    ("http://example.com/jobs/1", "https://example.com/jobs/1"),
    ("https://example.com/job?id=5&gclid=zzz&ref=li", "https://example.com/job?id=5"),
    ("https://example.com/job?b=2&a=1", "https://example.com/job?a=1&b=2"),
    ("https://example.com//jobs//1/", "https://example.com/jobs/1"),
    ("https://example.com/jobs/1;jsessionid=ABC123?x=1", "https://example.com/jobs/1?x=1"),
    ("https://example.com:443/x", "https://example.com/x"),
    ("", ""),
])
def test_canonicalize(raw, expected):
    assert canonicalize_url(raw) == expected


def J(url, sid=None, title="Head of Risk"):
    return Job(bank_id="b", bank_name="B", title=title, url=url, location="Frankfurt", source_job_id=sid)


def test_job_id_stable_and_deterministic():
    assert J("https://x.com/j/1?utm_source=a").job_id == J("https://x.com/j/1/").job_id
    assert J("https://x.com/j/1", sid="42").job_id == "b:42"
    assert J("https://x.com/j/1").job_id != J("https://x.com/j/2").job_id


def test_dedup_by_id_or_url():
    db = JobDatabase(":memory:")
    assert db.record(J("https://x.com/j/1", sid="42")) is True
    assert db.record(J("https://x.com/j/1?utm_campaign=z", sid="99")) is False   # same canonical URL, other ID
    assert db.record(J("https://x.com/other", sid="42")) is False                # same ID, other URL
    assert db.record(J("https://x.com/j/2", sid="43")) is True
    assert db.count() == 2


def test_notification_state_and_retry():
    db = JobDatabase(":memory:")
    j = J("https://x.com/j/1", sid="1")
    assert db.needs_notification(j)
    db.record(j)
    assert db.needs_notification(j)          # recorded but delivery not confirmed -> retry next run
    db.mark_notified([j])
    assert not db.needs_notification(j)
    row = db.find(j)
    assert row["notified"] == 1 and row["notification_date"] and row["first_seen"] and row["last_seen"]


def test_last_seen_updates():
    db = JobDatabase(":memory:")
    j = J("https://x.com/j/1", sid="1")
    db.record(j)
    db.conn.execute("UPDATE jobs SET last_seen = '2000-01-01T00:00:00+00:00'")
    db.record(j)
    assert db.find(j)["last_seen"] > "2000"


def test_notified_before_finds_jobs_a_baseline_run_marked_as_sent(tmp_path):
    from src.models.job import Job
    from src.storage.database import JobDatabase
    db = JobDatabase(tmp_path / "j.db")
    old = Job(bank_id="b", bank_name="B", title="Head of Risk", url="https://x.test/1", location="Frankfurt")
    db.record(old)
    db.mark_notified([old])
    db.conn.execute("UPDATE jobs SET notification_date = '2026-10-03T16:26:30+00:00'")
    db.conn.commit()
    fresh = Job(bank_id="b", bank_name="B", title="Head of Audit", url="https://x.test/2", location="Frankfurt")
    db.record(fresh)
    db.mark_notified([fresh])
    assert db.notified_before(old, "2026-10-04") and not db.notified_before(fresh, "2026-10-04")
    assert not db.notified_before(Job(bank_id="b", bank_name="B", title="New", url="https://x.test/3"), "2026-10-04")
