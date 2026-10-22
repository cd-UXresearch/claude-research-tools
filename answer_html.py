"""Build the HTML page for an ask_research.py conversation.

Uses the same look as the findings reports (styles come from html_report.py).
Like the reports, it's one self-contained file that works offline.
"""

import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from html_report import CSS, esc, support_dots
from research_library import CITATION_OK

COVERAGE = {
    "covered": ("ok", "✓ Covered by the research"),
    "partly_covered": ("warn", "◐ Partly covered"),
    "not_covered": ("gap", "○ Not covered by the research"),
}

QUOTE_PROBLEMS = {
    "finding_not_found": "⚠ Finding doesn't exist",
    "quote_not_in_finding": "✕ Not in the report",
    "wrong_participant": "⚠ Wrong participant",
}


def report_link(study, page_dir: Path) -> str:
    """A link from the conversation page to a study's report, relative so it works anywhere."""
    return Path(os.path.relpath(study.report_html.resolve(), page_dir.resolve())).as_posix()


def source_chip(finding_id: str, ok: bool, library, page_dir: Path) -> str:
    if not ok:
        return (
            f'<span class="source bad" title="This finding isn\'t in any report">'
            f"⚠ {esc(finding_id)} · not in the reports</span>"
        )
    study, f = library.findings[finding_id]
    n, total = len(f["supporting_participants"]), len(study.transcripts)
    link = f"{report_link(study, page_dir)}#finding-{f['number']}"
    single = ' <span class="badge warn sm">Single source</span>' if n == 1 else ""
    return f"""
          <a class="source" href="{esc(link)}" title="Open this finding in its report">
            <span class="src-study">{esc(study.name)} · F{f['number']:02d}</span>
            <span class="src-head">{esc(f['headline'])}</span>
            <span class="src-support">{support_dots(n, total)} {n} of {total} participants{single}</span>
          </a>"""


def cited_quote(q: dict, check: dict, library) -> str:
    ok = check["status"] == CITATION_OK
    study_name, role = "", ""
    if q["finding_id"] in library.findings:
        study, _ = library.findings[q["finding_id"]]
        study_name = study.name
        t = study.transcripts.get(q["participant_id"])
        role = t.header.get("Role", "") if t else ""
    label = "✓ Verified in report" if ok else QUOTE_PROBLEMS.get(check["status"], "⚠ Check failed")
    note = "" if ok else f'<p class="check-note">{esc(check["message"])}</p>'
    role_attr = f' title="{esc(role)}"' if role else ""
    return f"""
          <figure class="quote {'ok' if ok else 'bad'}">
            <blockquote>{esc(q['quote'])}</blockquote>
            <figcaption>
              <span class="pid"{role_attr}>{esc(q['participant_id'])}</span>
              <span class="muted small">{esc(study_name)}</span>
              <span class="check {'ok' if ok else 'bad'}" title="{esc(check['message'])}">{label}</span>
            </figcaption>
            {note}
          </figure>"""


def tool_summary(tool_log: list) -> str:
    counts = Counter(t["tool"] for t in tool_log)
    return ", ".join(f"{name} ×{n}" if n > 1 else name for name, n in counts.items()) or "no tools"


