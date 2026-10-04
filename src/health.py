"""Source health: notice scrapers that silently stop working (a bank changed its career page).

For every source the run stores how many vacancies it found. Compared with the last healthy run, a source is flagged when it
  - raised an error (blocked, 404, layout parser crashed),
  - found nothing although it normally finds >= EMPTY_MIN_BASELINE vacancies ("empty"),
  - found less than DROP_RATIO of its usual count, usual >= DROP_MIN_BASELINE ("drop").
A flagged source keeps its old baseline, so it stays flagged every run until it recovers (or is fixed/disabled).
The table lives in the same SQLite file as the seen-jobs store, so the workflow's "Persist database" step commits it.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

EMPTY_MIN_BASELINE = 3
DROP_MIN_BASELINE = 10
DROP_RATIO = 0.3
BASELINE_DECAY = 0.8     # baseline = max(count, baseline * decay): a slow shrink is normal, a collapse is not

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_health (
    bank_id     TEXT PRIMARY KEY,
    baseline    INTEGER NOT NULL DEFAULT 0,
    last_count  INTEGER,
    issue_kind  TEXT,
    issue_since TEXT,
    last_error  TEXT,
    updated     TEXT
);
"""


@dataclass
class Issue:
    bank_id: str
    label: str
    kind: str          # error | empty | drop
    detail: str
    since: str         # date the problem was first seen

    def line(self) -> str:
        return f"{self.label}: {self.detail} (since {self.since})"


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def assess(prev: dict | None, count: int | None, error: str | None, today: str | None = None):
    """Pure decision. Returns (new_row, kind, detail); kind is None for a healthy source."""
    today = today or _today()
    prev = prev or {}
    baseline = int(prev.get("baseline") or 0)
    kind = detail = None
    if error:
        kind, detail = "error", error[:160]
    elif count == 0 and baseline >= EMPTY_MIN_BASELINE:
        kind, detail = "empty", f"found 0 vacancies, usually ~{baseline}"
    elif baseline >= DROP_MIN_BASELINE and count is not None and count < baseline * DROP_RATIO:
        kind, detail = "drop", f"found {count} vacancies, usually ~{baseline}"
    elif count is not None:
        baseline = max(count, int(baseline * BASELINE_DECAY))
    since = (prev.get("issue_since") or today) if kind and prev.get("issue_kind") else today
    row = {"baseline": baseline, "last_count": count, "issue_kind": kind,
           "issue_since": since if kind else None, "last_error": error[:160] if error else None}
    return row, kind, detail


class HealthStore:
    """Loads the stored rows once, records this run's results in memory, persists them on save() (only if writable).
    A dry run reads the real database (if it exists) so it can show what would be flagged, but never writes."""

    def __init__(self, path, writable: bool = True):
        self.path, self.writable = path, writable
        self.rows: dict[str, dict] = {}
        self.touched: set[str] = set()
        self.issues: list[Issue] = []
        if str(path) == ":memory:" or not Path(path).exists():
            return
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            self.rows = {r["bank_id"]: dict(r) for r in conn.execute("SELECT * FROM source_health")}
            conn.close()
        except sqlite3.Error:
            self.rows = {}        # table not created yet (first run with this feature)

    def record(self, bank_id: str, label: str, count: int | None = None, error: str | None = None) -> Issue | None:
        row, kind, detail = assess(self.rows.get(bank_id), count, error)
        self.rows[bank_id] = row
        self.touched.add(bank_id)
        if not kind:
            return None
        issue = Issue(bank_id, label, kind, detail, row["issue_since"])
        self.issues.append(issue)
        return issue

    def save(self) -> None:
        if not self.writable or not self.touched or str(self.path) == ":memory:":
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.executescript(SCHEMA)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for bid in self.touched:
            r = self.rows[bid]
            conn.execute(
                "INSERT INTO source_health (bank_id, baseline, last_count, issue_kind, issue_since, last_error, updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(bank_id) DO UPDATE SET baseline=excluded.baseline, "
                "last_count=excluded.last_count, issue_kind=excluded.issue_kind, issue_since=excluded.issue_since, "
                "last_error=excluded.last_error, updated=excluded.updated",
                (bid, r["baseline"], r["last_count"], r["issue_kind"], r["issue_since"], r["last_error"], now))
        conn.commit()
        conn.close()
