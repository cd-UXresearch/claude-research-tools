"""Build the HTML version of a findings report.

The page is a single self-contained .html file: all styling is included, it
loads nothing from the internet, and it works offline. It follows your
computer's light or dark mode, and prints cleanly.

Every piece of text that came from Claude or a transcript goes through
`esc()`, so stray characters like < or & can't break the page.
"""

from datetime import datetime
from html import escape
from pathlib import Path

QUOTE_BADGES = {
    "verified": ("ok", "✓ Verified", "Found word-for-word in this participant's transcript"),
    "wrong_participant": ("bad", "⚠ Wrong participant", None),
    "interviewer_words": ("bad", "⚠ Interviewer's words", None),
    "unknown_participant": ("bad", "⚠ Unknown participant", None),
    "not_found": ("bad", "✕ Not found", None),
}


def esc(text) -> str:
    return escape(" ".join(str(text).split()))


def title_from_folder(folder: Path) -> str:
    return folder.resolve().name.replace("_", " ").replace("-", " ").strip().capitalize() or "Interviews"


def support_dots(n: int, total: int) -> str:
    dots = "".join(f'<i class="dot{" on" if i < n else ""}"></i>' for i in range(total))
    return f'<span class="dots" aria-hidden="true">{dots}</span>'


def quote_block(participant_id: str, quote: str, check, role: str, counter: bool = False) -> str:
    tone, label, tip = QUOTE_BADGES[check.status]
    note = "" if check.ok else f'<p class="check-note">{esc(check.message)}</p>'
    role_attr = f' title="{esc(role)}"' if role else ""
    kind = " counter" if counter else ""
    return f"""
        <figure class="quote {tone}{kind}">
          <blockquote>{esc(quote)}</blockquote>
          <figcaption>
            <span class="pid"{role_attr}>{esc(participant_id)}</span>
            <span class="check {tone}" title="{esc(tip or check.message)}">{label}</span>
          </figcaption>
          {note}
        </figure>"""


def finding_card(index: int, f: dict, total: int, roles: dict) -> str:
    n = len(f["supporting_participants"])
    who = ", ".join(f["supporting_participants"]) or "none"
    badges = [
        f'<span class="badge support">{support_dots(n, total)}{n} of {total} participants</span>'
    ]
    if n == 1:
        badges.append('<span class="badge warn">⚑ Single source</span>')
    if any(not q["check"].ok for q in f["quotes"]) or any(not c["check"].ok for c in f["contrasting_views"]):
        badges.append('<span class="badge bad">⚠ Quote check failed</span>')

    alerts = []
    if n == 1:
        alerts.append(
            '<div class="alert warn"><strong>Single-participant finding.</strong> '
            "Only one person raised this. Treat it as a lead to explore, not a pattern.</div>"
        )
    alerts += [f'<div class="alert bad">{esc(p)}</div>' for p in f["problems"]]

    quotes = "".join(
        quote_block(q["participant_id"], q["quote"], q["check"], roles.get(q["participant_id"], ""))
        for q in f["quotes"]
    )

    contrasting = ""
    if f["contrasting_views"]:
        items = "".join(
            f"""
          <div class="contrast">
            <p><span class="pid">{esc(c['participant_id'])}</span> {esc(c['summary'])}</p>
            {quote_block(c['participant_id'], c['quote'], c['check'], roles.get(c['participant_id'], ''), counter=True)}
          </div>"""
            for c in f["contrasting_views"]
        )
        contrasting = f"""
        <div class="subsection counterpoints">
          <h4><span class="label ct">Contrasting views</span> Participants who disagreed or saw it differently</h4>{items}
        </div>"""

    return f"""
    <article class="card" id="finding-{index}">
      <header class="card-head">
        <span class="num">{index:02d}</span>
        <div>
          <h3>{esc(f['headline'])}</h3>
          <div class="badges">{''.join(badges)}</div>
          <p class="who">Supported by {esc(who)}</p>
        </div>
      </header>
      {''.join(alerts)}
      <section class="evidence">
        <h4><span class="label ev">Evidence</span> What participants said</h4>
        <p>{esc(f['evidence'])}</p>
        {quotes}
        {contrasting}
      </section>
      <section class="interpretation">
        <h4><span class="label in">Interpretation</span> What it might mean</h4>
        <p>{esc(f['interpretation'])}</p>
      </section>
    </article>"""


