"""The Gemini to chat-completions proxy for Gemini CLI, against a stub chat upstream on loopback.

Request bodies come from `gemini-cli/fixtures/cli_requests.json`: what Gemini CLI 0.65.0 really sent in the
fake_gemini traces, so the translation is tested on the shapes the CLI emits, not on invented ones.
"""

from pathlib import Path
import copy
import importlib.util
import json
import math
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = Path(__file__).resolve().parents[1]
HERE = REPO / "lab" / "patton_lab" / "harness" / "gemini-cli"
_spec = importlib.util.spec_from_file_location("gemini_cli_proxy", HERE / "proxy.py")
proxy_mod = importlib.util.module_from_spec(_spec)
sys.modules["gemini_cli_proxy"] = proxy_mod
_spec.loader.exec_module(proxy_mod)
GeminiProxy, json_schema = proxy_mod.GeminiProxy, proxy_mod.json_schema

FIX = json.loads((HERE / "fixtures" / "cli_requests.json").read_text())
TOOLS = FIX["turn1"]["tools"]
STREAM = "/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse"
GENERATE = "/v1beta/models/gemini-2.5-flash:generateContent"


def body(name):
    """A fixture request with the tools every turn carries."""
    b = copy.deepcopy(FIX[name])
    b.setdefault("tools", copy.deepcopy(TOOLS))
    return b


def chat_reply(message, finish="stop", usage=None):
    return {"id": "chatcmpl-1", "object": "chat.completion", "model": "up-model",
            "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish}],
            "usage": usage or {"prompt_tokens": 1200, "completion_tokens": 34, "total_tokens": 1234}}


def call(cid, name, args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}


