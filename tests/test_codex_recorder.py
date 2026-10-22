"""The Codex recorder: relays Responses calls, keeps each in a cassette, and keeps credentials out of it."""

from pathlib import Path
import base64
import gzip
import http.client
import json
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.codex import recorder  # noqa: E402
from patton_lab.codex.fake_upstream import FakeUpstream  # noqa: E402

TOKEN = "eyJ-access-token-that-must-never-be-stored-0123456789"
ACCOUNT = "acct-1234567890abcdef"


def body(n_items: int = 2, extra: str = "") -> dict:
    items = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"turn {i} {extra}"}]}
             for i in range(n_items)]
    items.append({"type": "reasoning", "id": "rs_0", "summary": [], "encrypted_content": "gAAAA" + "z" * 400})
    return {"model": "gpt-6.1-sol", "instructions": "be careful", "input": items, "tools": [{"name": "exec"}],
            "store": False, "stream": True}


class RecorderCase(unittest.TestCase):
    steps = [{"exec": "text(1)", "reasoning": "think about it"}, {"text": "done"}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cassette = Path(self.tmp.name) / "c.jsonl.gz"
        self.fake = FakeUpstream(self.steps)
        self.upstream = f"http://127.0.0.1:{self.fake.start()}"

    def tearDown(self):
        self.fake.stop()
        self.tmp.cleanup()

    def post(self, port, payload, path="/backend-api/codex/responses", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        data = json.dumps(payload).encode()
        conn.request("POST", path, data, {"content-type": "application/json", "authorization": "Bearer placeholder",
                                          "originator": "codex_exec", **(headers or {})})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()


class RelayTests(RecorderCase):
    def test_the_stream_reaches_the_worker_unchanged_and_the_call_is_counted(self):
        rec = recorder.Recorder(self.upstream, self.cassette)
        port = rec.start()
        try:
            status, headers, raw = self.post(port, body())
        finally:
            rec.stop()
        self.assertEqual(status, 200)
        self.assertIn(b"event: response.completed", raw)
        self.assertIn(b'"name": "exec"', raw)
        self.assertEqual(self.fake.requests[0]["model"], "gpt-6.1-sol")
        self.assertEqual(rec.calls, [{"status": 200, "input_tokens": 1000, "cached_tokens": 800, "output_tokens": 40,
                                      "reasoning_tokens": 8}])

    def test_each_call_is_stored_with_its_output_usage_and_rate_limits(self):
        rec = recorder.Recorder(self.upstream, self.cassette)
        port = rec.start()
        try:
            self.post(port, body(1))
            self.post(port, body(2))
        finally:
            rec.stop()
        entries = recorder.load(self.cassette)
        self.assertEqual([e["seq"] for e in entries], [0, 1])
        first = entries[0]
        self.assertEqual(first["path"], "/backend-api/codex/responses")
        self.assertEqual(first["response"]["usage"]["output_tokens"], 40)
        self.assertEqual(first["rate_limits"]["x-codex-secondary-used-percent"], "0.25")
        calls = recorder.model_calls(entries)
        self.assertEqual(calls, [{"call_id": "call_0", "name": "exec", "input": "text(1)"}])
        self.assertEqual(entries[1]["response"]["output"][0]["type"], "message")

    def test_requests_are_restored_whole_while_repeated_parts_are_stored_once(self):
        rec = recorder.Recorder(self.upstream, self.cassette)
        port = rec.start()
        try:
            self.post(port, body(3))
            self.post(port, body(4))
        finally:
            rec.stop()
        entries = recorder.load(self.cassette)
        self.assertEqual(entries[1]["request"]["model"], "gpt-6.1-sol")
        self.assertEqual(len(entries[1]["request"]["input"]), 5)
        self.assertEqual(entries[1]["request"]["input"][3]["content"][0]["text"], "turn 3 ")
        with gzip.open(self.cassette, "rt") as handle:
            blobs = [json.loads(l) for l in handle if '"blob"' in l[:12]]
        self.assertEqual(len(blobs), len({b["blob"] for b in blobs}), "a blob was stored twice")

    def test_reasoning_text_and_encrypted_content_are_reduced_to_their_length(self):
        rec = recorder.Recorder(self.upstream, self.cassette)
        port = rec.start()
        try:
            self.post(port, body(1))
        finally:
            rec.stop()
        text = gzip.open(self.cassette, "rt").read()
        self.assertNotIn("zzzz", text)
        self.assertNotIn("think about it", text)
        self.assertNotIn("xxxx", text)
        entry = recorder.load(self.cassette)[0]
        self.assertEqual(entry["request"]["input"][-1]["encrypted_content"], {"chars": 405})

    def test_a_stream_sent_without_an_event_stream_content_type_is_still_read(self):
        # The ChatGPT Codex backend streams SSE under another content type (seen 2026-10-22).
        self.fake.content_type = "application/octet-stream"
        rec = recorder.Recorder(self.upstream, self.cassette)
        port = rec.start()
        try:
            self.post(port, body())
        finally:
            rec.stop()
        self.assertEqual(rec.calls[0]["output_tokens"], 40)
        self.assertEqual(recorder.model_calls(recorder.load(self.cassette))[0]["name"], "exec")

    def test_an_upstream_error_is_relayed_and_recorded(self):
        self.fake.status = 429
        rec = recorder.Recorder(self.upstream, self.cassette)
        port = rec.start()
        try:
            status, _h, _raw = self.post(port, body())
        finally:
            rec.stop()
        self.assertEqual(status, 429)
        self.assertEqual(rec.calls[0]["status"], 429)
        self.assertIsNone(rec.calls[0]["input_tokens"])
        self.assertEqual(recorder.load(self.cassette)[0]["status"], 429)


class CredentialTests(RecorderCase):
    def test_the_recorder_adds_the_credentials_and_stores_none_of_them(self):
        rec = recorder.Recorder(self.upstream, self.cassette, secrets=[TOKEN, ACCOUNT],
                                auth=lambda: {"authorization": f"Bearer {TOKEN}", "chatgpt-account-id": ACCOUNT})
        port = rec.start()
        try:
            self.post(port, body())
        finally:
            rec.stop()
        self.assertEqual(self.fake.headers[0]["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(self.fake.headers[0]["chatgpt-account-id"], ACCOUNT)
        self.assertEqual(self.fake.headers[0]["originator"], "codex_exec")
        text = gzip.open(self.cassette, "rt").read()
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(ACCOUNT, text)
        self.assertEqual(rec.leaks, [])

    def test_a_call_that_carries_a_secret_is_relayed_but_never_stored(self):
        rec = recorder.Recorder(self.upstream, self.cassette, secrets=[TOKEN])
        port = rec.start()
        try:
            self.post(port, body(1, extra=TOKEN))
        finally:
            rec.stop()
        self.assertEqual(rec.leaks, [0])
        self.assertNotIn(TOKEN, gzip.open(self.cassette, "rt").read() if self.cassette.exists() else "")

    def test_the_host_user_name_never_reaches_the_cassette(self):
        rec = recorder.Recorder(self.upstream, self.cassette, user="hostuser")
        port = rec.start()
        try:
            self.post(port, body(1, extra="/home/hostuser/x"))
        finally:
            rec.stop()
        text = gzip.open(self.cassette, "rt").read()
        self.assertNotIn("hostuser", text)
        self.assertIn("/home/worker/x", text)


def jwt(exp: float) -> str:
    part = base64.urlsafe_b64encode(json.dumps({"exp": int(exp)}).encode()).decode().rstrip("=")
    return f"h.{part}.s"


class ChatGPTAuthTests(unittest.TestCase):
    def write(self, access: str) -> Path:
        path = Path(self.tmp.name) / "auth.json"
        path.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": access, "refresh_token": "r" * 20,
                                                                         "id_token": "i" * 20, "account_id": ACCOUNT}}))
        return path

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_headers_come_from_the_file_on_each_call(self):
        token = jwt(time.time() + 3600)
        auth = recorder.ChatGPTAuth(self.write(token))
        self.assertEqual(auth(), {"authorization": f"Bearer {token}", "chatgpt-account-id": ACCOUNT})
        newer = jwt(time.time() + 7200)
        self.write(newer)
        self.assertEqual(auth()["authorization"], f"Bearer {newer}")

    def test_every_credential_in_the_file_is_a_secret_to_keep_out_of_records(self):
        token = jwt(time.time() + 3600)
        secrets = recorder.ChatGPTAuth(self.write(token)).secrets()
        for value in (token, "r" * 20, "i" * 20, ACCOUNT):
            self.assertIn(value, secrets)

    def test_a_token_close_to_expiry_stops_before_any_call(self):
        auth = recorder.ChatGPTAuth(self.write(jwt(time.time() + 60)))
        with self.assertRaises(recorder.TokenExpiring):
            auth.check(min_seconds=900)
        recorder.ChatGPTAuth(self.write(jwt(time.time() + 3600))).check(min_seconds=900)

    def test_an_api_key_login_is_refused(self):
        path = Path(self.tmp.name) / "auth.json"
        path.write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x"}))
        with self.assertRaises(recorder.TokenExpiring):
            recorder.ChatGPTAuth(path).check()


if __name__ == "__main__":
    unittest.main()
