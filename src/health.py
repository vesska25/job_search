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
from datetime import datetime, timedelta, timezone
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
CREATE TABLE IF NOT EXISTS source_runs (
    bank_id  TEXT NOT NULL,
    run_at   TEXT NOT NULL,
    found    INTEGER,
    germany  INTEGER,
    relevant INTEGER,
    error    TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_runs ON source_runs(bank_id, run_at);
"""
WEEK_AGO_MIN_DAYS = 5     # "a week ago" = the newest stored run that is at least this old (fallback: the previous run)


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
        self.history: dict[str, list[dict]] = {}
        self.current: dict[str, dict] = {}       # this run: found / germany / relevant / error per source
        self.touched: set[str] = set()
        self.issues: list[Issue] = []
        if str(path) != ":memory:" and Path(path).exists():
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                self.rows = {r["bank_id"]: dict(r) for r in conn.execute("SELECT * FROM source_health")}
            except sqlite3.Error:
                self.rows = {}        # table not created yet (first run with this feature)
            try:
                for r in conn.execute("SELECT * FROM source_runs ORDER BY run_at"):
                    self.history.setdefault(r["bank_id"], []).append(dict(r))
            except sqlite3.Error:
                self.history = {}
            conn.close()
        self.initial = {k: dict(v) for k, v in self.rows.items()}   # state before this run: the fallback for "last time"

    def previous(self, bank_id: str, now: datetime | None = None) -> dict | None:
        """The run to compare with: newest stored run >= 5 days old, else the latest stored run, else the count the
        health table kept before this run. Returns {run_at, found, germany, relevant} or None."""
        runs = self.history.get(bank_id) or []
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=WEEK_AGO_MIN_DAYS)).isoformat(timespec="seconds")
        older = [r for r in runs if r["run_at"] <= cutoff and r.get("error") is None]
        pick = older[-1] if older else next((r for r in reversed(runs) if r.get("error") is None), None)
        if pick:
            return pick
        init = self.initial.get(bank_id)
        if init and init.get("last_count") is not None:
            return {"run_at": init.get("updated"), "found": init["last_count"], "germany": None, "relevant": None}
        return None

    def record(self, bank_id: str, label: str, count: int | None = None, error: str | None = None,
               germany: int | None = None, relevant: int | None = None) -> Issue | None:
        row, kind, detail = assess(self.rows.get(bank_id), count, error)
        self.rows[bank_id] = row
        self.touched.add(bank_id)
        self.current[bank_id] = {"found": count, "germany": germany, "relevant": relevant, "error": error}
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
            c = self.current[bid]
            conn.execute("INSERT INTO source_runs (bank_id, run_at, found, germany, relevant, error) VALUES (?, ?, ?, ?, ?, ?)",
                         (bid, now, c["found"], c["germany"], c["relevant"], (c["error"] or None) and c["error"][:160]))
        conn.commit()
        conn.close()
