"""Tests for the ask-research agent, using a fake Claude API on this computer.

The fake API plays back a scripted conversation, including deliberate mistakes
(an invented quote, a misattributed quote, a finding that doesn't exist), so we
can check that the agent's tools work and that every mistake gets flagged.
No API key or internet is needed and nothing is billed.

Run with:  python -m unittest discover tests
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import research_library as rl  # noqa: E402

FAKE_ANSWER = ROOT / "tests" / "fake_claude_answer.json"


def make_reports_folder(folder: Path) -> None:
    """A reports folder with one study: the fake answer, checked against sample_transcripts."""
    shutil.copy(FAKE_ANSWER, folder / "findings_2026-01-01_000000.json")
    details = {"model": "fake", "study": {
        "name": "Sample transcripts", "transcripts_folder": str(ROOT / "sample_transcripts"),
    }}
    (folder / "findings_2026-01-01_000000_details.json").write_text(json.dumps(details))


def message(content, stop_reason):
    return {
        "id": "msg_fake", "type": "message", "role": "assistant", "model": "fake-model",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def tool_use(id_, name, tool_input):
    return {"type": "tool_use", "id": id_, "name": name, "input": tool_input}


class FakeAPI:
    """Answers each request with the next scripted (status, body) and records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                api.requests.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
                status, body = api.script.pop(0)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("request-id", "req_fake")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


GOOD_SUBMIT = {
    "coverage": "partly_covered",
    "summary": "Unused bookings came up often.",
    "points": [{
        "text": "Participants described booked instruments sitting empty.",
        "finding_ids": ["sample_transcripts/F01", "sample_transcripts/F09"],
        "quotes": [
            # Real and verified.
            {"finding_id": "sample_transcripts/F01", "participant_id": "P01",
             "quote": "Bookings that nobody shows up for."},
            # This quote failed the report's own check, so the agent should never have seen it.
            {"finding_id": "sample_transcripts/F01", "participant_id": "P04",
             "quote": "Nobody ever comes to their bookings."},
            # Real quote, but said by P06, not P01.
            {"finding_id": "sample_transcripts/F01", "participant_id": "P01",
             "quote": "it's people like me who lose out"},
        ],
    }],
    "gaps": "",
    "suggested_research": [],
}