class Stub:
    """A chat-completions upstream that answers each POST with the next queued (status, bytes, headers)."""

    def __init__(self):
        self.queue, self.bodies, self.delay = [], [], 0.0
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                stub.bodies.append({"path": self.path, "auth": self.headers.get("authorization"),
                                    "body": json.loads(raw)})
                if stub.delay:
                    time.sleep(stub.delay)
                code, payload, headers = stub.queue.pop(0) if stub.queue else (500, b"{}", {})
                self.send_response(code)
                for k, v in {"content-type": "application/json", **headers}.items():
                    self.send_header(k, v)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except OSError:  # the proxy gave up first (the timeout test)
                    pass

            def log_message(self, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/openai/v1"

    def reply(self, obj, code=200, headers=None):
        self.queue.append((code, obj if isinstance(obj, bytes) else json.dumps(obj).encode(), headers or {}))

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class ProxyCase(unittest.TestCase):
    def setUp(self):
        self.stub = Stub()
        self.addCleanup(self.stub.stop)
        self.proxy = GeminiProxy(self.stub.url, "up-model", timeout=5)
        self.port = self.proxy.start()
        self.addCleanup(self.proxy.stop)

    def post(self, path, payload):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, resp.headers.get("content-type"), resp.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("content-type"), err.read().decode()

    def stream(self, payload):
        """POST to streamGenerateContent and return the parsed SSE chunks."""
        code, kind, text = self.post(STREAM, payload)
        self.assertEqual(code, 200, text)
        self.assertEqual(kind, "text/event-stream")
        events = [e for e in text.replace("\r\n", "\n").split("\n\n") if e.strip()]
        self.assertTrue(events and all(e.startswith("data: ") for e in events), text)
        return [json.loads(e[len("data: "):]) for e in events]

    def sent(self, n=-1):
        return self.stub.bodies[n]["body"]


class TextAndRequestShape(ProxyCase):
    def test_a_text_reply_from_the_real_first_turn(self):
        self.stub.reply(chat_reply({"content": "hello there"}))
        (chunk,) = self.stream(body("turn1"))
        cand = chunk["candidates"][0]
        self.assertEqual(cand["content"], {"role": "model", "parts": [{"text": "hello there"}]})
        self.assertEqual(cand["finishReason"], "STOP")
        self.assertEqual(chunk["usageMetadata"],
                         {"promptTokenCount": 1200, "candidatesTokenCount": 34, "totalTokenCount": 1234})
        sent = self.sent()
        self.assertEqual(self.stub.bodies[-1]["path"], "/openai/v1/chat/completions")
        self.assertEqual(sent["model"], "up-model")
        self.assertIs(sent["stream"], False)
        self.assertEqual(sent["messages"][0]["role"], "system")
        self.assertIn("non-interactive CLI agent", sent["messages"][0]["content"])
        self.assertEqual([m["role"] for m in sent["messages"]], ["system", "user"])
        self.assertIn("<session_context>", sent["messages"][1]["content"])
        self.assertTrue(sent["messages"][1]["content"].endswith("Follow the scripted steps."))
        self.assertEqual(self.proxy.requests, [sent])
        self.assertEqual(self.proxy.received[-1]["_path"], STREAM.split("?")[0])

    def test_generation_config_is_mapped_and_the_rest_reported(self):
        b = body("turn1")
        b["generationConfig"]["maxOutputTokens"] = 512
        b["generationConfig"]["stopSequences"] = ["END"]
        self.stub.reply(chat_reply({"content": "ok"}))
        self.post(GENERATE, b)
        sent = self.sent()
        self.assertEqual((sent["temperature"], sent["top_p"], sent["max_tokens"], sent["stop"]), (1, 0.95, 512, ["END"]))
        self.assertNotIn("top_k", sent)
        self.assertIn("generationConfig.thinkingConfig", self.proxy.untranslated)
        self.assertIn("generationConfig.topK", self.proxy.untranslated)

    def test_generate_content_returns_plain_json(self):
        self.stub.reply(chat_reply({"content": "plain"}))
        code, kind, text = self.post(GENERATE, body("turn1"))
        self.assertEqual((code, kind), (200, "application/json"))
        self.assertEqual(json.loads(text)["candidates"][0]["content"]["parts"], [{"text": "plain"}])

    def test_the_api_key_goes_upstream_as_a_bearer_and_not_into_requests(self):
        proxy = GeminiProxy(self.stub.url, "up-model", api_key="k-test-123", timeout=5)
        proxy.start()
        self.addCleanup(proxy.stop)
        self.stub.reply(chat_reply({"content": "ok"}))
        proxy.generate(body("turn1"))
        self.assertEqual(self.stub.bodies[-1]["auth"], "Bearer k-test-123")
        self.assertNotIn("k-test-123", json.dumps(proxy.requests))


class ToolCalls(ProxyCase):
    def test_a_single_tool_call(self):
        self.stub.reply(chat_reply({"content": None, "tool_calls": [call("call_a1", "run_shell_command",
                                                                         {"command": "ls /work"})]}, "tool_calls"))
        (chunk,) = self.stream(body("turn1"))
        cand = chunk["candidates"][0]
        self.assertEqual(cand["content"]["parts"], [{"functionCall": {"name": "run_shell_command",
                                                                      "args": {"command": "ls /work"},
                                                                      "id": "call_a1"}}])
        self.assertEqual(cand["finishReason"], "STOP")

    def test_parallel_tool_calls_keep_their_order(self):
        self.stub.reply(chat_reply({"content": None, "tool_calls": [
            call("c1", "read_file", {"file_path": "/work/a"}), call("c2", "read_file", {"file_path": "/work/b"}),
            call("c3", "list_directory", {"dir_path": "/work"})]}, "tool_calls"))
        (chunk,) = self.stream(body("turn1"))
        fcs = [p["functionCall"] for p in chunk["candidates"][0]["content"]["parts"]]
        self.assertEqual([(f["id"], f["name"]) for f in fcs], [("c1", "read_file"), ("c2", "read_file"),
                                                               ("c3", "list_directory")])
        self.assertEqual(fcs[1]["args"], {"file_path": "/work/b"})

    def test_text_and_a_tool_call_in_one_turn(self):
        self.stub.reply(chat_reply({"content": "Listing first.", "tool_calls": [call("c9", "glob", {"pattern": "*"})]},
                                   "tool_calls"))
        (chunk,) = self.stream(body("turn1"))
        parts = chunk["candidates"][0]["content"]["parts"]
        self.assertEqual(parts[0], {"text": "Listing first."})
        self.assertEqual(parts[1]["functionCall"]["name"], "glob")

    def test_empty_and_null_arguments_are_an_empty_object(self):
        self.stub.reply(chat_reply({"content": None, "tool_calls": [call("e1", "list_background_processes", ""),
                                                                    call("e2", "write_todos", "null")]}, "tool_calls"))
        out = self.proxy.generate(body("turn1"))
        self.assertEqual([p["functionCall"]["args"] for p in out["candidates"][0]["content"]["parts"]], [{}, {}])

    def test_unparseable_arguments_are_a_malformed_call_not_a_crash(self):
        for bad in ('{"command": "ls', "[1, 2]"):
            self.stub.reply(chat_reply({"content": None, "tool_calls": [call("b1", "run_shell_command", bad)]},
                                       "tool_calls"))
            (chunk,) = self.stream(body("turn1"))
            cand = chunk["candidates"][0]
            self.assertEqual(cand["finishReason"], "MALFORMED_FUNCTION_CALL")
            self.assertIn("run_shell_command", cand["finishMessage"])
            self.assertFalse(any("functionCall" in p for p in cand["content"]["parts"]))

    def test_reasoning_becomes_a_thought_part(self):
        self.stub.reply(chat_reply({"content": "done", "reasoning": "think think"},
                                   usage={"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17,
                                          "completion_tokens_details": {"reasoning_tokens": 5},
                                          "prompt_tokens_details": {"cached_tokens": 4}}))
        out = self.proxy.generate(body("turn1"))
        self.assertEqual(out["candidates"][0]["content"]["parts"], [{"text": "think think", "thought": True},
                                                                     {"text": "done"}])
        self.assertEqual(out["usageMetadata"], {"promptTokenCount": 10, "candidatesTokenCount": 7,
                                                "totalTokenCount": 17, "thoughtsTokenCount": 5,
                                                "cachedContentTokenCount": 4})

    def test_finish_reasons(self):
        for chat, gemini in (("length", "MAX_TOKENS"), ("content_filter", "SAFETY"), ("weird", "OTHER"),
                             (None, "STOP")):
            self.stub.reply(chat_reply({"content": "x"}, chat))
            self.assertEqual(self.proxy.generate(body("turn1"))["candidates"][0]["finishReason"], gemini)


class RoundTrip(ProxyCase):
    def test_the_real_blocked_call_history_from_s2(self):
        """The CLI's own call id (it synthesised one against fake_gemini) pairs the call with its response."""
        self.stub.reply(chat_reply({"content": "done"}))
        self.proxy.generate(body("s2_after_block"))
        msgs = self.sent()["messages"]
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "tool"])
        (tc,) = msgs[2]["tool_calls"]
        self.assertEqual(tc["function"]["name"], "run_shell_command")
        self.assertEqual(json.loads(tc["function"]["arguments"]), {"command": "rm /work/keep.txt"})
        self.assertEqual(msgs[3]["tool_call_id"], tc["id"])
        self.assertIn("Patton gate: this changes keep.txt", msgs[3]["content"])
        self.assertTrue(msgs[3]["content"].startswith('{"error": '))
        self.assertIsNone(msgs[2]["content"])

    def test_the_real_shell_output_from_s1_is_the_bare_string(self):
        self.stub.reply(chat_reply({"content": "done"}))
        self.proxy.generate(body("s1_after_ls"))
        tool = self.sent()["messages"][-1]
        self.assertEqual(tool["role"], "tool")
        self.assertIn("Output: keep.txt", tool["content"])
        self.assertFalse(tool["content"].startswith("{"))

    def _history(self, ids, response_ids):
        b = body("turn1")
        b["contents"].append({"role": "model", "parts": [
            {"functionCall": {**({"id": i} if i else {}), "name": "read_file", "args": {"file_path": f"/work/{n}"}}}
            for n, i in enumerate(ids)]})
        b["contents"].append({"role": "user", "parts": [
            {"functionResponse": {**({"id": i} if i else {}), "name": "read_file", "response": {"output": f"file {n}"}}}
            for n, i in response_ids]})
        return b

    def _pairs(self):
        msgs = self.sent()["messages"]
        assistant = next(m for m in msgs if m["role"] == "assistant")
        calls = {tc["id"]: json.loads(tc["function"]["arguments"])["file_path"] for tc in assistant["tool_calls"]}
        return [(calls[m["tool_call_id"]], m["content"]) for m in msgs if m["role"] == "tool"]

    def test_ids_the_proxy_issued_survive_with_and_without_the_name_prefix(self):
        self.stub.reply(chat_reply({"content": None, "tool_calls": [call("up_1", "read_file", {"file_path": "/work/0"}),
                                                                    call("up_2", "read_file", {"file_path": "/work/1"})]},
                                   "tool_calls"))
        self.proxy.generate(body("turn1"))
        self.stub.reply(chat_reply({"content": "done"}))
        # Responses out of order: one id as issued, one with the `<name>__` prefix newer CLIs add.
        self.proxy.generate(self._history(["up_1", "read_file__up_2"], [(1, "read_file__up_2"), (0, "up_1")]))
        assistant = self.sent()["messages"][2]
        self.assertEqual([tc["id"] for tc in assistant["tool_calls"]], ["up_1", "up_2"])
        self.assertEqual(self._pairs(), [("/work/1", "file 1"), ("/work/0", "file 0")])

    def test_responses_with_only_a_name_match_calls_in_order(self):
        self.stub.reply(chat_reply({"content": "done"}))
        self.proxy.generate(self._history([None, None], [(0, None), (1, None)]))
        self.assertEqual(self._pairs(), [("/work/0", "file 0"), ("/work/1", "file 1")])
        ids = [tc["id"] for tc in self.sent()["messages"][2]["tool_calls"]]
        self.assertEqual(len(set(ids)), 2)
        # Stable across requests, so the same history gives the same ids.
        self.stub.reply(chat_reply({"content": "done"}))
        self.proxy.generate(self._history([None, None], [(0, None), (1, None)]))
        self.assertEqual([tc["id"] for tc in self.sent()["messages"][2]["tool_calls"]], ids)

    def test_long_cli_ids_are_shortened_consistently(self):
        long = "read_file__" + "x" * 60
        self.stub.reply(chat_reply({"content": "done"}))
        self.proxy.generate(self._history([long], [(0, long)]))
        msgs = self.sent()["messages"]
        tid = msgs[2]["tool_calls"][0]["id"]
        self.assertLessEqual(len(tid), 40)
        self.assertEqual(msgs[3]["tool_call_id"], tid)

    def test_an_unanswered_call_and_an_orphan_response_keep_the_history_well_formed(self):
        b = self._history(["a", "b"], [(0, "a")])
        b["contents"].append({"role": "user", "parts": [{"text": "carry on"}]})
        b["contents"].append({"role": "user", "parts": [{"functionResponse": {"name": "ghost", "response": {"output": "boo"}}}]})
        self.stub.reply(chat_reply({"content": "done"}))
        self.proxy.generate(b)
        msgs = self.sent()["messages"]
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "tool", "tool", "user", "user"])
        self.assertIn("no result was recorded", msgs[4]["content"])
        self.assertEqual(msgs[4]["tool_call_id"], "b")
        self.assertEqual(msgs[5]["content"], "carry on")
        self.assertIn("[result of ghost] boo", msgs[6]["content"])

    def test_thought_parts_are_dropped_from_history(self):
        b = body("turn1")
        b["contents"].append({"role": "model", "parts": [{"text": "secret plan", "thought": True}, {"text": "visible"}]})
        self.stub.reply(chat_reply({"content": "done"}))
        self.proxy.generate(b)
        self.assertEqual(self.sent()["messages"][-1], {"role": "assistant", "content": "visible"})


