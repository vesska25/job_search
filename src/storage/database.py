"""SQLite store for already-seen matching vacancies."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS {t} (
    job_id            TEXT PRIMARY KEY,
    canonical_url     TEXT NOT NULL,
    bank_id           TEXT NOT NULL,
    title             TEXT NOT NULL,
    location          TEXT,
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    notified          INTEGER NOT NULL DEFAULT 0,
    notification_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_{t}_url ON {t}(canonical_url);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobDatabase:
    """Seen-jobs store. The main profile uses table 'jobs'; every additional profile has its own table
    (jobs_<name>) in the same file, so each recipient gets each vacancy exactly once."""

    def __init__(self, path, table: str = "jobs"):
        if not table.replace("_", "").isalnum():
            raise ValueError(f"invalid table name {table!r}")
        self.path, self.table = path, table
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA.format(t=table))

    def for_profile(self, name: str) -> "JobDatabase":
        return self if name == "main" else JobDatabase(self.path, f"jobs_{name}")

    def close(self):
        self.conn.close()

    def find(self, job):
        """A job is known if its stable ID OR its canonical URL is already stored."""
        return self.conn.execute(
            f"SELECT * FROM {self.table} WHERE job_id = ? OR (canonical_url = ? AND canonical_url != '') LIMIT 1",
            (job.job_id, job.canonical_url),
        ).fetchone()

    def needs_notification(self, job) -> bool:
        """New job, or known but a previous Telegram delivery failed (notified = 0)."""
        row = self.find(job)
        return row is None or not row["notified"]

    def record(self, job) -> bool:
        """Insert new job or refresh last_seen. Returns True if the job was new."""
        row = self.find(job)
        now = _now()
        if row:
            self.conn.execute(f"UPDATE {self.table} SET last_seen = ? WHERE job_id = ?", (now, row["job_id"]))
            self.conn.commit()
            return False
        self.conn.execute(
            f"INSERT INTO {self.table} (job_id, canonical_url, bank_id, title, location, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job.job_id, job.canonical_url, job.bank_id, job.title, job.location, now, now),
        )
        self.conn.commit()
        return True

    def mark_notified(self, jobs) -> None:
        now = _now()
        for job in jobs:
            row = self.find(job)
            if row:
                self.conn.execute(
                    f"UPDATE {self.table} SET notified = 1, notification_date = ? WHERE job_id = ?", (now, row["job_id"]))
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0]
