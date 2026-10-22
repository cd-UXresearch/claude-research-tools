"""ask-research: chat with an agent that answers questions from your research reports.

Usage (from this folder, with the virtual environment active):

    python ask_research.py                      # start a chat
    python ask_research.py "what do we know about booking conflicts?"   # ask one question

See README.md for details.
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

from answer_html import render_conversation
from research_library import (
    CITATION_OK, check_answer, get_finding, list_studies, load_library, search_findings,
)

DEFAULT_MODEL = "claude-opus-5-5"
MAX_STEPS = 15   # safety limit on tool calls per question

SYSTEM_PROMPT = """\
You are a research assistant for a UX research team. You answer questions \
using only the team's synthesized interview findings, which you can reach \
through your tools. The researcher will rely on your answer, and code will \
check every finding and quote you cite, so accuracy and honesty matter more \
than sounding complete.

How to work:
- Always look before you answer. Start with list_studies or search_findings, \
then use get_finding to read the full finding before you rely on it or quote \
it. Search with several wordings and synonyms if the first search misses \
(for example "double booking", "clash", "conflict").
- Base every statement on findings you have read. Cite them by finding_id. \
Never use outside knowledge about how products or users usually behave.
- Quotes must be copied exactly from the verified_quotes or verified_quote \
fields that get_finding returned, credited to the participant who said them, \
with the finding_id you got them from. Never write a quote from memory or \
paraphrase inside quotation marks.
- Keep evidence and interpretation apart. Findings' evidence and quotes are \
what participants said. "interpretation_by_ai" is an earlier AI hypothesis, \
not a fact; if you mention it, say so.
- Be precise about support: say "4 of 6 participants in <study>", note \
single-source findings as such, and include disagreement (contrasting views) \
rather than smoothing it over. Studies had different participants, so don't \
add counts across studies.
- If the research doesn't cover the question, or only partly covers it, say \
so plainly. Set coverage accordingly, explain the gap, and suggest what a \
future study could look into. Do not fill gaps with guesses.