def turn_card(i: int, turn: dict, library, page_dir: Path) -> str:
    question = f"""
      <div class="question">
        <span class="label ev">Question {i}</span>
        <h3>{esc(turn['question'])}</h3>
      </div>"""

    if turn.get("error") and not turn.get("answer"):
        return f"""
    <article class="card turn" id="q-{i}">{question}
      <div class="alert bad">This question couldn't be answered: {esc(turn['error'])}</div>
    </article>"""

    answer, checks = turn["answer"], turn.get("checks") or {"points": [], "failures": 0, "total": 0}
    tone, coverage_label = COVERAGE.get(answer["coverage"], ("gap", answer["coverage"]))
    badges = [f'<span class="badge cov-{tone}">{coverage_label}</span>']
    if checks["failures"]:
        badges.append(f'<span class="badge bad">⚠ {checks["failures"]} of {checks["total"]} citations failed the check</span>')
    elif checks["total"]:
        badges.append(f'<span class="badge okb">✓ All {checks["total"]} citations verified</span>')

    points = []
    for point, pcheck in zip(answer["points"], checks["points"]):
        sources = "".join(source_chip(f["finding_id"], f["ok"], library, page_dir) for f in pcheck["findings"])
        quotes = "".join(cited_quote(q, c, library) for q, c in zip(point["quotes"], pcheck["quotes"]))
        points.append(f"""
        <li class="point">
          <p>{esc(point['text'])}</p>
          {quotes}
          {f'<p class="src-label">Sources</p><div class="sources">{sources}</div>' if sources else ""}
        </li>""")

    gaps = ""
    if answer["gaps"] or answer["suggested_research"]:
        suggestions = "".join(f"<li>{esc(s)}</li>" for s in answer["suggested_research"])
        gaps = f"""
      <section class="gaps">
        <h4><span class="label gp">Gaps</span> What the research doesn't cover</h4>
        {f"<p>{esc(answer['gaps'])}</p>" if answer["gaps"] else ""}
        {f'<p class="worth">Worth studying</p><ul>{suggestions}</ul>' if suggestions else ""}
      </section>"""

    log = turn.get("tool_log", [])
    steps = "".join(
        f'<li><code>{esc(t["tool"])}</code> {esc(", ".join(f"{k}: {v}" for k, v in t["input"].items() if v))}'
        f' <span class="muted">→ {esc(t["summary"])}</span></li>'
        for t in log
    )
    tools = f"""
      <details class="tools">
        <summary>How this was answered: {len(log)} tool call{'s' if len(log) != 1 else ''} ({esc(tool_summary(log))})</summary>
        <ol>{steps}</ol>
      </details>"""

    return f"""
    <article class="card turn" id="q-{i}">{question}
      <div class="badges">{''.join(badges)}</div>
      <p class="summary-text">{esc(answer['summary'])}</p>
      {f'<section class="evidence"><h4><span class="label ev">Evidence</span> From the research</h4><ol class="points">{"".join(points)}</ol></section>' if points else ""}
      {gaps}
      {tools}
    </article>"""


def render_conversation(conversation: dict, library, page_dir: Path) -> str:
    """page_dir is the folder the page will be saved in, so links to the reports can be relative."""
    turns = conversation["turns"]
    started = datetime.fromisoformat(conversation["started"]).strftime("%-d %B %Y, %H:%M")
    studies = "".join(
        f'<a class="study-chip" href="{esc(report_link(s, page_dir))}">'
        f'<b>{esc(s.name)}</b><span>{len(s.transcripts)} participants · {len(s.findings)} findings</span></a>'
        for s in library.studies.values()
    )
    overview = "".join(
        f"""
          <li><a href="#q-{i}"><span class="num">{i:02d}</span>
            <span class="ov-head">{esc(t['question'])}</span>
            <span class="ov-support">{coverage_pill(t)}</span></a></li>"""
        for i, t in enumerate(turns, 1)
    )
    cards = "".join(turn_card(i, t, library, page_dir) for i, t in enumerate(turns, 1))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research Q&amp;A, {esc(started)}</title>
<style>{CSS}{EXTRA_CSS}</style>
</head>
<body>
<main>
  <header class="report-head">
    <p class="eyebrow">Ask the research</p>
    <h1>Research Q&amp;A</h1>
    <p class="meta">{started} · {len(turns)} question{'s' if len(turns) != 1 else ''} · answered by {esc(conversation.get('model', 'unknown model'))}</p>
  </header>

  <section class="summary">
    <h2>Studies searched</h2>
    <div class="studies">{studies}</div>
    <p class="how">
      Answers come only from the findings reports. Every cited finding and quote was checked by code:
      quotes must match a verified quote in the cited finding, word for word.
      <span class="label in">Interpretation</span> in the reports is an AI hypothesis, not evidence.
    </p>
  </section>

  {f'<section class="overview"><h2>Questions</h2><ol>{overview}</ol></section>' if len(turns) > 1 else ""}

  <section class="findings">
    <h2>Answers</h2>
    {cards}
  </section>
