"""End-to-end tests using a fake Claude API running on this computer.

No API key or internet is needed and nothing is billed. The fake server sends
back a streamed response in the same shape as the real API, so these tests
exercise the real Anthropic library and the real save/parse/report code.

Run with:  python -m unittest discover tests
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOOD_ANSWER = (ROOT / "tests" / "fake_claude_answer.json").read_text(encoding="utf-8")


def sse_body(answer_text: str) -> bytes:
    """Build a streamed Messages API response whose answer is `answer_text`."""
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_fake", "type": "message", "role": "assistant", "content": [],
            "model": "fake-model-for-tests", "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 1},
        }}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": answer_text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 50}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


class FakeAPI:
    """A tiny local web server that answers every request with `answer_text`."""

    def __init__(self, answer_text: str):
        body = sse_body(answer_text)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length", 0)))
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("request-id", "req_fake_123")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def run_tool(api: FakeAPI, out_dir: Path, *extra):
    env = {**os.environ, "ANTHROPIC_API_KEY": "fake-key", "ANTHROPIC_BASE_URL": api.url}
    return subprocess.run(
        [sys.executable, str(ROOT / "research_synth.py"), str(ROOT / "sample_transcripts"),
         "--out", str(out_dir), "--no-open", *extra],
        cwd=out_dir, env=env, capture_output=True, text=True, timeout=60,
    )


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_with_answer(self, answer_text: str, *extra):
        api = FakeAPI(answer_text)
        try:
            return run_tool(api, self.out, *extra)
        finally:
            api.close()

    def saved_answer(self) -> Path:
        answers = [p for p in self.out.glob("findings_*.json") if not p.name.endswith("_details.json")]
        self.assertEqual(len(answers), 1, f"expected one saved answer, found {answers}")
        return answers[0]

    def test_good_answer_is_saved_and_reported(self):
        result = self.run_with_answer(GOOD_ANSWER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.saved_answer().read_text(encoding="utf-8"), GOOD_ANSWER)

        details = json.loads(next(self.out.glob("findings_*_details.json")).read_text(encoding="utf-8"))
        self.assertEqual(details["request_id"], "req_fake_123")

        report = next(self.out.glob("findings_*.md")).read_text(encoding="utf-8")
        self.assertIn("**Model:** fake-model-for-tests", report)
        self.assertIn("4 of 6", report)
        self.assertIn("NOT FOUND IN TRANSCRIPTS", report)

        page = next(self.out.glob("findings_*.html")).read_text(encoding="utf-8")
        self.assertIn("4 of 6 participants", page)
        self.assertIn("Single source", page)
        self.assertIn("✓ Verified", page)
        self.assertIn("✕ Not found", page)
        self.assertIn("⚠ Wrong participant", page)
        self.assertIn("fake-model-for-tests", page)

    def test_invalid_json_is_still_saved(self):
        result = self.run_with_answer("this is not JSON")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.saved_answer().read_text(encoding="utf-8"), "this is not JSON")
        self.assertIn("was saved to", result.stderr)

    def test_report_crash_keeps_answer_and_explains_retry(self):
        broken = json.dumps({"findings": [{"headline": "Missing every other field"}]})
        result = self.run_with_answer(broken)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.saved_answer().read_text(encoding="utf-8"), broken)
        self.assertIn("--from-json", result.stderr)
        self.assertEqual(list(self.out.glob("findings_*.md")), [])
        self.assertEqual(list(self.out.glob("findings_*.html")), [])

    def test_from_json_rebuilds_report_without_api(self):
        first = self.run_with_answer(GOOD_ANSWER)
        self.assertEqual(first.returncode, 0, first.stderr)
        answer = self.saved_answer()
        for report in [*self.out.glob("findings_*.md"), *self.out.glob("findings_*.html")]:
            report.unlink()

        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        rerun = subprocess.run(
            [sys.executable, str(ROOT / "research_synth.py"), str(ROOT / "sample_transcripts"),
             "--out", str(self.out), "--from-json", str(answer), "--no-open"],
            cwd=self.out, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        # The rebuilt reports are named after the answer file, not a new timestamp.
        report = (self.out / f"{answer.stem}.md").read_text(encoding="utf-8")
        self.assertIn("**Model:** fake-model-for-tests", report)
        page = (self.out / f"{answer.stem}.html").read_text(encoding="utf-8")
        self.assertIn("4 of 6 participants", page)


if __name__ == "__main__":
    unittest.main()