NOT_COVERED_SUBMIT = {
    "coverage": "not_covered",
    "summary": "The research doesn't cover pricing in other countries.",
    "points": [],
    "gaps": "No study asked about international pricing.",
    "suggested_research": ["Interview lab managers outside the UK about pricing."],
}


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        make_reports_folder(Path(self.tmp.name))
        self.library = rl.load_library(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_loads_study_and_findings(self):
        self.assertEqual(list(self.library.studies), ["sample_transcripts"])
        self.assertEqual(list(self.library.findings), ["sample_transcripts/F01", "sample_transcripts/F02"])

    def test_failed_quotes_are_hidden_from_the_agent(self):
        finding = rl.get_finding(self.library, "sample_transcripts/F01")
        quotes = [q["quote"] for q in finding["verified_quotes"]]
        self.assertNotIn("Nobody ever comes to their bookings.", quotes)
        self.assertEqual(finding["quotes_removed_because_they_failed_verification"], 1)
        # F02's only quote was credited to the wrong participant, so none are shown.
        self.assertEqual(rl.get_finding(self.library, "sample_transcripts/F02")["verified_quotes"], [])

    def test_search_finds_relevant_finding(self):
        matches = rl.search_findings(self.library, "maintenance windows")["matches"]
        self.assertEqual(matches[0]["finding_id"], "sample_transcripts/F02")

    def test_unknown_finding_is_an_error_not_a_crash(self):
        self.assertIn("error", rl.get_finding(self.library, "nope/F01"))

    def test_answer_checks(self):
        checks = rl.check_answer(GOOD_SUBMIT, self.library)
        point = checks["points"][0]
        self.assertEqual([f["ok"] for f in point["findings"]], [True, False])
        self.assertEqual(
            [q["status"] for q in point["quotes"]],
            [rl.CITATION_OK, rl.QUOTE_NOT_IN_FINDING, rl.WRONG_PARTICIPANT],
        )
        self.assertEqual((checks["failures"], checks["total"]), (3, 5))

    def test_report_without_study_info_is_skipped_with_a_warning(self):
        folder = Path(self.tmp.name)
        (folder / "findings_2026-01-01_000000_details.json").write_text("{}")
        library = rl.load_library(folder)
        self.assertEqual(library.studies, {})
        self.assertIn("doesn't say which transcripts", library.warnings[0])


class AgentEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "reports").mkdir()
        make_reports_folder(self.dir / "reports")

    def tearDown(self):
        self.tmp.cleanup()

    def run_chat(self, script, typed_input):
        api = FakeAPI(script)
        try:
            env = {**os.environ, "ANTHROPIC_API_KEY": "fake-key", "ANTHROPIC_BASE_URL": api.url}
            result = subprocess.run(
                [sys.executable, str(ROOT / "ask_research.py"), "--reports", "reports", "--no-open"],
                cwd=self.dir, env=env, input=typed_input, capture_output=True, text=True, timeout=60,
            )
        finally:
            api.close()
        return result, api

    def saved(self, suffix):
        files = list((self.dir / "answers").glob(f"conversation_*{suffix}"))
        self.assertEqual(len(files), 1)
        return files[0].read_text(encoding="utf-8")

    def test_two_question_chat(self):
        script = [
            # Question 1: two tools at once, then read a finding, then answer.
            (200, message([
                tool_use("t1", "list_studies", {}),
                tool_use("t2", "search_findings", {"query": "unused bookings"}),
            ], "tool_use")),
            (200, message([tool_use("t3", "get_finding", {"finding_id": "sample_transcripts/F01"})], "tool_use")),
            (200, message([tool_use("t4", "submit_answer", GOOD_SUBMIT)], "tool_use")),
            # Question 2: Claude first answers in plain text, gets nudged, then submits properly.
            (200, message([{"type": "text", "text": "I couldn't find anything."}], "end_turn")),
            (200, message([tool_use("t5", "submit_answer", NOT_COVERED_SUBMIT)], "tool_use")),
        ]
        result, api = self.run_chat(script, "What about unused bookings?\nAny pricing research abroad?\nquit\n")
        self.assertEqual(result.returncode, 0, result.stderr)

        # Tool calls are shown as they happen, with a summary per answer.
        self.assertIn("🔧 list_studies() → 1 studies, 2 findings", result.stdout)
        self.assertIn("🔧 get_finding(finding_id='sample_transcripts/F01')", result.stdout)
        self.assertIn("Tools used: list_studies, search_findings, get_finding", result.stdout)
        self.assertIn("⚠ 3 of 5 citations failed the check", result.stdout)
        self.assertIn("Not covered by the research", result.stdout)
        self.assertIn("Interview lab managers outside the UK", result.stdout)

        # Both parallel tool results went back together, in one message.
        second = api.requests[1]["messages"][-1]["content"]
        self.assertEqual([b["tool_use_id"] for b in second], ["t1", "t2"])
        # Question 2's message starts with the result owed for question 1's submit_answer.
        q2 = api.requests[3]["messages"][-1]["content"]
        self.assertEqual(q2[0]["tool_use_id"], "t4")
        self.assertEqual(q2[1]["text"], "Any pricing research abroad?")
        # History is only ever added to: each request starts with everything the previous one sent.
        for earlier, later in zip(api.requests, api.requests[1:]):
            self.assertEqual(later["messages"][:len(earlier["messages"])], earlier["messages"])
        # The plain-text reply got a nudge.
        self.assertIn("submit_answer", api.requests[4]["messages"][-1]["content"][0]["text"])

        conversation = json.loads(self.saved(".json"))
        self.assertEqual(len(conversation["turns"]), 2)
        self.assertEqual(conversation["turns"][0]["answer"], GOOD_SUBMIT)

        page = self.saved(".html")
        for expected in [
            "✓ Verified in report", "✕ Not in the report", "⚠ Wrong participant",
            "sample_transcripts/F09 · not in the reports", "3 of 5 citations failed the check",
            "Not covered by the research", "Worth studying", "How this was answered: 3 tool calls",
            "4 of 6 participants",
        ]:
            self.assertIn(expected, page)

    def test_error_mid_chat_rolls_back_so_chat_continues(self):
        error = {"type": "error", "error": {"type": "invalid_request_error", "message": "Something was wrong."}}
        script = [
            (400, error),
            (200, message([tool_use("t1", "submit_answer", NOT_COVERED_SUBMIT)], "tool_use")),
        ]
        result, api = self.run_chat(script, "First question\nSecond question\nquit\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Something was wrong.", result.stderr)
        # The failed question was removed, so the second request is a clean start.
        messages = api.requests[1]["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"][-1]["text"], "Second question")
        conversation = json.loads(self.saved(".json"))
        self.assertIn("error", conversation["turns"][0])
        self.assertIn("answer", conversation["turns"][1])

    def test_from_json_rebuilds_page_without_api(self):
        script = [(200, message([tool_use("t1", "submit_answer", GOOD_SUBMIT)], "tool_use"))]
        result, _ = self.run_chat(script, "What about unused bookings?\nquit\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        json_file = next((self.dir / "answers").glob("conversation_*.json"))
        json_file.with_suffix(".html").unlink()
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        rebuild = subprocess.run(
            [sys.executable, str(ROOT / "ask_research.py"), "--reports", "reports", "--no-open",
             "--from-json", str(json_file)],
            cwd=self.dir, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(rebuild.returncode, 0, rebuild.stderr)
        self.assertIn("✓ Verified in report", json_file.with_suffix(".html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
