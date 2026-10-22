"""research-synth: turn interview transcripts into a findings report.

Usage (from this folder, with the virtual environment active):

    python research_synth.py sample_transcripts

See README.md for setup and options.
"""

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from html_report import render_html, title_from_folder
from quote_check import check_quote, load_transcripts

DEFAULT_MODEL = "claude-opus-5-5"

# --- What we ask Claude for ---------------------------------------------------

SYSTEM_PROMPT = """\
You are helping a UX researcher synthesize qualitative interview transcripts \
into findings. The researcher will use your output in a report, and every \
quote and participant count will be checked by code against the transcripts, \
so accuracy matters far more than polish.

How to work:
- A finding is a pattern, need, pain point, or behaviour that is grounded in \
what participants actually said. Include strong findings that only one \
participant raised; the report will flag them as single-source.
- Keep evidence and interpretation separate. "evidence" describes only what \
participants said or reported doing, in neutral terms. "interpretation" is \
where you say what it might mean, why it might be happening, or what it could \
imply for the product, phrased as a hypothesis, not a fact.
- "supporting_participants" lists only participants whose own words support \
the finding. Do not include someone because they might agree. Do not state \
counts or percentages anywhere; the report computes them.
- "contrasting_views" lists participants who disagreed, had the opposite \
experience, or downplayed the issue. Leave it empty if nobody did. Never drop \
disagreement to make a finding look cleaner.
- Quotes must be copied exactly, character for character, from the \
participant's own lines (never the interviewer's), attributed to the \
participant who said them. Do not fix grammar, paraphrase, merge sentences \
from different places, or change capitalisation. Prefer one continuous \
excerpt; if you must skip words, use "..." between verbatim pieces from the \
same answer. Give 1 to 3 quotes per finding.
- Order findings from most to least important for the product team.
"""

PARTICIPANT_QUOTE = {
    "type": "object",
    "properties": {
        "participant_id": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["participant_id", "quote"],
    "additionalProperties": False,
}

FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "supporting_participants": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "string"},
                    "quotes": {"type": "array", "items": PARTICIPANT_QUOTE},
                    "contrasting_views": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "participant_id": {"type": "string"},
                                "summary": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["participant_id", "summary", "quote"],
                            "additionalProperties": False,
                        },
                    },
                    "interpretation": {"type": "string"},
                },
                "required": [
                    "headline", "supporting_participants", "evidence",
                    "quotes", "contrasting_views", "interpretation",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def build_user_message(transcripts: dict) -> str:
    parts = [
        f"Here are {len(transcripts)} interview transcripts. "
        f"The participant IDs are: {', '.join(transcripts)}.\n"
    ]
    for pid, t in transcripts.items():
        parts.append(f'<transcript participant_id="{pid}">\n{t.raw_text.strip()}\n</transcript>\n')
    parts.append("Synthesize these transcripts into findings.")
    return "\n".join(parts)


class SynthesisError(Exception):
    """A problem we can explain to the user in plain language."""


def save_answer(response, request_id, study: dict, answer_path: Path, details_path: Path) -> str:
    """Write Claude's answer to disk straight away, before anything else can fail.

    Two files are written:
    - answer_path: Claude's answer text exactly as received. It's the input for --from-json.
    - details_path: the full API response (model, token usage, request ID), for the record.
    Returns the answer text.
    """
    text = "".join(block.text for block in response.content if block.type == "text")
    answer_path.write_text(text, encoding="utf-8")
    try:
        details = response.to_dict()
    except Exception:
        # The details file is a nice-to-have; never let it block saving the answer.
        details = {"model": getattr(response, "model", None)}
    details["request_id"] = request_id
    details["study"] = study
    details_path.write_text(json.dumps(details, indent=2, default=str), encoding="utf-8")
    return text


def ask_claude(transcripts: dict, model: str, effort: str, study: dict, answer_path: Path, details_path: Path) -> dict:
    client = anthropic.Anthropic()
    try:
        # Streaming keeps the connection alive during long analyses.
        # "fallbacks" lets the API retry on a backup model if this one declines.
        with client.beta.messages.stream(
            model=model,
            max_tokens=64000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_message(transcripts)}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": FINDINGS_SCHEMA},
            },
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()
            # Save first: from here on the answer has been paid for.
            text = save_answer(response, stream.request_id, study, answer_path, details_path)
    except anthropic.AuthenticationError:
        raise SynthesisError(
            "The API rejected your key. Check that ANTHROPIC_API_KEY in .env is "
            "copied correctly, with no spaces or quote marks around it."
        )
    except anthropic.PermissionDeniedError as e:
        raise SynthesisError(f"Your API key doesn't have access to this request: {e.message}")
    except anthropic.NotFoundError:
        raise SynthesisError(f"The model {model!r} wasn't found. Check the --model option.")
    except anthropic.RateLimitError:
        raise SynthesisError("Rate limit reached. Wait a minute and try again.")
    except anthropic.BadRequestError as e:
        raise SynthesisError(f"The API couldn't process the request: {e.message}")
    except anthropic.APIStatusError as e:
        raise SynthesisError(f"The API returned an error ({e.status_code}): {e.message}. Try again later.")
    except anthropic.APIConnectionError:
        raise SynthesisError("Couldn't reach the API. Check your internet connection.")

    if response.stop_reason == "refusal":
        raise SynthesisError("Claude declined to analyze these transcripts.")
    if response.stop_reason == "max_tokens":
        raise SynthesisError("The response was cut off because it was too long. Try fewer transcripts at a time.")
    return parse_answer(text)


