"""The cassette recorder for the gate study: a proxy that forwards model calls unchanged and keeps a copy."""

from __future__ import annotations

import gzip
import http.client
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from patton_lab.cc import fake_upstream, recorder  # noqa: E402

TOOLS = [{"name": "Bash", "description": "run a command", "input_schema": {"type": "object"}}]
SYSTEM = [{"type": "text", "text": "You are a worker.", "cache_control": {"type": "ephemeral"}}]


def body(messages, stream=True, **extra):
    return {"model": "gpt-oss-120b", "max_tokens": 100, "stream": stream, "system": SYSTEM, "tools": TOOLS,
            "messages": messages, **extra}


def post(port, payload, headers=None, path="/v1/messages"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    conn.request("POST", path, raw, {"content-type": "application/json", **(headers or {})})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


SCRIPT = [{"tool": "Bash", "input": {"command": "ls"}, "thinking": "PRIVATE-REASONING-TEXT"},
          {"text": "All done."}]
TURN1 = [{"role": "user", "content": "clean up"}]
TURN2 = TURN1 + [
    {"role": "assistant", "content": [{"type": "thinking", "thinking": "PRIVATE-REASONING-TEXT", "signature": "sig"},
                                      {"type": "tool_use", "id": "toolu_fake_0", "name": "Bash", "input": {"command": "ls"}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_fake_0", "content": "a.csv",
                                  "cache_control": {"type": "ephemeral"}}]}]


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.upstream = fake_upstream.FakeUpstream(SCRIPT)
        up = self.upstream.start()
        self.addCleanup(self.upstream.stop)
        self.cassette = self.dir / "cassettes" / "run.a0.jsonl.gz"
        self.rec = recorder.Recorder(f"http://127.0.0.1:{up}", self.cassette, secrets=["SECRET-VALUE-123"])
        self.port = self.rec.start()
        self.addCleanup(self.rec.stop)

    def cassette_text(self):
        parts = [gzip.decompress(self.cassette.read_bytes()).decode("utf-8")]
        for blob in sorted((self.cassette.parent / "blobs").glob("*")):
            parts.append(gzip.decompress(blob.read_bytes()).decode("utf-8"))
        return "\n".join(parts)


class ForwardingTests(Harness):
    def test_the_worker_gets_the_upstream_bytes_unchanged(self):
        direct_status, direct = post(self.upstream.port, body(TURN1))
        status, via = post(self.port, body(TURN1))
        self.assertEqual((status, via), (direct_status, direct))
        self.assertIn(b"event: message_start", via)

    def test_a_non_streaming_call_is_forwarded_and_recorded(self):
        status, data = post(self.port, body(TURN1, stream=False))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["content"][1]["name"], "Bash")
        entry = recorder.load(self.cassette)[0]
        self.assertEqual(entry["response"]["content"][1], {"type": "tool_use", "id": "toolu_fake_0", "name": "Bash",
                                                           "input": {"command": "ls"}})

    def test_an_upstream_error_status_reaches_the_worker_and_the_cassette(self):
        self.upstream.script = [{"status": 429}]
        status, data = post(self.port, body(TURN1))
        self.assertEqual(status, 429)
        self.assertIn(b"rate_limit", data)
        self.assertEqual(self.rec.calls[0]["status"], 429)
        self.assertEqual(recorder.load(self.cassette)[0]["status"], 429)

    def test_an_unreachable_upstream_is_a_502_and_is_recorded(self):
        self.upstream.stop()
        status, _ = post(self.port, body(TURN1))
        self.assertEqual(status, 502)
        self.assertEqual(self.rec.calls[0]["status"], 502)
        self.assertEqual(recorder.load(self.cassette)[0]["status"], 502)


