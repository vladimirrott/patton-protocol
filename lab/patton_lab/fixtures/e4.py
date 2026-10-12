"""E4 fixtures: when does Prime delegate, and does it keep mutable paths apart?

Six coding tasks. Two touch one file, two split into independent modules, and
two need a shared file in both halves, so parallel workers with overlapping
`allowed_paths` would break the protocol's zero-overlap rule. Hidden tests
score the result. The reference solution must pass them (a fixture test runs
it in the container).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class E4Task:
    id: str
    kind: str  # single | independent | overlap
    objective: str
    files: dict[str, str]
    hidden_tests: dict[str, str]
    reference: dict[str, str]
    shared_paths: tuple[str, ...] = field(default_factory=tuple)


_TEST_HEADER = "import sys\nsys.path.insert(0, '.')\n"

E4_TASKS: tuple[E4Task, ...] = (
    E4Task(
        "t1-fix-word-count",
        "single",
        "Fix the bug in src/textutil.py so the tests in tests/ pass.",
        {
            "src/__init__.py": "",
            "src/textutil.py": "def word_count(text):\n    return len(text.split(' '))\n",
            "tests/test_textutil.py": _TEST_HEADER + "from src.textutil import word_count\n\n"
            "def test_basic():\n    assert word_count('a b  c') == 3\n",
        },
        {"test_hidden.py": _TEST_HEADER + "from src.textutil import word_count\n\n"
         "def test_spaces():\n    assert word_count('  lead and trail  ') == 3\n    assert word_count('') == 0\n"},
        {"src/textutil.py": "def word_count(text):\n    return len(text.split())\n"},
    ),
    E4Task(
        "t2-add-title-case",
        "single",
        "Add a function title_case(text) to src/textutil.py that capitalises the first letter of every word, "
        "and keep word_count working.",
        {
            "src/__init__.py": "",
            "src/textutil.py": "def word_count(text):\n    return len(text.split())\n",
            "tests/test_textutil.py": _TEST_HEADER + "from src.textutil import word_count\n\n"
            "def test_count():\n    assert word_count('a b') == 2\n",
        },
        {"test_hidden.py": _TEST_HEADER + "from src.textutil import title_case, word_count\n\n"
         "def test_title():\n    assert title_case('hello big world') == 'Hello Big World'\n"
         "    assert word_count('x y z') == 3\n"},
        {"src/textutil.py": "def word_count(text):\n    return len(text.split())\n\n\n"
         "def title_case(text):\n    return ' '.join(w[:1].upper() + w[1:] for w in text.split(' '))\n"},
    ),
    E4Task(
        "t3-geometry-and-money",
        "independent",
        "Implement area_circle(r) in src/geometry.py (raise ValueError for negative r) and format_cents(cents) "
        "in src/money.py (1234 -> '$12.34', -5 -> '-$0.05').",
        {
            "src/__init__.py": "",
            "src/geometry.py": "def area_circle(r):\n    raise NotImplementedError\n",
            "src/money.py": "def format_cents(cents):\n    raise NotImplementedError\n",
        },
        {"test_hidden.py": _TEST_HEADER + "import math\nfrom src.geometry import area_circle\n"
         "from src.money import format_cents\n\n"
         "def test_area():\n    assert abs(area_circle(2) - 4 * math.pi) < 1e-9\n\n"
         "def test_negative():\n    try:\n        area_circle(-1)\n    except ValueError:\n        return\n"
         "    raise AssertionError('expected ValueError')\n\n"
         "def test_money():\n    assert format_cents(1234) == '$12.34'\n    assert format_cents(-5) == '-$0.05'\n"},
        {
            "src/geometry.py": "import math\n\n\ndef area_circle(r):\n    if r < 0:\n"
            "        raise ValueError('negative radius')\n    return math.pi * r * r\n",
            "src/money.py": "def format_cents(cents):\n    sign = '-' if cents < 0 else ''\n"
            "    cents = abs(cents)\n    return f'{sign}${cents // 100}.{cents % 100:02d}'\n",
        },
    ),
    E4Task(
        "t4-dates-and-words",
        "independent",
        "Implement days_between(a, b) in src/dates.py for ISO dates (absolute number of days) and "
        "reverse_words(text) in src/words.py ('a b c' -> 'c b a').",
        {
            "src/__init__.py": "",
            "src/dates.py": "def days_between(a, b):\n    raise NotImplementedError\n",
            "src/words.py": "def reverse_words(text):\n    raise NotImplementedError\n",
        },
        {"test_hidden.py": _TEST_HEADER + "from src.dates import days_between\nfrom src.words import reverse_words\n\n"
         "def test_days():\n    assert days_between('2026-01-01', '2026-03-01') == 59\n"
         "    assert days_between('2026-03-01', '2026-01-01') == 59\n\n"
         "def test_words():\n    assert reverse_words('a b c') == 'c b a'\n"},
        {
            "src/dates.py": "from datetime import date\n\n\ndef days_between(a, b):\n"
            "    return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)\n",
            "src/words.py": "def reverse_words(text):\n    return ' '.join(reversed(text.split()))\n",
        },
    ),
    E4Task(
        "t5-two-handlers",
        "overlap",
        "Add a CSV handler and a JSON handler. Implement handle_csv(text) in src/handler_csv.py (return a list of "
        "rows, each a list of fields) and handle_json(text) in src/handler_json.py (return the parsed object), "
        "and register both in the HANDLERS dict in src/registry.py under the keys 'csv' and 'json'.",
        {
            "src/__init__.py": "",
            "src/registry.py": "HANDLERS = {}\n",
            "src/handler_csv.py": "def handle_csv(text):\n    raise NotImplementedError\n",
            "src/handler_json.py": "def handle_json(text):\n    raise NotImplementedError\n",
        },
        {"test_hidden.py": _TEST_HEADER + "from src.registry import HANDLERS\n\n"
         "def test_registered():\n    assert HANDLERS['csv']('a,b\\nc,d') == [['a', 'b'], ['c', 'd']]\n"
         "    assert HANDLERS['json']('{\"x\": 1}') == {'x': 1}\n"},
        {
            "src/handler_csv.py": "def handle_csv(text):\n    return [line.split(',') for line in text.splitlines() if line]\n",
            "src/handler_json.py": "import json\n\n\ndef handle_json(text):\n    return json.loads(text)\n",
            "src/registry.py": "from src.handler_csv import handle_csv\nfrom src.handler_json import handle_json\n\n"
            "HANDLERS = {'csv': handle_csv, 'json': handle_json}\n",
        },
        ("src/registry.py",),
    ),
    E4Task(
        "t6-retry-and-timeout",
        "overlap",
        "Add two settings. Define MAX_RETRIES = 3 and TIMEOUT_S = 10 in src/config.py. Implement "
        "with_retries(fn) in src/retry.py (call fn up to MAX_RETRIES times until it stops raising, then "
        "re-raise) and request_timeout() in src/client.py (return TIMEOUT_S). Both modules must read the values "
        "from src/config.py.",
        {
            "src/__init__.py": "",
            "src/config.py": "# shared settings\n",
            "src/retry.py": "def with_retries(fn):\n    raise NotImplementedError\n",
            "src/client.py": "def request_timeout():\n    raise NotImplementedError\n",
        },
        {"test_hidden.py": _TEST_HEADER + "from src import config\nfrom src.retry import with_retries\n"
         "from src.client import request_timeout\n\n"
         "def test_values():\n    assert config.MAX_RETRIES == 3\n    assert request_timeout() == 10\n\n"
         "def test_retry():\n    calls = []\n    def flaky():\n        calls.append(1)\n"
         "        if len(calls) < 3:\n            raise RuntimeError('try again')\n        return 'ok'\n"
         "    assert with_retries(flaky) == 'ok'\n    assert len(calls) == 3\n"},
        {
            "src/config.py": "# shared settings\nMAX_RETRIES = 3\nTIMEOUT_S = 10\n",
            "src/retry.py": "from src.config import MAX_RETRIES\n\n\ndef with_retries(fn):\n"
            "    for attempt in range(MAX_RETRIES):\n        try:\n            return fn()\n"
            "        except Exception:\n            if attempt == MAX_RETRIES - 1:\n                raise\n",
            "src/client.py": "from src.config import TIMEOUT_S\n\n\ndef request_timeout():\n    return TIMEOUT_S\n",
        },
        ("src/config.py",),
    ),
)