</main>
</body>
</html>
"""


def coverage_pill(turn: dict) -> str:
    if not turn.get("answer"):
        return '<span class="badge bad sm">Error</span>'
    tone, label = COVERAGE.get(turn["answer"]["coverage"], ("gap", ""))
    return f'<span class="badge cov-{tone} sm">{esc(label)}</span>'


EXTRA_CSS = """
:root { --gap: #5f6368; --gap-bg: #eceae4; }
@media (prefers-color-scheme: dark) { :root { --gap: #b9bcc2; --gap-bg: #2a2c30; } }
.small { font-size: .8rem; }
.studies { display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: .75rem; }
.study-chip { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: .8rem 1rem; box-shadow: var(--shadow); display: grid; gap: .15rem; }
.study-chip:hover { border-color: var(--accent); }
.study-chip span { color: var(--muted); font-size: .85rem; }
.summary h2 { margin-top: 0; }

.question h3 { font-size: 1.35rem; margin-top: .5rem; letter-spacing: -.01em; }
.turn .badges { margin-top: 1rem; }
.badge.cov-ok, .badge.okb { background: var(--ok-bg); color: var(--ok); }
.badge.cov-warn { background: var(--warn-bg); color: var(--warn); }
.badge.cov-gap { background: var(--gap-bg); color: var(--gap); }
.summary-text { font-size: 1.1rem; line-height: 1.55; margin: 1rem 0 0; }

.points { list-style: none; padding: 0; margin: 0; counter-reset: pt; }
.point { position: relative; padding-left: 2rem; margin-bottom: 1.4rem; counter-increment: pt; }
.point::before { content: counter(pt); position: absolute; left: 0; top: .1rem; width: 1.4rem; height: 1.4rem; border-radius: 50%; background: var(--ev-bg); color: var(--ev); font-size: .75rem; font-weight: 700; display: grid; place-items: center; }
.point > p { margin: 0 0 .7rem; }
.src-label { margin: .9rem 0 .4rem; font-size: .75rem; font-weight: 700; text-transform: uppercase; letter-spacing: .07em; color: var(--muted); }
.sources { display: grid; gap: .5rem; }
.source { display: grid; gap: .1rem; padding: .55rem .8rem; border: 1px solid var(--border); border-radius: 10px; font-size: .88rem; }
a.source:hover { border-color: var(--accent); background: var(--ev-bg); }
.source.bad { background: var(--bad-bg); color: var(--bad); border-color: transparent; font-weight: 600; }
.src-study { font-size: .75rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: .05em; }
.src-head { font-weight: 500; }
.src-support { display: flex; align-items: center; gap: .45rem; color: var(--muted); font-size: .8rem; }

.gaps { background: var(--gap-bg); border-radius: 12px; padding: 1rem 1.1rem; }
.gaps h4 { color: var(--gap); }
.gaps p { margin: 0 0 .5rem; }
.gaps .worth { font-weight: 600; margin-top: .75rem; }
.gaps ul { margin: 0; padding-left: 1.2rem; }
.label.gp { background: var(--surface); color: var(--gap); border: 1px solid var(--border); }

.tools { margin-top: 1.5rem; border-top: 1px solid var(--border); padding-top: 1rem; font-size: .9rem; }
.tools summary { cursor: pointer; color: var(--muted); font-weight: 500; }
.tools summary:hover { color: var(--text); }
.tools ol { margin: .75rem 0 0; padding-left: 1.4rem; display: grid; gap: .35rem; }
.tools code { font: 600 .82rem ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--bg); border: 1px solid var(--border); border-radius: 5px; padding: .05rem .35rem; }
@media print { .tools { display: none; } }
"""