class CassetteTests(Harness):
    def test_a_stream_is_assembled_into_the_message_the_worker_saw(self):
        post(self.port, body(TURN1))
        entry = recorder.load(self.cassette)[0]
        response = entry["response"]
        self.assertEqual(response["stop_reason"], "tool_use")
        self.assertEqual([b["type"] for b in response["content"]], ["thinking", "tool_use"])
        self.assertEqual(response["content"][1]["input"], {"command": "ls"})
        self.assertGreater(response["usage"]["input_tokens"], 0)
        self.assertGreater(response["usage"]["output_tokens"], 0)
        self.assertEqual(self.rec.calls[0]["input_tokens"], response["usage"]["input_tokens"])

    def test_streamed_usage_comes_from_the_end_of_the_stream(self):
        # Output tokens arrive in message_delta; message_start carries a placeholder of 1.
        self.upstream.usage = {"input_tokens": 500, "output_tokens": 77}
        post(self.port, body(TURN1))
        self.assertEqual((self.rec.calls[0]["input_tokens"], self.rec.calls[0]["output_tokens"]), (500, 77))

    def test_the_upstream_sees_its_own_host_not_the_recorder(self):
        post(self.port, body(TURN1))
        self.assertEqual(self.upstream.requests[-1]["host"], f"127.0.0.1:{self.upstream.port}")

    def test_reasoning_text_and_headers_never_reach_the_cassette(self):
        post(self.port, body(TURN1), headers={"authorization": "Bearer SECRET-HEADER", "x-api-key": "SECRET-KEY"})
        post(self.port, body(TURN2))
        text = self.cassette_text()
        for private in ("PRIVATE-REASONING-TEXT", "SECRET-HEADER", "SECRET-KEY", "sig", "cache_control"):
            self.assertNotIn(private, text)
        entries = recorder.load(self.cassette)
        self.assertEqual(entries[0]["response"]["content"][0], {"type": "thinking", "chars": len("PRIVATE-REASONING-TEXT")})
        self.assertEqual(entries[1]["request"]["messages"][1]["content"][0],
                         {"type": "thinking", "chars": len("PRIVATE-REASONING-TEXT")})

    def test_the_host_user_name_is_replaced_and_the_request_still_rebuilds(self):
        # The worker runs as the host user, so `ls -l` output in a tool result carries that name.
        user = recorder.getpass.getuser()
        listing = TURN1 + [{"role": "assistant", "content": "x"},
                           {"role": "user", "content": f"-rw-r--r-- 1 {user} {user} 9 a.csv"}]
        post(self.port, body(listing))
        self.assertNotIn(user, self.cassette_text())
        self.assertIn("worker worker", self.cassette_text())
        self.assertEqual(len(recorder.load(self.cassette)), 1)

    def test_a_secret_in_a_request_is_not_stored_and_is_reported(self):
        leaky = TURN1 + [{"role": "assistant", "content": "x"}, {"role": "user", "content": "the key is SECRET-VALUE-123"}]
        status, _ = post(self.port, body(leaky))
        self.assertEqual(status, 200, "the call is forwarded; the upstream already sees what the worker sends")
        self.assertEqual(self.rec.leaks, [0])
        self.assertNotIn("SECRET-VALUE-123", self.cassette_text() if self.cassette.exists() else "")

    def test_requests_are_rebuilt_exactly_from_blobs_and_deltas(self):
        post(self.port, body(TURN1))
        post(self.port, body(TURN2))
        raw = [json.loads(line) for line in gzip.decompress(self.cassette.read_bytes()).decode().splitlines()]
        self.assertNotIn("messages", raw[1]["request"], "the second call stores only what it adds")
        self.assertEqual(raw[1]["request"]["append"], recorder.normalize(body(TURN2))["messages"][1:])
        self.assertEqual(len(list((self.cassette.parent / "blobs").glob("*"))), 2, "system and tools stored once")
        entries = recorder.load(self.cassette)
        self.assertEqual([e["request"] for e in entries], [recorder.normalize(body(TURN1)), recorder.normalize(body(TURN2))])

    def test_a_tampered_cassette_fails_to_load(self):
        post(self.port, body(TURN1))
        post(self.port, body(TURN2))
        for blob in (self.cassette.parent / "blobs").glob("*"):
            blob.write_bytes(gzip.compress(b'{"changed": true}'))
        with self.assertRaises(ValueError):
            recorder.load(self.cassette)

    def test_an_edited_message_fails_to_load_even_with_every_blob_intact(self):
        post(self.port, body(TURN1))
        post(self.port, body(TURN2))
        lines = gzip.decompress(self.cassette.read_bytes()).decode().splitlines()
        second = json.loads(lines[1])
        second["request"]["append"][-1]["content"][0]["content"] = "edited"
        lines[1] = json.dumps(second)
        self.cassette.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode()))
        with self.assertRaises(ValueError):
            recorder.load(self.cassette)

    def test_a_missing_base_fails_to_load(self):
        post(self.port, body(TURN1))
        post(self.port, body(TURN2))
        lines = gzip.decompress(self.cassette.read_bytes()).decode().splitlines()
        self.cassette.write_bytes(gzip.compress((lines[1] + "\n").encode()))
        with self.assertRaises(ValueError):
            recorder.load(self.cassette)

    def test_usage_sums_cover_every_call(self):
        post(self.port, body(TURN1))
        post(self.port, body(TURN2))
        total_in, total_out, unknown = self.rec.usage()
        self.assertEqual(total_in, sum(c["input_tokens"] for c in self.rec.calls))
        self.assertEqual(total_out, sum(c["output_tokens"] for c in self.rec.calls))
        self.assertEqual(unknown, 0)

    def test_a_call_with_no_usage_is_counted_as_unknown(self):
        self.upstream.stop()
        post(self.port, body(TURN1))
        self.assertEqual(self.rec.usage()[2], 1)

    def test_tool_uses_come_out_in_call_order(self):
        post(self.port, body(TURN1))
        post(self.port, body(TURN2))
        self.assertEqual(recorder.tool_uses(recorder.load(self.cassette)),
                         [{"id": "toolu_fake_0", "name": "Bash", "input": {"command": "ls"}}])


class FakeUpstreamTests(unittest.TestCase):
    def test_the_step_is_the_number_of_assistant_turns(self):
        up = fake_upstream.FakeUpstream(SCRIPT)
        port = up.start()
        try:
            _, first = post(port, body(TURN1, stream=False))
            _, second = post(port, body(TURN2, stream=False))
            _, untooled = post(port, {"model": "m", "max_tokens": 5, "messages": TURN1})
        finally:
            up.stop()
        self.assertEqual(json.loads(first)["content"][-1]["type"], "tool_use")
        self.assertEqual(json.loads(second)["content"][-1], {"type": "text", "text": "All done."})
        self.assertEqual(json.loads(untooled)["content"][-1]["type"], "text", "a call without tools gets text")

    def test_past_the_end_of_the_script_it_answers_text(self):
        up = fake_upstream.FakeUpstream([{"text": "only"}])
        port = up.start()
        try:
            _, data = post(port, body(TURN2, stream=False))
        finally:
            up.stop()
        self.assertEqual(json.loads(data)["stop_reason"], "end_turn")


if __name__ == "__main__":
    unittest.main()