Finish every answer by calling submit_answer. Put the whole answer in it; \
don't also write it as text.
"""

TOOLS = [
    {
        "name": "list_studies",
        "description": (
            "List every study in the research library: its id, name, participants and their roles, "
            "and the headline and id of every finding. Call this first to see what research exists, "
            "or whenever you want to browse all findings rather than search."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "search_findings",
        "description": (
            "Keyword search across all findings (headlines, evidence, quotes, contrasting views, "
            "interpretations). Returns matching finding ids, headlines and support counts, best first. "
            "It matches words, not meanings, so try several wordings and synonyms. Use it to find "
            "findings relevant to the question, then read them with get_finding."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to search for, e.g. 'notifications email'"},
                "study_id": {
                    "type": "string",
                    "description": "Optional: only search this study (an id from list_studies). Leave empty for all.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_finding",
        "description": (
            "Read one finding in full: headline, support count, supporting participants and their roles, "
            "evidence, verified quotes, contrasting views, and the earlier AI interpretation. "
            "Call this before citing or quoting any finding. Only the quotes it returns may be quoted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "finding_id": {"type": "string", "description": "A finding id, e.g. 'mobile_interviews/F03'"},
            },
            "required": ["finding_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_answer",
        "description": (
            "Give the researcher your final answer. Call this exactly once per question, after you "
            "have read the findings you rely on."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "coverage": {
                    "type": "string",
                    "enum": ["covered", "partly_covered", "not_covered"],
                    "description": "How well the research answers the question.",
                },
                "summary": {"type": "string", "description": "A direct answer in 1 to 3 sentences."},
                "points": {
                    "type": "array",
                    "description": "The supporting points, each tied to the findings it comes from.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "finding_ids": {"type": "array", "items": {"type": "string"}},
                            "quotes": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "finding_id": {"type": "string"},
                                        "participant_id": {"type": "string"},
                                        "quote": {"type": "string"},
                                    },
                                    "required": ["finding_id", "participant_id", "quote"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["text", "finding_ids", "quotes"],
                        "additionalProperties": False,
                    },
                },
                "gaps": {
                    "type": "string",
                    "description": "What the research doesn't cover about this question. Empty if nothing.",
                },
                "suggested_research": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Questions a future study could explore to fill the gaps. Empty if none.",
                },
            },
            "required": ["coverage", "summary", "points", "gaps", "suggested_research"],
            "additionalProperties": False,
        },
    },
]


class AgentError(Exception):
    """A problem we can explain to the user in plain language."""


def run_tool(library, name: str, tool_input: dict) -> tuple:
    """Run one research tool. Returns (result data, short summary for the screen)."""
    if name == "list_studies":
        result = list_studies(library)
        n = sum(len(s["findings"]) for s in result["studies"])
        return result, f"{len(result['studies'])} studies, {n} findings"
    if name == "search_findings":
        result = search_findings(library, tool_input.get("query", ""), tool_input.get("study_id", ""))
        if "error" in result:
            return result, result["error"]
        return result, f"{len(result['matches'])} matching findings"
    if name == "get_finding":
        result = get_finding(library, tool_input.get("finding_id", ""))
        if "error" in result:
            return result, result["error"]
        return result, f"{result['headline'][:70]} ({result['support']})"
    return {"error": f"Unknown tool {name!r}"}, f"unknown tool {name!r}"


def describe_call(name: str, tool_input: dict) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in tool_input.items() if v)
    return f"{name}({args})"


class ResearchAgent:
    def __init__(self, library, model: str, effort: str):
        self.library = library
        self.model = model
        self.effort = effort
        self.client = anthropic.Anthropic()
        # The full conversation, including every tool call and result. The API
        # needs it unchanged on every request, so we only ever add to it.
        self.messages = []
        # Results still owed to Claude for its last tool calls (its submit_answer).
        # They go at the start of the next question's message.
        self.pending_results = []

    def ask(self, question: str, on_tool=print) -> dict:
        """Ask one question. Returns {"answer": ..., "tool_log": [...], "usage": {...}}.

        If anything goes wrong, the conversation is rolled back to how it was
        before this question, so the next question still works.
        """
        before = (len(self.messages), list(self.pending_results))
        try:
            return self._ask(question, on_tool)
        except Exception:
            del self.messages[before[0]:]
            self.pending_results = before[1]
            raise

    def _ask(self, question: str, on_tool) -> dict:
        self.messages.append({
            "role": "user",
            "content": self.pending_results + [{"type": "text", "text": question}],
        })
        self.pending_results = []
        tool_log = []
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        nudged = False

        for _ in range(MAX_STEPS):
            response = self.call_claude()
            self.messages.append({"role": "assistant", "content": response.content})
            for key in usage:
                usage[key] += getattr(response.usage, key, 0) or 0

            if response.stop_reason == "refusal":
                raise AgentError("Claude declined to answer this question.")
            if response.stop_reason == "max_tokens":
                raise AgentError("The answer was cut off because it was too long. Try a narrower question.")

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if nudged:
                    raise AgentError("Claude replied without using submit_answer, so the answer can't be checked.")
                # Claude answered in plain text. Ask it once to use the answer tool.
                self.messages.append({"role": "user", "content": [{
                    "type": "text", "text": "Please give that answer by calling submit_answer.",
                }]})
                nudged = True
                continue

            results, answer = [], None
            for block in tool_uses:
                if block.name == "submit_answer":
                    answer = block.input
                    content = "Answer received and shown to the researcher."
                else:
                    data, summary = run_tool(self.library, block.name, block.input)
                    tool_log.append({"tool": block.name, "input": block.input, "summary": summary})
                    on_tool(f"  🔧 {describe_call(block.name, block.input)} → {summary}")
                    content = json.dumps(data, ensure_ascii=False)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})

            if answer is not None:
                self.pending_results = results
                return {"answer": answer, "tool_log": tool_log, "usage": usage}
            self.messages.append({"role": "user", "content": results})

        raise AgentError(f"Claude used more than {MAX_STEPS} steps without answering. Try a narrower question.")

    def call_claude(self):
        try:
            return self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self.messages,
                output_config={"effort": self.effort},
                # Reuse the unchanged start of the conversation from cache: cheaper and faster.
                cache_control={"type": "ephemeral"},
                # Retry on a backup model if this one declines.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise AgentError(
                "The API rejected your key. Check that ANTHROPIC_API_KEY in .env is "
                "copied correctly, with no spaces or quote marks around it."
            )
        except anthropic.PermissionDeniedError as e:
            raise AgentError(f"Your API key doesn't have access to this request: {e.message}")
        except anthropic.NotFoundError:
            raise AgentError(f"The model {self.model!r} wasn't found. Check the --model option.")
        except anthropic.RateLimitError:
            raise AgentError("Rate limit reached. Wait a minute and try again.")
        except anthropic.BadRequestError as e:
            raise AgentError(f"The API couldn't process the request: {e.message}")
        except anthropic.APIStatusError as e:
            raise AgentError(f"The API returned an error ({e.status_code}): {e.message}. Try again later.")
        except anthropic.APIConnectionError:
            raise AgentError("Couldn't reach the API. Check your internet connection.")


# --- Showing answers in the terminal -----------------------------------------

COVERAGE_LABELS = {
    "covered": "✅ Covered by the research",
    "partly_covered": "◐ Partly covered by the research",
    "not_covered": "○ Not covered by the research",
}


def print_answer(turn: dict, library) -> None:
    answer, checks = turn["answer"], turn["checks"]
    print()
    print(COVERAGE_LABELS.get(answer["coverage"], answer["coverage"]))
    print()
    print(answer["summary"])
    for point, pcheck in zip(answer["points"], checks["points"]):
        cites = []
        for f in pcheck["findings"]:
            if f["ok"]:
                study, finding = library.findings[f["finding_id"]]
                cites.append(f"{study.name} · F{finding['number']:02d}")
            else:
                cites.append(f"⚠ {f['finding_id']} (NOT IN REPORTS)")
        print(f"\n• {point['text']}")
        if cites:
            print(f"  Sources: {'; '.join(cites)}")
        for q, qcheck in zip(point["quotes"], pcheck["quotes"]):
            mark = "✓" if qcheck["status"] == CITATION_OK else "⚠"
            print(f"    “{q['quote']}” ({q['participant_id']}) {mark}")
            if qcheck["status"] != CITATION_OK:
                print(f"      {qcheck['message']}")
    if answer["gaps"]:
        print(f"\nWhat the research doesn't cover: {answer['gaps']}")
    if answer["suggested_research"]:
        print("Worth studying:")
        for s in answer["suggested_research"]:
            print(f"  – {s}")

    print()
    if checks["failures"]:
        print(f"⚠ {checks['failures']} of {checks['total']} citations failed the check. They're marked above.")
    elif checks["total"]:
        print(f"✓ All {checks['total']} citations checked against the reports.")
    tools = ", ".join(t["tool"] for t in turn["tool_log"]) or "none"
    print(f"Tools used: {tools}")


# --- Saving the conversation --------------------------------------------------

def save_json(conversation: dict, json_path: Path) -> None:
    json_path.write_text(json.dumps(conversation, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ask-research",
        description="Ask questions about your research. Answers come only from the verified reports.",
    )
    parser.add_argument("question", nargs="*", help="ask one question and exit (leave out to start a chat)")
    parser.add_argument("--reports", type=Path, default=Path("reports"), help="folder of findings reports (default: reports)")
    parser.add_argument("--out", type=Path, default=Path("answers"), help="folder for saved conversations (default: answers)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model to use (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"],
        help="how hard Claude thinks; higher is slower and costs more (default: medium)",
    )
    parser.add_argument(
        "--from-json", type=Path, metavar="FILE",
        help="rebuild a saved conversation's HTML page from its .json file (free, no API call)",
    )
    parser.add_argument("--no-open", action="store_true", help="don't open the conversation in your browser")
    args = parser.parse_args()

    library = load_library(args.reports)
    for warning in library.warnings:
        print(f"Note: {warning}", file=sys.stderr)
    if not library.studies:
        print(f"Error: no usable reports found in {args.reports}. Run research_synth.py first.", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)

    if args.from_json:
        conversation = json.loads(args.from_json.read_text(encoding="utf-8"))
        for turn in conversation["turns"]:
            if turn.get("answer"):
                turn["checks"] = check_answer(turn["answer"], library)
        html_path = args.from_json.with_suffix(".html")
        html_path.write_text(render_conversation(conversation, library, html_path.parent), encoding="utf-8")
        print(f"Rebuilt {html_path}")
        if not args.no_open:
            webbrowser.open(html_path.resolve().as_uri())
        return 0

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "Error: ANTHROPIC_API_KEY isn't set.\n"
            "Copy .env.example to a new file named .env and put your key in it. See README.md.",
            file=sys.stderr,
        )
        return 1

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    json_path = args.out / f"conversation_{stamp}.json"
    html_path = args.out / f"conversation_{stamp}.html"
    conversation = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "model": args.model,
        "studies": [{"id": s.id, "name": s.name, "report": s.answer_path.name} for s in library.studies.values()],
        "turns": [],
    }
    agent = ResearchAgent(library, args.model, args.effort)

    studies = ", ".join(f"{s.name} ({len(s.findings)} findings)" for s in library.studies.values())
    print(f"Research library: {studies}")

    one_shot = bool(args.question)
    if not one_shot:
        print('Ask a question about your research. Type "quit" (or press Ctrl+D) to finish.\n')

    while True:
        if one_shot:
            question = " ".join(args.question)
        else:
            try:
                question = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if question.lower() in {"quit", "exit", "q"}:
                break
            if not question:
                continue

        print("Researching...")
        turn = {"question": question, "asked": datetime.now().isoformat(timespec="seconds")}
        try:
            result = agent.ask(question)
            turn.update(result)
        except AgentError as e:
            turn["error"] = str(e)
            print(f"Error: {e}", file=sys.stderr)
        conversation["turns"].append(turn)

        # Save Claude's answer first, before checking or page-building can fail.
        save_json(conversation, json_path)
        try:
            if turn.get("answer"):
                turn["checks"] = check_answer(turn["answer"], library)
                print_answer(turn, library)
            save_json(conversation, json_path)
            html_path.write_text(render_conversation(conversation, library, html_path.parent), encoding="utf-8")
        except Exception:
            print(
                f"\nSomething went wrong after Claude answered. The conversation is saved in {json_path}\n"
                f"After it's fixed, rebuild the page for free with:\n"
                f"  python ask_research.py --from-json {json_path}\n",
                file=sys.stderr,
            )
            raise

        if one_shot:
            break
        print()

    if conversation["turns"]:
        print(f"\nConversation saved to {html_path}")
        if not args.no_open:
            webbrowser.open(html_path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