def render_html(findings: list, transcripts: dict, source_folder: Path, meta: dict) -> str:
    total = len(transcripts)
    checks = [q["check"] for f in findings for q in f["quotes"]] + [
        c["check"] for f in findings for c in f["contrasting_views"]
    ]
    passed = sum(c.ok for c in checks)
    failed = len(checks) - passed
    single = sum(len(f["supporting_participants"]) == 1 for f in findings)
    roles = {pid: t.header.get("Role", "") for pid, t in transcripts.items()}
    title = title_from_folder(source_folder)
    generated = meta.get("generated", datetime.now()).strftime("%-d %B %Y, %H:%M")
    model = meta.get("model") or "unknown model"

    if failed:
        check_banner = (
            f'<div class="banner bad"><strong>{failed} of {len(checks)} quotes failed the check.</strong> '
            "They're marked in red below. Don't use them without checking the transcript yourself.</div>"
        )
    else:
        check_banner = (
            f'<div class="banner ok"><strong>✓ All {len(checks)} quotes verified.</strong> '
            "Each one was found word-for-word in the transcript of the participant it's credited to.</div>"
        )

    overview_rows = "".join(
        f"""
          <li>
            <a href="#finding-{i}">
              <span class="num">{i:02d}</span>
              <span class="ov-head">{esc(f['headline'])}</span>
              <span class="ov-support">{support_dots(len(f['supporting_participants']), total)}
                {len(f['supporting_participants'])}/{total}{' <span class="badge warn sm">Single source</span>' if len(f['supporting_participants']) == 1 else ''}</span>
            </a>
          </li>"""
        for i, f in enumerate(findings, 1)
    )

    participants = "".join(
        f'<li><span class="pid">{esc(pid)}</span> {esc(role) or "<span class=muted>No role given</span>"}</li>'
        for pid, role in roles.items()
    )

    cards = "".join(finding_card(i, f, total, roles) for i, f in enumerate(findings, 1))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} findings</title>
<style>{CSS}</style>
</head>
<body>
<main>
  <header class="report-head">
    <p class="eyebrow">Research findings report</p>
    <h1>{esc(title)}</h1>
    <p class="meta">{generated} · {total} interviews · {len(findings)} findings · synthesized by {esc(model)}</p>
  </header>

  <section class="summary">
    <div class="stats">
      <div class="stat"><b>{len(findings)}</b><span>findings</span></div>
      <div class="stat"><b>{total}</b><span>participants</span></div>
      <div class="stat {'ok' if not failed else 'bad'}"><b>{passed}/{len(checks)}</b><span>quotes verified</span></div>
      <div class="stat {'warn' if single else ''}"><b>{single}</b><span>single-source findings</span></div>
    </div>
    {check_banner}
    <p class="how">
      <span class="label ev">Evidence</span> is what participants said.
      <span class="label in">Interpretation</span> is an AI-generated hypothesis for you to judge, not a conclusion.
      Participant counts and quote checks are computed by code from the transcripts, not by the AI.
    </p>
  </section>

  <section class="overview">
    <h2>Findings at a glance</h2>
    <ol>{overview_rows}
    </ol>
  </section>

  <section class="findings">
    <h2>Findings</h2>
    {cards}
  </section>

  <footer>
    <h2>Participants</h2>
    <ul class="participants">{participants}</ul>
    <p class="muted">Source folder: {esc(source_folder.resolve().name)}</p>
  </footer>
