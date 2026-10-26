"""The fleet recorder: what reaches upstream, what the harness gets back, and what is charged.

Upstreams are local scripted servers; nothing here reaches a paid API.
"""

from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab import spend  # noqa: E402
from patton_lab.fleet import money, recorder  # noqa: E402
from patton_lab.harness import fake_chat  # noqa: E402


class Lab:
    def __init__(self, spent=0.0):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        (self.path / "results").mkdir()
        (self.path / "results" / "spend.json").write_text(json.dumps({"spent": {"groq": spent}}))
        self.ledger = self.path / spend.LINE_LEDGERS["fleet"]

    def meter(self, cap):
        return money.Meter(self.path, self.ledger, cap)


class Scripted:
    """An upstream that answers with fixed statuses in turn, then succeeds; it counts the requests it got. A status
    may be a (status, body) pair."""

    def __init__(self, statuses=()):
        self.statuses, self.requests = list(statuses), []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                outer.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                code = outer.statuses.pop(0) if outer.statuses else 200
                code, body = code if isinstance(code, tuple) else (code, None)
                if body is None:
                    body = fake_chat.completion({"text": "ok"}, 1) if code == 200 else {"error": {"message": "x"}}
                data = json.dumps(body).encode()
                self.send_response(code)
                if code == 429:
                    self.send_header("retry-after", "0")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def post(port, path, body):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.lab = Lab()
        self.addCleanup(self.lab.tmp.cleanup)

    def start(self, upstream_url, cap=1.0, analyst_url=None):
        rec = recorder.Recorder(upstream_url, self.lab.meter(cap), "run1", "gpt-oss-120b", analyst_url,
                                sleep=lambda s: None)
        port = rec.start()
        self.addCleanup(rec.stop)
        return rec, port

    def test_a_streamed_tool_call_comes_back_whole_and_is_charged(self):
        fake = fake_chat.FakeChat([{"tool": "bash", "args": {"command": "ls"}}])
        self.addCleanup(fake.stop)
        rec, port = self.start(f"http://127.0.0.1:{fake.start()}")
        status, text = post(port, "/v1/chat/completions", {"model": "anything", "stream": True,
                                                            "stream_options": {"include_usage": True},
                                                            "messages": [{"role": "user", "content": "go"}]})
        self.assertEqual(status, 200)
        events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]
        call = events[0]["choices"][0]["delta"]["tool_calls"][0]
        self.assertEqual((call["function"]["name"], json.loads(call["function"]["arguments"])), ("bash", {"command": "ls"}))
        self.assertEqual(events[-1]["choices"][0]["finish_reason"], "tool_calls")
        self.assertTrue(text.rstrip().endswith("data: [DONE]"))
        sent = fake.requests[0]
        self.assertEqual((sent["model"], "stream" in sent, "stream_options" in sent, sent["max_tokens"]),
                         ("gpt-oss-120b", False, False, recorder.MAX_OUT))
        charged = [json.loads(l) for l in self.lab.ledger.read_text().splitlines()]
        self.assertEqual(len(charged), 1)
        self.assertAlmostEqual(charged[0]["usd"], rec.usd)
        self.assertGreater(rec.usd, 0)
        self.assertEqual(rec.calls[0]["route"], "worker")

    def test_reasoning_content_goes_upstream_under_the_name_groq_takes(self):
        # Cline, OpenCode and goose send the reasoning back as reasoning_content, which Groq refuses with a 400
        fake = fake_chat.FakeChat([{"text": "done"}], groq=True)
        self.addCleanup(fake.stop)
        rec, port = self.start(f"http://127.0.0.1:{fake.start()}")
        history = [{"role": "user", "content": "go"},
                   {"role": "assistant", "content": None, "reasoning_content": "think a"},
                   {"role": "assistant", "content": "x", "reasoning": "kept", "reasoning_content": "dropped"},
                   {"role": "user", "content": "more"}]
        self.assertEqual(post(port, "/v1/chat/completions", {"messages": history})[0], 200)
        sent = fake.requests[0]["messages"]
        self.assertEqual([m.get("reasoning") for m in sent], [None, "think a", "kept", None])
        self.assertFalse(any("reasoning_content" in m for m in sent))
        self.assertEqual(history[1], {"role": "assistant", "content": None, "reasoning_content": "think a"})
        self.assertIsNone(rec.stopped)

    def test_a_request_groq_refuses_stops_the_run_so_it_is_never_stored(self):
        refused = (400, {"error": {"message": "'messages.2' : property 'reasoning_content' is unsupported",
                                   "type": "invalid_request_error"}})
        upstream = Scripted([refused])
        self.addCleanup(upstream.close)
        rec, port = self.start(upstream.url)
        self.assertNotEqual(post(port, "/v1/chat/completions", {"messages": []})[0], 200)
        self.assertIn("400", rec.stopped or "")
        self.assertEqual(post(port, "/v1/chat/completions", {"messages": []})[0], 503)
        self.assertEqual(len(upstream.requests), 1)

    def test_a_malformed_tool_call_is_the_models_and_the_run_goes_on(self):
        failed = (400, {"error": {"message": "Failed to parse tool call arguments as JSON",
                                  "type": "invalid_request_error", "code": "tool_use_failed"}})
        upstream = Scripted([failed] * 3)
        self.addCleanup(upstream.close)
        rec, port = self.start(upstream.url)
        status, text = post(port, "/v1/chat/completions", {"messages": []})
        self.assertEqual(status, 400)
        self.assertIn("tool_use_failed", text)
        self.assertIsNone(rec.stopped)
        self.assertEqual(post(port, "/v1/chat/completions", {"messages": []})[0], 200)
        self.assertEqual(len(upstream.requests), 4)

    def test_a_model_fault_is_drawn_again_twice_before_the_harness_sees_it(self):
        failed = (400, {"error": {"message": "x", "type": "invalid_request_error", "code": "tool_use_failed",
                                  "failed_generation": "{}"}})
        upstream = Scripted([failed, failed])
        self.addCleanup(upstream.close)
        rec, port = self.start(upstream.url)
        self.assertEqual(post(port, "/v1/chat/completions", {"messages": []})[0], 200)
        self.assertEqual([a["status"] for a in rec.calls[0]["attempts"]], [400, 400, 200])
        upstream = Scripted([failed, failed, failed])
        self.addCleanup(upstream.close)
        rec, port = self.start(upstream.url)
        self.assertEqual(post(port, "/v1/chat/completions", {"messages": []})[0], 400)
        self.assertEqual(len(rec.calls[0]["attempts"]), 1 + recorder.MODEL_FAULT_RETRIES)
        self.assertIsNone(rec.stopped)

    def test_output_groq_could_not_parse_is_the_models_and_the_run_goes_on(self):
        # Groq names the model's own output in failed_generation; the P1 pilot met this one on Claude Code
        failed = (400, {"error": {"message": "Parsing failed. The model generated output that could not be parsed. "
                                             "Please adjust your prompt. See 'failed_generation' for more details.",
                                  "type": "invalid_request_error", "failed_generation": "<|channel|>x"}})
        upstream = Scripted([failed] * 3)
        self.addCleanup(upstream.close)
        rec, port = self.start(upstream.url)
        self.assertEqual(post(port, "/v1/chat/completions", {"messages": []})[0], 400)
        self.assertIsNone(rec.stopped)
        self.assertEqual(post(port, "/v1/chat/completions", {"messages": []})[0], 200)

    def test_the_groq_fake_answers_with_reasoning_and_refuses_reasoning_content(self):
        fake = fake_chat.FakeChat([{"text": "done"}], groq=True)
        self.addCleanup(fake.stop)
        port = fake.start()
        status, text = post(port, "/v1/chat/completions", {"messages": [{"role": "user", "content": "go"}]})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(text)["choices"][0]["message"]["reasoning"])
        status, text = post(port, "/v1/chat/completions", {"stream": True, "messages": []})
        self.assertIn('"reasoning"', text)
        status, text = post(port, "/v1/chat/completions", {"messages": [
            {"role": "assistant", "content": "x", "reasoning_content": "r"}]})
        self.assertEqual(status, 400)
        self.assertIn("reasoning_content", text)
        plain = fake_chat.FakeChat([{"text": "done"}])
        self.addCleanup(plain.stop)
        status, text = post(plain.start(), "/v1/chat/completions", {"messages": []})
        self.assertNotIn("reasoning", text)

    def test_the_analyst_route_goes_to_its_own_upstream(self):
        worker, analyst = Scripted(), Scripted()
        self.addCleanup(worker.close)
        self.addCleanup(analyst.close)
        rec, port = self.start(worker.url, analyst_url=analyst.url)
        self.assertEqual(post(port, "/t1/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]})[0], 200)
        self.assertEqual((len(worker.requests), len(analyst.requests)), (0, 1))
        self.assertEqual(rec.calls[0]["route"], "t1")

    def test_a_call_that_could_pass_the_cap_never_leaves(self):
        upstream = Scripted()
        self.addCleanup(upstream.close)
        rec, port = self.start(upstream.url, cap=0.0001)
        status, _ = post(port, "/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]})
        self.assertEqual((status, len(upstream.requests)), (503, 0))
        self.assertIn("cap", rec.stopped)
        self.assertEqual(post(port, "/v1/chat/completions", {"messages": []})[0], 503)
        self.assertEqual(len(upstream.requests), 0)

    def test_the_lab_wide_cap_counts_every_ledger(self):
        lab = Lab(spent=spend.GLOBAL_CAP - 0.0001)
        self.addCleanup(lab.tmp.cleanup)
        upstream = Scripted()
        self.addCleanup(upstream.close)
        rec = recorder.Recorder(upstream.url, lab.meter(100.0), "r", "gpt-oss-120b", sleep=lambda s: None)
        port = rec.start()
        self.addCleanup(rec.stop)
        self.assertEqual(post(port, "/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]})[0], 503)
        self.assertEqual(len(upstream.requests), 0)

    def test_a_429_is_waited_out_and_a_failing_upstream_stops_the_run(self):
        busy = Scripted([429, 429])
        self.addCleanup(busy.close)
        rec, port = self.start(busy.url)
        self.assertEqual(post(port, "/v1/chat/completions", {"messages": []})[0], 200)
        self.assertEqual(len(rec.calls[0]["attempts"]), 3)
        self.assertEqual(rec.calls[0]["usd"], rec.calls[0]["attempts"][-1]["usd"])
        down = Scripted([500, 500, 500, 500])
        self.addCleanup(down.close)
        rec, port = self.start(down.url)
        self.assertEqual(post(port, "/v1/chat/completions", {"messages": []})[0], 502)
        self.assertEqual(len(down.requests), 3)
        self.assertIn("upstream 500", rec.stopped)

    def test_an_anthropic_tool_call_streams_back_whole_and_is_charged(self):
        from patton_lab.cc.fake_upstream import FakeUpstream
        fake = FakeUpstream([{"tool": "Bash", "input": {"command": "ls"}, "thinking": "look first"}])
        rec, port = self.start(f"http://127.0.0.1:{fake.start()}")
        self.addCleanup(fake.stop)
        status, text = post(port, "/v1/messages", {"model": "claude-x", "stream": True, "max_tokens": 64000,
                                                   "tools": [{"name": "Bash"}],
                                                   "messages": [{"role": "user", "content": "go"}]})
        self.assertEqual(status, 200)
        events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
        kinds = [e["type"] for e in events]
        self.assertEqual((kinds[0], kinds[-1]), ("message_start", "message_stop"))
        tool = next(e for e in events if e["type"] == "content_block_start" and e["content_block"]["type"] == "tool_use")
        args = next(e for e in events if e.get("index") == tool["index"] and e["type"] == "content_block_delta")
        self.assertEqual((tool["content_block"]["name"], json.loads(args["delta"]["partial_json"])), ("Bash", {"command": "ls"}))
        sent = fake.requests[0]["body"]
        self.assertEqual(fake.requests[0]["path"], "/v1/messages")
        self.assertEqual((sent["model"], "stream" in sent, sent["max_tokens"]), ("gpt-oss-120b", False, recorder.MAX_OUT))
        self.assertEqual(rec.calls[0]["wire"], "anthropic")
        self.assertGreater(rec.usd, 0)
        self.assertEqual(post(port, "/v1/messages/count_tokens", {"messages": []})[0], 200)
        self.assertEqual(len(fake.requests), 1)

    def test_a_large_max_tokens_is_cut_to_the_cap(self):
        upstream = Scripted()
        self.addCleanup(upstream.close)
        rec, port = self.start(upstream.url)
        post(port, "/v1/chat/completions", {"messages": [], "max_completion_tokens": 10 ** 6})
        self.assertEqual(upstream.requests[0]["max_completion_tokens"], recorder.MAX_OUT)


if __name__ == "__main__":
    unittest.main()
