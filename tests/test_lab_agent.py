"""Tests for lab/patton_lab/agent.py with a scripted client."""

from pathlib import Path
import json
import sys
import unittest

LAB_ROOT = Path(__file__).resolve().parents[1] / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab.agent import AgentSpec, run_agent, run_interleaved  # noqa: E402
from patton_lab.client import Completion, PROVIDERS  # noqa: E402
from patton_lab.gate import Gate  # noqa: E402
from patton_lab.sandbox import Sandbox  # noqa: E402


def tool_call(call_id, name, arguments):
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": raw}}


def completion(content=None, calls=None, prompt=10, completion_tokens=5, reasoning=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    if reasoning:
        message["reasoning"] = reasoning
    return Completion(message, {"prompt_tokens": prompt, "completion_tokens": completion_tokens}, "m", "stop", False)


class ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, provider, model, messages, tools, *, salt, max_tokens=2000, **kwargs):
        self.calls.append({"salt": salt, "messages": json.loads(json.dumps(messages)), "tools": tools})
        if not self.script:
            raise AssertionError("script exhausted")
        step = self.script.pop(0)
        return step(salt) if callable(step) else step


PROVIDER = PROVIDERS["groq"]


class RunAgentTests(unittest.TestCase):
    def test_tool_turn_then_final(self):
        client = ScriptedClient(
            [
                completion(calls=[tool_call("c1", "list_dir", {}), tool_call("c2", "read_file", {"path": "a.txt"})]),
                completion(content="All done."),
            ]
        )
        with Sandbox({"a.txt": "alpha"}) as box:
            result = run_agent(client, PROVIDER, "m", "sys", "user", box, ["list_dir", "read_file"], salt="job")
        self.assertEqual(result.stop_reason, "final")
        self.assertEqual(result.final_text, "All done.")
        self.assertEqual(result.turns, 2)
        self.assertEqual(result.usage, {"prompt_tokens": 20, "completion_tokens": 10})
        second = client.calls[1]["messages"]
        self.assertEqual([m["role"] for m in second], ["system", "user", "assistant", "tool", "tool"])
        self.assertEqual(second[3]["tool_call_id"], "c1")
        self.assertEqual(second[4]["content"], "alpha")
        self.assertEqual([c["salt"] for c in client.calls], ["job|t0", "job|t1"])

    def test_turn_cap(self):
        client = ScriptedClient([completion(calls=[tool_call(f"c{i}", "list_dir", {})]) for i in range(10)])
        with Sandbox({"a.txt": "a"}) as box:
            result = run_agent(client, PROVIDER, "m", "s", "u", box, ["list_dir"], salt="j", max_turns=10)
        self.assertEqual(result.stop_reason, "turn_cap")
        self.assertEqual(result.turns, 10)
        self.assertEqual(len(client.calls), 10)

    def test_malformed_arguments_are_reported_and_the_loop_continues(self):
        client = ScriptedClient(
            [completion(calls=[tool_call("c1", "read_file", "{oops")]), completion(content="Stopped.")]
        )
        with Sandbox({"a.txt": "a"}) as box:
            result = run_agent(client, PROVIDER, "m", "s", "u", box, ["read_file"], salt="j")
            self.assertFalse(box.events[0]["ok"])
        tool_message = client.calls[1]["messages"][3]
        self.assertTrue(tool_message["content"].startswith("ERROR"))
        self.assertEqual(result.stop_reason, "final")

    def test_a_tool_outside_the_offered_set_is_refused(self):
        client = ScriptedClient(
            [completion(calls=[tool_call("c1", "delete_file", {"path": "a.txt"})]), completion(content="ok")]
        )
        with Sandbox({"a.txt": "a"}) as box:
            run_agent(client, PROVIDER, "m", "s", "u", box, ["read_file"], salt="j")
            self.assertTrue(box.work.joinpath("a.txt").exists())
        self.assertTrue(client.calls[1]["messages"][3]["content"].startswith("ERROR"))

    def test_empty_final_answer(self):
        client = ScriptedClient([completion(content="  ")])
        with Sandbox({}) as box:
            result = run_agent(client, PROVIDER, "m", "s", "u", box, ["list_dir"], salt="j")
        self.assertEqual(result.stop_reason, "empty")

    def test_reasoning_is_kept_in_the_transcript_only_as_length(self):
        client = ScriptedClient([completion(content="done", reasoning="private chain of thought")])
        with Sandbox({}) as box:
            result = run_agent(client, PROVIDER, "m", "s", "u", box, [], salt="j")
        self.assertEqual(result.transcript[-1]["reasoning_chars"], len("private chain of thought"))
        self.assertNotIn("private chain of thought", json.dumps(result.transcript))


class InterleaveTests(unittest.TestCase):
    def test_alternates_and_uses_each_agents_gate(self):
        order = []

        def step(name, content=None, calls=None):
            def make(salt):
                order.append(name)
                return completion(content=content, calls=calls)

            return make

        client = ScriptedClient(
            [
                step("w1", calls=[tool_call("a", "write_file", {"path": "own1.txt", "content": "1"})]),
                step("w2", calls=[tool_call("b", "write_file", {"path": "own1.txt", "content": "2"})]),
                step("w1", content="w1 done"),
                step("w2", content="w2 done"),
            ]
        )
        with Sandbox({}) as box:
            specs = [
                AgentSpec("w1", "s", "u", ["write_file"], Gate(allowed_paths=["own1.txt"])),
                AgentSpec("w2", "s", "u", ["write_file"], Gate(allowed_paths=["own2.txt"])),
            ]
            results = run_interleaved(client, PROVIDER, "m", specs, box, salt="j")
            self.assertEqual(box.work.joinpath("own1.txt").read_text(encoding="utf-8"), "1")
        self.assertEqual(order, ["w1", "w2", "w1", "w2"])
        self.assertEqual([r.final_text for r in results], ["w1 done", "w2 done"])
        self.assertIn("j|w2|t0", [c["salt"] for c in client.calls])


if __name__ == "__main__":
    unittest.main()
