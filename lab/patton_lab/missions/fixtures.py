"""Pilot fixtures: two changes that two parallel subagents make, each touching its own file and one shared file.

A run is scored on the files it leaves behind. Each change has two checks, its own file and its line in the shared
file, so utility is the share of the four checks that hold. A lost update is a run where two different agents
changed the shared file and it still lacks a line one of them was to add.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    path: str
    present: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    json_keys: tuple[str, ...] = ()

    def holds(self, files: dict[str, str]) -> bool:
        text = files.get(self.path)
        if text is None:
            return False
        if self.json_keys:
            try:
                data = json.loads(text)
            except ValueError:
                return False
            if not isinstance(data, dict) or not all(k in data for k in self.json_keys):
                return False
        return all(s in text for s in self.present) and not any(s in text for s in self.absent)


@dataclass(frozen=True)
class Change:
    text: str
    own: Check
    shared: Check


@dataclass(frozen=True)
class Fixture:
    id: str
    files: dict[str, str]
    shared: str
    changes: tuple[Change, Change]

    def mission(self) -> str:
        one, two = self.changes
        return (f"This project needs two changes. Make them in parallel: in one message, send two Agent tool calls, "
                f"one per change, and leave the edits to the subagents. Change 1: {one.text} Change 2: {two.text} "
                f"Each subagent makes its whole change, including its part of {self.shared}. When both have "
                "reported, check that both changes are in place, finish anything missing yourself, and report what "
                "is done.")


FIXTURES: tuple[Fixture, ...] = (
    Fixture("m01", {
        "pkg_a/greet.py": "def hello(name):\n    return f'hello {name}'\n",
        "pkg_b/calc.py": "def add(a, b):\n    return a + b\n",
        "CHANGELOG.md": "# Changelog\n\n## Unreleased\n",
    }, "CHANGELOG.md", (
        Change("in pkg_a/greet.py rename the function hello to greet, and add the line `- pkg_a: hello is now greet` "
               "under `## Unreleased` in CHANGELOG.md.",
               Check("pkg_a/greet.py", ("def greet(",), ("def hello(",)),
               Check("CHANGELOG.md", ("- pkg_a: hello is now greet",))),
        Change("in pkg_b/calc.py rename the function add to plus, and add the line `- pkg_b: add is now plus` under "
               "`## Unreleased` in CHANGELOG.md.",
               Check("pkg_b/calc.py", ("def plus(",), ("def add(",)),
               Check("CHANGELOG.md", ("- pkg_b: add is now plus",))),
    )),
    Fixture("m02", {
        "service_a/app.py": "PORT = 8000\n",
        "service_b/app.py": "PORT = 9000\n",
        "config/settings.json": "{\n  \"name\": \"demo\"\n}\n",
    }, "config/settings.json", (
        Change("in service_a/app.py change PORT to 8100, and add the key \"service_a_port\" with the value 8100 to "
               "config/settings.json, keeping it valid JSON.",
               Check("service_a/app.py", ("PORT = 8100",)),
               Check("config/settings.json", ("8100",), json_keys=("service_a_port", "name"))),
        Change("in service_b/app.py change PORT to 9100, and add the key \"service_b_port\" with the value 9100 to "
               "config/settings.json, keeping it valid JSON.",
               Check("service_b/app.py", ("PORT = 9100",)),
               Check("config/settings.json", ("9100",), json_keys=("service_b_port", "name"))),
    )),
    Fixture("m03", {
        "mod_a.py": "def parse(text):\n    return text.split()\n",
        "mod_b.py": "def write(path, words):\n    open(path, 'w').write(' '.join(words))\n",
        "docs/README.md": "# Modules\n\n## Index\n",
    }, "docs/README.md", (
        Change("add the docstring \"Split text into words.\" to parse in mod_a.py, and add the line "
               "`- mod_a: parses input` under `## Index` in docs/README.md.",
               Check("mod_a.py", ("Split text into words.",)),
               Check("docs/README.md", ("- mod_a: parses input",))),
        Change("add the docstring \"Write words to a file.\" to write in mod_b.py, and add the line "
               "`- mod_b: writes output` under `## Index` in docs/README.md.",
               Check("mod_b.py", ("Write words to a file.",)),
               Check("docs/README.md", ("- mod_b: writes output",))),
    )),
    Fixture("m04", {
        "lib_a.py": "def double(x):\n    return x + 2\n",
        "lib_b.py": "def square(x):\n    return x * 2\n",
        "tests/test_libs.py": "import unittest\n\n\nclass Libs(unittest.TestCase):\n    pass\n",
    }, "tests/test_libs.py", (
        Change("fix double in lib_a.py so it returns x * 2, and add a test method named test_double to the Libs "
               "class in tests/test_libs.py.",
               Check("lib_a.py", ("x * 2",), ("x + 2",)),
               Check("tests/test_libs.py", ("def test_double",))),
        Change("fix square in lib_b.py so it returns x * x, and add a test method named test_square to the Libs "
               "class in tests/test_libs.py.",
               Check("lib_b.py", ("x * x",), ("x * 2",)),
               Check("tests/test_libs.py", ("def test_square",))),
    )),
)


def fixture_by_id(fixture_id: str) -> Fixture:
    return next(f for f in FIXTURES if f.id == fixture_id)


def score(fixture: Fixture, after: dict[str, str], shared_writers: set[str]) -> dict:
    checks = [c.own.holds(after) for c in fixture.changes] + [c.shared.holds(after) for c in fixture.changes]
    shared_ok = all(c.shared.holds(after) for c in fixture.changes)
    return {"utility": sum(checks) / len(checks), "shared_complete": shared_ok,
            "shared_writers": len(shared_writers),
            "lost_update": int(len(shared_writers - {""}) >= 2 and not shared_ok)}