</main>
</body>
</html>
"""


CSS = """
:root {
  color-scheme: light;
  --bg: #f5f4f0; --surface: #ffffff; --text: #1c1d1f; --muted: #62656b;
  --border: #e3e1db; --accent: #2b5a87; --dot: #d9d6ce;
  --ok: #1d6e45; --ok-bg: #e5f3eb; --warn: #7d5300; --warn-bg: #fcefd2;
  --bad: #ad2a20; --bad-bg: #fbe6e3;
  --ev: #2b5a87; --ev-bg: #e8f0f8; --in: #5a4a8c; --in-bg: #f1eef9;
  --shadow: 0 1px 2px rgba(0,0,0,.04), 0 2px 8px rgba(0,0,0,.04);
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --bg: #131416; --surface: #1c1d20; --text: #ebebec; --muted: #9fa2a8;
    --border: #2f3135; --accent: #8db8e8; --dot: #3a3d42;
    --ok: #74cf9c; --ok-bg: #16301f; --warn: #f0c46c; --warn-bg: #352a10;
    --bad: #f2948b; --bad-bg: #3a1c19;
    --ev: #8db8e8; --ev-bg: #182838; --in: #b8a8ec; --in-bg: #241f36;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
main { max-width: 52rem; margin: 0 auto; padding: 3rem 1.25rem 4rem; }
h1, h2, h3, h4 { line-height: 1.25; margin: 0; }
h2 { font-size: .8rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 3rem 0 1rem; }
a { color: inherit; text-decoration: none; }
.muted { color: var(--muted); }

.report-head .eyebrow { color: var(--accent); font-weight: 600; font-size: .85rem; letter-spacing: .06em; text-transform: uppercase; margin: 0 0 .4rem; }
.report-head h1 { font-size: clamp(1.9rem, 4vw, 2.6rem); letter-spacing: -.02em; }
.report-head .meta { color: var(--muted); margin: .6rem 0 0; }

.summary { margin-top: 2rem; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: .75rem; }
.stat { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1rem 1.1rem; box-shadow: var(--shadow); }
.stat b { display: block; font-size: 1.7rem; font-weight: 650; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
.stat span { color: var(--muted); font-size: .9rem; }
.stat.ok b { color: var(--ok); } .stat.bad b { color: var(--bad); } .stat.warn b { color: var(--warn); }
.banner { margin-top: 1rem; padding: .8rem 1rem; border-radius: 10px; }
.banner.ok { background: var(--ok-bg); color: var(--ok); }
.banner.bad { background: var(--bad-bg); color: var(--bad); }
.how { color: var(--muted); font-size: .92rem; margin: 1rem 0 0; }

.overview ol { list-style: none; margin: 0; padding: 0; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; box-shadow: var(--shadow); overflow: hidden; }
.overview li + li { border-top: 1px solid var(--border); }
.overview a { display: grid; grid-template-columns: 2rem 1fr auto; gap: .75rem; align-items: center; padding: .75rem 1rem; }
.overview a:hover { background: var(--ev-bg); }
.overview .num { color: var(--muted); font-variant-numeric: tabular-nums; font-size: .85rem; }
.ov-head { font-weight: 500; }
.ov-support { display: flex; align-items: center; gap: .5rem; white-space: nowrap; color: var(--muted); font-size: .85rem; font-variant-numeric: tabular-nums; }

.dots { display: inline-flex; gap: 3px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--dot); }
.dot.on { background: var(--accent); }

.card { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 1.75rem; margin-bottom: 1.5rem; box-shadow: var(--shadow); scroll-margin-top: 1rem; }
.card-head { display: grid; grid-template-columns: auto 1fr; gap: 1rem; }
.card-head .num { font-size: .85rem; font-weight: 600; color: var(--accent); background: var(--ev-bg); border-radius: 8px; padding: .25rem .5rem; height: fit-content; font-variant-numeric: tabular-nums; }
.card h3 { font-size: 1.25rem; letter-spacing: -.01em; }
.badges { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .75rem; }
.badge { display: inline-flex; align-items: center; gap: .45rem; font-size: .82rem; font-weight: 600; padding: .25rem .65rem; border-radius: 999px; }
.badge.support { background: var(--ev-bg); color: var(--ev); }
.badge.warn { background: var(--warn-bg); color: var(--warn); }
.badge.bad { background: var(--bad-bg); color: var(--bad); }
.badge.sm { font-size: .72rem; padding: .1rem .5rem; }
.who { color: var(--muted); font-size: .88rem; margin: .5rem 0 0; }

.alert { margin-top: 1rem; padding: .7rem .9rem; border-radius: 10px; font-size: .92rem; }
.alert.warn { background: var(--warn-bg); color: var(--warn); }
.alert.bad { background: var(--bad-bg); color: var(--bad); }

.card section { margin-top: 1.5rem; }
.card h4 { font-size: .95rem; font-weight: 600; display: flex; align-items: center; gap: .6rem; color: var(--muted); margin-bottom: .6rem; }
.label { font-size: .72rem; font-weight: 700; text-transform: uppercase; letter-spacing: .07em; padding: .15rem .5rem; border-radius: 6px; }
.label.ev { background: var(--ev-bg); color: var(--ev); }
.label.in { background: var(--in-bg); color: var(--in); }
.evidence > p { margin: 0 0 1rem; }

.quote { margin: 0 0 .75rem; padding: .9rem 1rem .8rem 1.1rem; border-left: 3px solid var(--ev); background: var(--bg); border-radius: 0 10px 10px 0; }
.quote.bad { border-left-color: var(--bad); background: var(--bad-bg); }
.quote blockquote { margin: 0; font-family: Georgia, "Iowan Old Style", "Times New Roman", serif; font-size: 1.05rem; line-height: 1.55; }
.quote blockquote::before { content: "\\201C"; } .quote blockquote::after { content: "\\201D"; }
.quote figcaption { display: flex; align-items: center; gap: .6rem; margin-top: .5rem; }
.pid { display: inline-block; font-size: .78rem; font-weight: 700; letter-spacing: .03em; padding: .05rem .45rem; border-radius: 6px; background: var(--surface); border: 1px solid var(--border); font-variant-numeric: tabular-nums; }
.check { font-size: .8rem; font-weight: 600; }
.check.ok { color: var(--ok); } .check.bad { color: var(--bad); }
.check-note { margin: .4rem 0 0; font-size: .88rem; color: var(--bad); }

.subsection { margin-top: 1.25rem; }
.counterpoints { border-top: 1px dashed var(--border); padding-top: 1.1rem; }
.label.ct { background: var(--bg); color: var(--muted); border: 1px solid var(--border); }
.contrast > p { margin: 0 0 .5rem; }
.quote.counter { border-left-style: dashed; border-left-color: var(--muted); background: transparent; border: 1px solid var(--border); border-left: 3px dashed var(--muted); }

.interpretation { background: var(--in-bg); border-radius: 12px; padding: 1rem 1.1rem; }
.interpretation h4 { color: var(--in); }
.interpretation p { margin: 0; font-style: italic; }

footer h2 { margin-top: 3.5rem; }
.participants { list-style: none; padding: 0; margin: 0 0 1rem; display: grid; gap: .4rem; }
.participants li { display: flex; gap: .6rem; align-items: baseline; }
footer .muted { font-size: .82rem; word-break: break-all; }

@media (max-width: 560px) {
  main { padding-top: 2rem; }
  .card { padding: 1.2rem; }
  .card-head { grid-template-columns: 1fr; gap: .5rem; }
  .card-head .num { width: fit-content; }
  .overview a { grid-template-columns: 1.6rem 1fr; }
  .ov-support { grid-column: 2; }
}
@media print {
  body { background: #fff; }
  .card, .stat, .overview ol { box-shadow: none; break-inside: avoid; }
}
"""