class Schemas(ProxyCase):
    def test_every_real_declaration_becomes_an_openai_function(self):
        self.stub.reply(chat_reply({"content": "ok"}))
        self.proxy.generate(body("turn1"))
        tools = self.sent()["tools"]
        names = [d["name"] for d in TOOLS[0]["functionDeclarations"]]
        self.assertEqual([t["function"]["name"] for t in tools], names)
        self.assertEqual(len(tools), 16)
        for t in tools:
            self.assertEqual(t["type"], "function")
            self.assertEqual(t["function"]["parameters"]["type"], "object")
            self.assertNotIn("$schema", json.dumps(t))
        shell = next(t["function"] for t in tools if t["function"]["name"] == "run_shell_command")
        self.assertEqual(shell["parameters"]["required"], ["command"])
        self.assertEqual(shell["parameters"]["properties"]["delay_ms"]["type"], "integer")
        self.assertTrue(shell["description"].startswith("This tool executes"))

    def test_old_style_schema_with_upper_case_types(self):
        legacy = {"type": "OBJECT", "propertyOrdering": ["type", "items"], "properties": {
            "type": {"type": "STRING", "format": "enum", "enum": ["a", "b"], "nullable": True},
            "properties": {"type": "INTEGER", "minimum": "1", "example": 3},
            "items": {"type": "ARRAY", "minItems": "1", "maxItems": "5",
                      "items": {"type": "OBJECT", "properties": {"n": {"type": "NUMBER"}}, "required": ["n"]}}},
            "required": ["type"]}
        self.assertEqual(json_schema(legacy), {"type": "object", "properties": {
            "type": {"type": ["string", "null"], "enum": ["a", "b"]},
            "properties": {"type": "integer", "minimum": 1},
            "items": {"type": "array", "minItems": 1, "maxItems": 5,
                      "items": {"type": "object", "properties": {"n": {"type": "number"}}, "required": ["n"]}}},
            "required": ["type"]})

    def test_a_declaration_with_parameters_not_json_schema(self):
        b = body("turn1")
        b["tools"] = [{"functionDeclarations": [
            {"name": "legacy", "description": "d", "parameters": {"type": "OBJECT", "properties": {
                "x": {"type": "STRING"}}}},
            {"name": "bare"}]}, {"googleSearch": {}}]
        self.stub.reply(chat_reply({"content": "ok"}))
        self.proxy.generate(b)
        fns = [t["function"] for t in self.sent()["tools"]]
        self.assertEqual(fns[0]["parameters"], {"type": "object", "properties": {"x": {"type": "string"}}})
        self.assertEqual(fns[1]["parameters"], {"type": "object", "properties": {}})
        self.assertIn("tools.googleSearch", self.proxy.untranslated)

    def test_any_of_and_json_schema_types_pass_through(self):
        s = {"type": "object", "properties": {"v": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
             "additionalProperties": False, "$schema": "http://json-schema.org/draft-07/schema#"}
        self.assertEqual(json_schema(s), {"type": "object", "properties": {"v": {"anyOf": [{"type": "string"},
                                                                                         {"type": "null"}]}},
                                          "additionalProperties": False})

    def test_tool_config_modes(self):
        for cfg, want in (({"mode": "ANY"}, "required"), ({"mode": "NONE"}, "none"), ({"mode": "AUTO"}, "auto"),
                          ({"mode": "ANY", "allowedFunctionNames": ["glob"]},
                           {"type": "function", "function": {"name": "glob"}})):
            b = body("turn1")
            b["toolConfig"] = {"functionCallingConfig": cfg}
            self.stub.reply(chat_reply({"content": "ok"}))
            self.proxy.generate(b)
            self.assertEqual(self.sent()["tool_choice"], want)


class Errors(ProxyCase):
    def error(self, payload=None, path=STREAM):
        code, kind, text = self.post(path, payload or body("turn1"))
        self.assertEqual(kind, "application/json")
        return code, json.loads(text)["error"]

    def test_upstream_429_is_a_retryable_quota_error(self):
        self.stub.reply({"error": {"message": "Rate limit reached for model", "type": "tokens"}}, 429,
                        {"retry-after": "7"})
        code, err = self.error()
        self.assertEqual((code, err["code"], err["status"]), (429, 429, "RESOURCE_EXHAUSTED"))
        self.assertIn("Rate limit reached", err["message"])
        self.assertIn("Please retry in 7s", err["message"])
        self.assertEqual(err["details"], [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}])

    def test_upstream_500_and_400_keep_their_status(self):
        self.stub.reply(b"<html>boom</html>", 500)
        code, err = self.error()
        self.assertEqual((code, err["status"]), (500, "INTERNAL"))
        self.assertIn("boom", err["message"])
        self.stub.reply({"error": {"message": "tool schema rejected"}}, 400)
        code, err = self.error()
        self.assertEqual((code, err["status"]), (400, "INVALID_ARGUMENT"))
        self.assertIn("tool schema rejected", err["message"])

    def test_garbage_and_shapeless_upstream_bodies_are_502(self):
        self.stub.reply(b"this is not json at all")
        code, err = self.error()
        self.assertEqual((code, err["status"]), (502, "UNAVAILABLE"))
        self.assertIn("not JSON", err["message"])
        self.stub.reply({"choices": []})
        code, err = self.error()
        self.assertEqual(code, 502)
        self.assertIn("no choices", err["message"])

    def test_connection_refused_is_502(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
        s.close()
        proxy = GeminiProxy(f"http://127.0.0.1:{dead}/v1", "m", timeout=5)
        port = proxy.start()
        self.addCleanup(proxy.stop)
        self.port = port
        start = time.monotonic()
        code, err = self.error()
        self.assertEqual((code, err["status"]), (502, "UNAVAILABLE"))
        self.assertIn("unreachable", err["message"])
        self.assertLess(time.monotonic() - start, 5)

    def test_a_slow_upstream_times_out_as_504(self):
        proxy = GeminiProxy(self.stub.url, "m", timeout=0.5)
        self.port = proxy.start()
        self.addCleanup(proxy.stop)
        self.stub.delay = 2.0
        self.stub.reply(chat_reply({"content": "late"}))
        start = time.monotonic()
        code, err = self.error()
        self.assertEqual((code, err["status"]), (504, "DEADLINE_EXCEEDED"))
        self.assertLess(time.monotonic() - start, 2.0)

    def test_a_bad_request_body_and_an_unknown_route(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{STREAM}", data=b"not json", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)
        code, err = self.error({}, "/v1beta/models/gemini-2.5-flash:embedContent")
        self.assertEqual(code, 404)
        self.assertEqual(self.stub.bodies, [])


class CountTokensAndModels(ProxyCase):
    def test_count_tokens_is_an_estimate_without_upstream(self):
        b = {"contents": body("turn1")["contents"]}
        code, _, text = self.post("/v1beta/models/gemini-2.5-flash:countTokens", b)
        self.assertEqual(code, 200)
        want = math.ceil(len(json.dumps({"contents": b["contents"]}, ensure_ascii=False)) / 4)
        self.assertEqual(json.loads(text), {"totalTokens": want})
        self.assertGreater(want, 50)
        self.assertEqual(self.stub.bodies, [])

    def test_count_tokens_inside_generate_content_request(self):
        inner = {"contents": [{"role": "user", "parts": [{"text": "x" * 400}]}]}
        n = GeminiProxy.count_tokens({"generateContentRequest": inner})
        self.assertEqual(n, math.ceil(len(json.dumps(inner)) / 4))

    def test_models_are_listed(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/v1beta/models", timeout=10) as resp:
            models = json.loads(resp.read())["models"]
        self.assertEqual(models[0]["name"], "models/up-model")
        self.assertIn("streamGenerateContent", models[0]["supportedGenerationMethods"])


if __name__ == "__main__":
    unittest.main()
