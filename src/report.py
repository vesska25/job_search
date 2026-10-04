"""Weekly source-health report as one self-contained HTML file: which sources are broken, which are switched off and why,
and the numbers for all working ones. Built from banks.yaml plus the stored per-source health rows (src/health.py)."""
from __future__ import annotations

import html
from datetime import date

KIND_TEXT = {
    "error": "Error",
    "empty": "Found nothing",
    "drop": "Sharp drop",
}
STATUS_TEXT = {
    "js_list": "List is loaded by JavaScript (no static data)",
    "js_widget": "Embedded JavaScript widget",
    "blocked_robots": "robots.txt does not allow it",
    "blocked_by_site": "Site blocks automated access",
    "needs_review": "Needs a manual look",
    "no_vacancies_now": "No vacancies at the moment",
    "unknown": "No official source found",
}

CSS = """
:root{--bg:#fff;--fg:#1d2433;--muted:#667085;--line:#e4e7ec;--card:#f7f8fa;--bad:#b42318;--badbg:#fef3f2;--warn:#b54708;--warnbg:#fffaeb;--ok:#067647;--okbg:#ecfdf3}
@media (prefers-color-scheme:dark){:root{--bg:#14171f;--fg:#e6e9f0;--muted:#98a2b3;--line:#2b3140;--card:#1c202b;--bad:#fda29b;--badbg:#3a1a18;--warn:#fec84b;--warnbg:#3a2a0c;--ok:#75e0a7;--okbg:#0e2f1f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.muted{color:var(--muted)}.kpis{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:130px}
.kpi b{display:block;font-size:22px}.kpi.bad b{color:var(--bad)}.kpi.ok b{color:var(--ok)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}
.tag{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600}.tag.bad{background:var(--badbg);color:var(--bad)}.tag.warn{background:var(--warnbg);color:var(--warn)}.tag.ok{background:var(--okbg);color:var(--ok)}
.num{text-align:right;white-space:nowrap}details{margin-top:8px}summary{cursor:pointer;font-weight:600;padding:6px 0}
.wrap{overflow-x:auto}a{color:inherit}
"""


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _short(note: str, n: int = 220) -> str:
    note = " ".join((note or "").split())
    return note if len(note) <= n else note[: n - 1].rstrip() + "…"


def classify(banks: list, rows: dict) -> dict:
    """Group banks: problems / working / unmeasured (enabled) and disabled (by verification_status)."""
    out = {"problems": [], "working": [], "unmeasured": [], "disabled": []}
    for b in banks:
        r = rows.get(b.id)
        if not b.enabled:
            if b.alias_of:
                continue
            out["disabled"].append((b, r))
        elif r and r.get("issue_kind"):
            out["problems"].append((b, r))
        elif r:
            out["working"].append((b, r))
        else:
            out["unmeasured"].append((b, r))
    return out


def _link(b) -> str:
    name = _e(b.label)
    return f'<a href="{_e(b.jobs_url)}">{name}</a>' if b.jobs_url.startswith("http") else name


def render_html(banks: list, rows: dict, today: date | None = None, run_summary: str = "") -> str:
    today = today or date.today()
    g = classify(banks, rows)
    total = sum((r.get("last_count") or 0) for _, r in g["working"] + g["problems"])
    bad = len(g["problems"])
    kpis = [
        ("kpi " + ("bad" if bad else "ok"), bad, "sources need attention"),
        ("kpi ok", len(g["working"]), "working"),
        ("kpi", len(g["unmeasured"]), "enabled, not measured yet"),
        ("kpi", len(g["disabled"]), "switched off / not connected"),
        ("kpi", f"{total:,}", "vacancies seen at the sources"),
    ]
    parts = [f"<h1>Bank job monitor: source health</h1><div class='muted'>Week of {today.isoformat()}</div>",
             "<div class='kpis'>" + "".join(f"<div class='{c}'><b>{_e(v)}</b>{_e(t)}</div>" for c, v, t in kpis) + "</div>"]

    parts.append("<h2>Needs attention</h2>")
    if g["problems"]:
        rows_html = []
        for b, r in sorted(g["problems"], key=lambda x: (x[1]["issue_kind"] != "error", x[0].label.lower())):
            kind = r["issue_kind"]
            detail = r.get("last_error") if kind == "error" else (
                f"found {r.get('last_count')}, usually ~{r.get('baseline')}")
            rows_html.append(
                f"<tr><td>{_link(b)}<div class='muted'>{_e(b.id)} · {_e(b.source_type)}</div></td>"
                f"<td><span class='tag {'bad' if kind == 'error' else 'warn'}'>{_e(KIND_TEXT.get(kind, kind))}</span></td>"
                f"<td>{_e(detail)}</td><td class='num'>{_e(r.get('issue_since'))}</td></tr>")
        parts.append("<div class='wrap'><table><tr><th>Source</th><th>Problem</th><th>Details</th><th>Since</th></tr>"
                     + "".join(rows_html) + "</table></div>")
    else:
        parts.append("<p><span class='tag ok'>All measured sources work</span></p>")

    if g["unmeasured"]:
        parts.append("<h2>Enabled but not measured yet</h2><p class='muted'>No stored result: the source was added after the last "
                     "full run. It is measured on the next run.</p><p>" + ", ".join(_link(b) for b, _ in g["unmeasured"]) + "</p>")

    by_status: dict[str, list] = {}
    for b, _ in g["disabled"]:
        by_status.setdefault(b.verification_status or "unknown", []).append(b)
    parts.append(f"<h2>Switched off or not connected ({len(g['disabled'])})</h2>"
                 "<p class='muted'>Known limitations, not new breakages. Reason is taken from the notes in banks.yaml.</p>")
    for status, items in sorted(by_status.items(), key=lambda kv: -len(kv[1])):
        trs = "".join(f"<tr><td>{_link(b)}</td><td class='muted'>{_e(_short(b.notes))}</td></tr>"
                      for b in sorted(items, key=lambda x: x.label.lower()))
        parts.append(f"<details><summary>{_e(STATUS_TEXT.get(status, status))} ({len(items)})</summary>"
                     f"<div class='wrap'><table>{trs}</table></div></details>")

    trs = "".join(
        f"<tr><td>{_link(b)}</td><td>{_e(b.source_type)}</td><td class='num'>{_e(r.get('last_count'))}</td>"
        f"<td class='num'>{_e(r.get('baseline'))}</td><td class='num muted'>{_e((r.get('updated') or '')[:10])}</td></tr>"
        for b, r in sorted(g["working"], key=lambda x: x[0].label.lower()))
    parts.append(f"<h2>Working sources ({len(g['working'])})</h2><details><summary>Show the table</summary><div class='wrap'><table>"
                 "<tr><th>Source</th><th>Adapter</th><th class='num'>Found</th><th class='num'>Usual</th><th class='num'>Measured</th></tr>"
                 f"{trs}</table></div></details>")
    if run_summary:
        parts.append(f"<h2>Run</h2><pre class='muted'>{_e(run_summary)}</pre>")
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Source health {today.isoformat()}</title><style>{CSS}</style></head><body><main>{''.join(parts)}</main></body></html>")