def parse_answer(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise SynthesisError("Claude's answer wasn't in the expected format (it isn't valid JSON).")
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise SynthesisError("Claude's answer wasn't in the expected format (no list of findings).")
    return data


def details_path_for(answer_path: Path) -> Path:
    return answer_path.with_name(answer_path.stem + "_details.json")


def load_details(answer_path: Path) -> dict:
    """Read the details file saved next to an answer, if there is one."""
    try:
        return json.loads(details_path_for(answer_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def answer_time(answer_path: Path) -> datetime:
    """When Claude produced an answer, from its file name (findings_<date>_<time>.json).

    Rebuilt reports show this time, not the time of the rebuild.
    """
    try:
        return datetime.strptime(answer_path.stem.removeprefix("findings_"), "%Y-%m-%d_%H%M%S")
    except ValueError:
        return datetime.now()


def study_info(transcripts_dir: Path) -> dict:
    """Which study a report came from. ask_research.py uses this to find the transcripts."""
    folder = transcripts_dir.resolve()
    project = Path(__file__).resolve().parent
    if folder.is_relative_to(project):
        folder = folder.relative_to(project)   # e.g. "mobile_interviews", so no personal paths are saved
    return {"name": title_from_folder(transcripts_dir), "transcripts_folder": folder.as_posix()}


def record_study(answer_path: Path, study: dict) -> None:
    """Add or update the study info in a details file (e.g. older reports that lack it)."""
    details = load_details(answer_path)
    if details and details.get("study") != study:
        details["study"] = study
        details_path_for(answer_path).write_text(json.dumps(details, indent=2, default=str), encoding="utf-8")


# --- Checking and writing the report -----------------------------------------

CHECK_LABELS = {
    "verified": "✅ verified",
    "wrong_participant": "⚠️ WRONG PARTICIPANT",
    "interviewer_words": "⚠️ INTERVIEWER'S WORDS",
    "unknown_participant": "⚠️ UNKNOWN PARTICIPANT",
    "not_found": "❌ NOT FOUND IN TRANSCRIPTS",
}


def check_findings(data: dict, transcripts: dict) -> list:
    """Recount support and check every quote. Returns findings with check results attached."""
    checked = []
    for finding in data["findings"]:
        problems = []
        supporters = list(dict.fromkeys(finding["supporting_participants"]))  # removes duplicates
        unknown = [pid for pid in supporters if pid not in transcripts]
        if unknown:
            problems.append(f"Lists participants with no transcript: {', '.join(unknown)}. They are not counted.")
        supporters = [pid for pid in supporters if pid in transcripts]

        quotes = []
        for q in finding["quotes"]:
            result = check_quote(q["quote"], q["participant_id"], transcripts)
            quotes.append({**q, "check": result})
            if result.ok and q["participant_id"] not in supporters:
                problems.append(f"Quotes {q['participant_id']}, who isn't listed as supporting this finding.")

        if not 1 <= len(finding["quotes"]) <= 3:
            problems.append(f"Has {len(finding['quotes'])} quotes; expected 1 to 3.")

        contrasting = []
        for c in finding["contrasting_views"]:
            contrasting.append({**c, "check": check_quote(c["quote"], c["participant_id"], transcripts)})

        checked.append({
            **finding,
            "supporting_participants": supporters,
            "quotes": quotes,
            "contrasting_views": contrasting,
            "problems": problems,
        })
    return checked


def md_quote(text: str) -> str:
    return " ".join(text.split())


def render_report(findings: list, transcripts: dict, source_folder: Path, meta: dict) -> str:
    total = len(transcripts)
    all_checks = [q["check"] for f in findings for q in f["quotes"]] + [
        c["check"] for f in findings for c in f["contrasting_views"]
    ]
    failed = [c for c in all_checks if not c.ok]

    lines = [
        f"# Findings report: {source_folder.name}",
        "",
        f"- **Generated:** {meta.get('generated', datetime.now()).strftime('%Y-%m-%d %H:%M')}",
        f"- **Transcripts:** {total} ({', '.join(transcripts)})",
        f"- **Model:** {meta.get('model', 'unknown')}",
        "",
        "> **How to read this report.** *Evidence* describes what participants said. "
        "*Interpretation* is an AI-generated hypothesis for you to judge, not a conclusion. "
        "Participant counts and quote checks are computed by code directly from the transcripts, not by the AI.",
        "",
        "## Quote check",
        "",
    ]
    if failed:
        lines.append(
            f"⚠️ **{len(failed)} of {len(all_checks)} quotes failed the check.** "
            "They are marked below. Do not use them without checking the transcript yourself."
        )
    else:
        lines.append(f"✅ All {len(all_checks)} quotes were found word-for-word in the right participant's transcript.")

    lines += ["", "## Summary", "", "| # | Finding | Support |", "|---|---|---|"]
    for i, f in enumerate(findings, 1):
        n = len(f["supporting_participants"])
        flag = " ⚑ single participant" if n == 1 else ""
        headline = f["headline"].replace("|", "\\|")
        lines.append(f"| {i} | {headline} | {n} of {total}{flag} |")

    for i, f in enumerate(findings, 1):
        n = len(f["supporting_participants"])
        lines += [
            "",
            "---",
            "",
            f"## {i}. {f['headline']}",
            "",
            f"**Support:** {n} of {total} participants ({', '.join(f['supporting_participants']) or 'none'})",
        ]
        if n == 1:
            lines += [
                "",
                "> ⚑ **Single-participant finding.** Only one person raised this. "
                "Treat it as a lead to explore, not a pattern.",
            ]
        for problem in f["problems"]:
            lines += ["", f"> ⚠️ {problem}"]

        lines += ["", "### Evidence", "", f["evidence"], ""]
        for q in f["quotes"]:
            check = q["check"]
            lines.append(f"> \"{md_quote(q['quote'])}\" (**{q['participant_id']}**) {CHECK_LABELS[check.status]}")
            if not check.ok:
                lines.append(f"> *{check.message}*")
            lines.append("")

        if f["contrasting_views"]:
            lines += ["### Contrasting views", ""]
            for c in f["contrasting_views"]:
                check = c["check"]
                lines.append(f"- **{c['participant_id']}:** {c['summary']}")
                lines.append(f"  > \"{md_quote(c['quote'])}\" {CHECK_LABELS[check.status]}")
                if not check.ok:
                    lines.append(f"  > *{check.message}*")
            lines.append("")

        lines += ["### Interpretation", "", f"*{f['interpretation']}*"]

    lines.append("")
    return "\n".join(lines)


# --- Command line ------------------------------------------------------------

def print_retry_help(answer_path: Path, transcripts_dir: Path) -> None:
    """After an unexpected error, say where the paid-for answer is and how to reuse it.

    The full error (traceback) is still shown after this, for debugging.
    """
    print(
        f"\nSomething went wrong after Claude answered. The answer is safe in {answer_path}\n"
        f"After it's fixed, rebuild the report for free with:\n"
        f"  python research_synth.py {transcripts_dir} --from-json {answer_path}\n",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="research-synth",
        description="Synthesize interview transcripts (.txt files) into a markdown findings report.",
    )
    parser.add_argument("transcripts", type=Path, help="folder containing .txt transcripts")
    parser.add_argument("--out", type=Path, default=Path("reports"), help="folder for the report (default: reports)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model to use (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"],
        help="how hard Claude thinks; higher is slower and costs more (default: high)",
    )
    parser.add_argument(
        "--from-json", type=Path, metavar="FILE",
        help="rebuild the reports from a saved .json answer instead of calling the API (free)",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="don't open the HTML report in your browser when finished",
    )
    args = parser.parse_args()

    if not args.transcripts.is_dir():
        print(f"Error: {args.transcripts} is not a folder.", file=sys.stderr)
        return 1
    try:
        transcripts = load_transcripts(args.transcripts)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not transcripts:
        print(f"Error: no .txt files found in {args.transcripts}.", file=sys.stderr)
        return 1
    print(f"Read {len(transcripts)} transcripts: {', '.join(transcripts)}")

    # Create the output folder before calling the API, so a folder problem
    # can't happen after we've paid for an answer.
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    report_path = args.out / f"findings_{stamp}.md"
    study = study_info(args.transcripts)

    if args.from_json:
        answer_path = args.from_json
        # Name the rebuilt reports after the answer, replacing the earlier versions.
        report_path = args.out / f"{answer_path.stem}.md"
        try:
            data = parse_answer(answer_path.read_text(encoding="utf-8"))
        except (OSError, SynthesisError) as e:
            print(f"Error reading {answer_path}: {e}", file=sys.stderr)
            return 1
    else:
        load_dotenv()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "Error: ANTHROPIC_API_KEY isn't set.\n"
                "Copy .env.example to a new file named .env and put your key in it. See README.md.",
                file=sys.stderr,
            )
            return 1
        answer_path = args.out / f"findings_{stamp}.json"
        details_path = args.out / f"findings_{stamp}_details.json"
        print(f"Asking {args.model} to synthesize findings (this can take a few minutes)...")
        try:
            data = ask_claude(transcripts, args.model, args.effort, study, answer_path, details_path)
        except SynthesisError as e:
            print(f"Error: {e}", file=sys.stderr)
            if answer_path.exists():
                print(f"Claude's answer was saved to {answer_path}", file=sys.stderr)
            return 1
        except Exception:
            if answer_path.exists():
                print_retry_help(answer_path, args.transcripts)
            raise
        print(f"Saved Claude's answer to {answer_path}")

    html_path = report_path.with_suffix(".html")
    try:
        findings = check_findings(data, transcripts)
        if args.from_json:
            record_study(answer_path, study)
        meta = {**load_details(answer_path), "generated": answer_time(answer_path)}
        report_path.write_text(render_report(findings, transcripts, args.transcripts, meta), encoding="utf-8")
        html_path.write_text(render_html(findings, transcripts, args.transcripts, meta), encoding="utf-8")
    except Exception:
        print_retry_help(answer_path, args.transcripts)
        raise

    checks = [q["check"] for f in findings for q in f["quotes"]] + [
        c["check"] for f in findings for c in f["contrasting_views"]
    ]
    failed = sum(not c.ok for c in checks)
    print(f"Wrote {len(findings)} findings to:\n  {html_path}\n  {report_path}")
    if failed:
        print(f"WARNING: {failed} of {len(checks)} quotes failed the check. They're marked in the report.")
    else:
        print(f"All {len(checks)} quotes verified against the transcripts.")

    if not args.no_open:
        webbrowser.open(html_path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
