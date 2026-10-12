"""Tests for lab/patton_lab/client.py against a real local HTTP server.

The server returns scripted responses, so these tests exercise the actual
urllib path: auth header, retries, quota errors, pacing, and the cassette.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import gzip
import json
import os
import sys
import threading
from tempfile import TemporaryDirectory
import unittest

LAB_ROOT = Path(__file__).resolve().parents[1] / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab import client as client_mod  # noqa: E402
from patton_lab.cassette import Cassette, CassetteMiss  # noqa: E402
from patton_lab.cost import Meter, PRICES  # noqa: E402

FAKE_KEY = "gsk_" + "TESTKEY" * 6
KEY_ENV = "PATTON_LAB_TEST_KEY"


def ok_body(content="done", tool_calls=None, reasoning=None, prompt=100, completion=20):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    if reasoning is not None:
        message["reasoning"] = reasoning
    return {
        "model": "openai/gpt-oss-120b",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion},
    }


class ScriptedServer:
    """A local server that replies from a queue of (status, headers, body)."""

    def __init__(self):
        self.queue = []
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                outer.requests.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"), "body": json.loads(raw)}
                )
                status, headers, body = outer.queue.pop(0) if outer.queue else (500, {}, {"error": "empty"})
                data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        self.server = ScriptedServer()
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ[KEY_ENV] = FAKE_KEY
        self.sleeps = []
        self.provider = client_mod.Provider(
            name="groq", base_url=self.server.base_url, key_env=KEY_ENV, supports_reasoning_effort=True
        )
        self.meter = Meter(self.root / "spend.json")

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()
        os.environ.pop(KEY_ENV, None)

    def make_client(self, mode="record"):
        cassette = Cassette(self.root / "c.jsonl.gz", mode)
        transport = client_mod.HttpTransport(self.provider, sleep=self.sleeps.append)
        return client_mod.Client(cassette, {"groq": transport}, self.meter), cassette

    def chat(self, client, messages=None, salt="job|t0"):
        return client.chat(
            self.provider,
            "openai/gpt-oss-120b",
            messages or [{"role": "user", "content": "hi"}],
            tools=[],
            salt=salt,
        )


class ClientTests(ClientTestCase):
    def test_parses_tool_calls_and_charges_usage(self):
        call = {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}
        self.server.queue.append((200, {}, ok_body(content=None, tool_calls=[call], prompt=1_000_000, completion=0)))
        client, _ = self.make_client()
        completion = self.chat(client)
        self.assertEqual(completion.message["tool_calls"][0]["function"]["name"], "list_dir")
        self.assertFalse(completion.from_cassette)
        self.assertAlmostEqual(self.meter.spent("groq"), 0.15)
        self.assertEqual(self.server.requests[0]["auth"], f"Bearer {FAKE_KEY}")
        self.assertEqual(self.server.requests[0]["path"], "/v1/chat/completions")

    def test_sends_sampling_parameters(self):
        self.server.queue.append((200, {}, ok_body()))
        client, _ = self.make_client()
        self.chat(client)
        body = self.server.requests[0]["body"]
        self.assertEqual(body["temperature"], 1.0)
        self.assertEqual(body["max_tokens"], 2000)
        self.assertEqual(body["reasoning_effort"], "medium")
        self.assertNotIn("seed", body)

    def test_no_reasoning_effort_for_providers_without_it(self):
        self.provider = client_mod.Provider(
            name="openai", base_url=self.server.base_url, key_env=KEY_ENV, supports_reasoning_effort=False
        )
        self.server.queue.append((200, {}, ok_body()))
        cassette = Cassette(self.root / "c.jsonl.gz", "record")
        client = client_mod.Client(cassette, {"openai": client_mod.HttpTransport(self.provider)}, self.meter)
        client.chat(self.provider, "gpt-4.1-mini", [{"role": "user", "content": "hi"}], tools=[], salt="s")
        self.assertNotIn("reasoning_effort", self.server.requests[0]["body"])

    def test_reasoning_is_stripped_from_history_but_kept_in_the_reply(self):
        self.server.queue.append((200, {}, ok_body(reasoning="thinking about it")))
        client, _ = self.make_client()
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "earlier", "reasoning": "old private thoughts"},
            {"role": "user", "content": "again"},
        ]
        completion = self.chat(client, messages=history)
        sent = self.server.requests[0]["body"]["messages"]
        self.assertNotIn("reasoning", sent[1])
        self.assertIn("reasoning", history[1], "the caller's history must not be mutated")
        self.assertEqual(completion.message["reasoning"], "thinking about it")

    def test_cassette_hit_makes_no_request_and_no_charge(self):
        self.server.queue.append((200, {}, ok_body()))
        client, _ = self.make_client()
        self.chat(client)
        spent = self.meter.spent()
        replay_client, cassette = self.make_client("replay")
        completion = self.chat(replay_client)
        self.assertTrue(completion.from_cassette)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.meter.spent(), spent)
        cassette.assert_no_misses()

    def test_replay_miss_raises_and_is_recorded(self):
        self.server.queue.append((200, {}, ok_body()))
        client, _ = self.make_client()
        self.chat(client, salt="a")
        replay_client, cassette = self.make_client("replay")
        with self.assertRaises(CassetteMiss):
            self.chat(replay_client, salt="b")
        self.assertEqual(len(cassette.misses), 1)
        self.assertEqual(len(self.server.requests), 1)

    def test_key_never_reaches_the_cassette(self):
        self.server.queue.append((200, {}, ok_body()))
        client, _ = self.make_client()
        self.chat(client)
        with gzip.open(self.root / "c.jsonl.gz", "rt", encoding="utf-8") as handle:
            self.assertNotIn(FAKE_KEY, handle.read())

    def test_rate_limit_waits_then_succeeds(self):
        self.server.queue.append((429, {"retry-after": "2"}, {"error": {"message": "slow down"}}))
        self.server.queue.append((200, {}, ok_body()))
        client, _ = self.make_client()
        self.chat(client)
        self.assertEqual(self.sleeps, [2.0])

    def test_six_rate_limits_raise(self):
        for _ in range(6):
            self.server.queue.append((429, {"retry-after": "1"}, {"error": {"message": "slow down"}}))
        client, _ = self.make_client()
        with self.assertRaises(client_mod.RateLimitError):
            self.chat(client)
        self.assertEqual(len(self.sleeps), 5)
        self.assertEqual(self.meter.spent(), 0.0)

    def test_auth_error_stops_without_retry_or_charge(self):
        self.server.queue.append((401, {}, {"error": {"message": "invalid api key"}}))
        client, _ = self.make_client()
        with self.assertRaises(client_mod.AuthError) as ctx:
            self.chat(client)
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.meter.spent(), 0.0)

    def test_quota_error_on_429_body(self):
        self.server.queue.append((429, {}, {"error": {"code": "insufficient_quota", "message": "no credit"}}))
        client, _ = self.make_client()
        with self.assertRaises(client_mod.QuotaError):
            self.chat(client)
        self.assertEqual(self.sleeps, [])

    def test_server_errors_retry_three_times(self):
        for _ in range(4):
            self.server.queue.append((503, {}, {"error": "busy"}))
        client, _ = self.make_client()
        with self.assertRaises(client_mod.ProviderError):
            self.chat(client)
        self.assertEqual(self.sleeps, [2.0, 4.0, 8.0])

    def test_other_client_errors_do_not_retry(self):
        self.server.queue.append((400, {}, {"error": {"message": "bad tool schema"}}))
        client, _ = self.make_client()
        with self.assertRaises(client_mod.ProviderError) as ctx:
            self.chat(client)
        self.assertIn("bad tool schema", str(ctx.exception))
        self.assertEqual(len(self.server.requests), 1)

    def test_missing_key_env_raises_auth_error(self):
        os.environ.pop(KEY_ENV)
        with self.assertRaises(client_mod.AuthError):
            client_mod.HttpTransport(self.provider)

    def test_pacing_waits_for_the_token_window(self):
        headers = {"x-ratelimit-remaining-tokens": "10", "x-ratelimit-reset-tokens": "1.5s"}
        self.server.queue.append((200, headers, ok_body()))
        self.server.queue.append((200, {}, ok_body()))
        client, _ = self.make_client()
        self.chat(client, salt="first")
        self.chat(client, salt="second")
        self.assertEqual(self.sleeps, [1.5])

    def test_meter_refuses_before_any_request(self):
        ledger = self.root / "spend.json"
        ledger.write_text('{"version": 1, "spent": {"groq": 9.0}, "calls": 1}', encoding="utf-8")
        self.meter = Meter(ledger)
        client, _ = self.make_client()
        from patton_lab.cost import BudgetExceeded

        with self.assertRaises(BudgetExceeded):
            self.chat(client)
        self.assertEqual(self.server.requests, [])

    def test_unpriced_model_is_refused_before_any_request(self):
        client, _ = self.make_client()
        with self.assertRaises(KeyError):
            client.chat(self.provider, "openai/gpt-oss-20b", [{"role": "user", "content": "x"}], tools=[], salt="s")
        self.assertEqual(self.server.requests, [])
        self.assertNotIn(("groq", "openai/gpt-oss-20b"), PRICES)


class ConservativeChargeTests(ClientTestCase):
    def bound(self, client):
        from patton_lab.cost import call_bound, estimate_tokens
        payload = {"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": "hi"}],
                   "max_tokens": 2000, "temperature": 1.0, "reasoning_effort": "medium"}
        return call_bound("groq", "openai/gpt-oss-120b", estimate_tokens(payload), 2000)

    def test_missing_usage_is_charged_at_the_bound(self):
        body = ok_body()
        del body["usage"]
        self.server.queue.append((200, {}, body))
        client, _ = self.make_client()
        completion = self.chat(client)
        self.assertTrue(completion.usage["estimated"])
        self.assertAlmostEqual(self.meter.spent(), self.bound(client))

    def test_null_token_counts_are_charged_at_the_bound(self):
        body = ok_body()
        body["usage"] = {"prompt_tokens": None, "completion_tokens": 3}
        self.server.queue.append((200, {}, body))
        client, _ = self.make_client()
        self.chat(client)
        self.assertAlmostEqual(self.meter.spent(), self.bound(client))

    def test_unreadable_200_body_is_retried_and_charged(self):
        self.server.queue.append((200, {}, "not an object"))
        self.server.queue.append((200, {}, ok_body(prompt=1, completion=0)))
        client, _ = self.make_client()
        self.chat(client)
        self.assertEqual(self.sleeps, [2.0])
        self.assertAlmostEqual(self.meter.spent(), self.bound(client) + 0.15 / 1e6)

    def test_zero_prompt_tokens_count_as_missing_usage(self):
        self.server.queue.append((200, {}, ok_body(prompt=0, completion=0)))
        client, _ = self.make_client()
        self.chat(client)
        self.assertAlmostEqual(self.meter.spent(), self.bound(client))

    def test_billed_attempts_are_charged_even_when_the_retries_run_out(self):
        for _ in range(4):
            self.server.queue.append((502, {}, {"error": "bad gateway"}))
        client, _ = self.make_client()
        with self.assertRaises(client_mod.ProviderError):
            self.chat(client)
        self.assertAlmostEqual(self.meter.spent(), 4 * self.bound(client))

    def test_model_text_cannot_raise_a_quota_error(self):
        error = {"error": {"message": "Failed to call a function.", "code": "tool_use_failed",
                           "failed_generation": "the quota was exceeded, insufficient_quota"}}
        self.server.queue.append((400, {}, error))
        client, _ = self.make_client()
        self.assertEqual(self.chat(client).finish_reason, "tool_use_failed")
        self.assertGreater(self.meter.spent(), 0)

    def test_body_without_choices_is_a_provider_error_and_still_charged(self):
        self.server.queue.append((200, {}, {"model": "m", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
        client, _ = self.make_client()
        with self.assertRaises(client_mod.ProviderError):
            self.chat(client)
        self.assertGreater(self.meter.spent(), 0)


class ToolUseFailedTests(ClientTestCase):
    def test_malformed_tool_call_comes_back_as_a_recorded_completion(self):
        error = {"error": {"message": "Failed to call a function.", "type": "invalid_request_error",
                           "code": "tool_use_failed", "failed_generation": "<|call|>read_file{bad json"}}
        self.server.queue.append((400, {}, error))
        client, _ = self.make_client()
        completion = self.chat(client)
        self.assertEqual(completion.finish_reason, "tool_use_failed")
        self.assertIn("bad json", completion.message["tool_use_failed"]["failed_generation"])
        self.assertGreater(self.meter.spent(), 0)
        replay_client, cassette = self.make_client("replay")
        again = self.chat(replay_client)
        self.assertEqual(again.finish_reason, "tool_use_failed")
        cassette.assert_no_misses()


class ParseFailureTests(ClientTestCase):
    def test_unparseable_model_output_is_a_recorded_turn(self):
        error = {"error": {"message": "Parsing failed. The model generated output that could not be parsed.",
                           "type": "invalid_request_error", "code": "output_parse_failed",
                           "failed_generation": "half a thought"}}
        self.server.queue.append((400, {}, error))
        client, _ = self.make_client()
        completion = self.chat(client)
        self.assertEqual(completion.finish_reason, "tool_use_failed")
        self.assertEqual(completion.message["tool_use_failed"]["code"], "output_parse_failed")
        self.assertGreater(self.meter.spent(), 0)

    def test_any_400_with_a_failed_generation_counts(self):
        error = {"error": {"message": "something new", "code": "brand_new_code", "failed_generation": "x"}}
        self.server.queue.append((400, {}, error))
        client, _ = self.make_client()
        self.assertEqual(self.chat(client).finish_reason, "tool_use_failed")

    def test_a_plain_400_still_stops(self):
        self.server.queue.append((400, {}, {"error": {"message": "bad schema", "code": "invalid"}}))
        client, _ = self.make_client()
        with self.assertRaises(client_mod.ProviderError):
            self.chat(client)


class RedactionTests(ClientTestCase):
    def test_masked_key_in_an_auth_error_is_removed(self):
        body = {"error": {"message": "Incorrect API key provided: sk-proj-ab********************wxyz. Find your key."}}
        self.server.queue.append((401, {}, body))
        client, _ = self.make_client()
        with self.assertRaises(client_mod.AuthError) as ctx:
            self.chat(client)
        self.assertNotIn("wxyz", str(ctx.exception))
        self.assertIn("[redacted]", str(ctx.exception))

    def test_quota_is_read_from_provider_fields_only(self):
        self.assertTrue(client_mod._is_quota_error({"code": "insufficient_quota"}))
        self.assertTrue(client_mod._is_quota_error({"message": "You exceeded your current quota"}))
        self.assertFalse(client_mod._is_quota_error({"code": "x", "failed_generation": "insufficient_quota exceeded"}))

    def test_redact(self):
        self.assertEqual(client_mod.redact("key gsk_ABCDEF123 end"), "key [redacted] end")


class DurationTests(unittest.TestCase):
    def test_parse_reset_durations(self):
        parse = client_mod.parse_duration
        self.assertAlmostEqual(parse("89ms"), 0.089)
        self.assertAlmostEqual(parse("7.66s"), 7.66)
        self.assertAlmostEqual(parse("1m2.5s"), 62.5)
        self.assertAlmostEqual(parse("2"), 2.0)
        self.assertIsNone(parse(""))
        self.assertIsNone(parse("soon"))


if __name__ == "__main__":
    unittest.main()
