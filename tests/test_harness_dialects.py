"""Run the per-harness dialect tests that live beside each port in lab/patton_lab/harness/, so CI runs them too."""

from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

PORTS = ("dsh", "goose", "cline")


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in PORTS:
        suite.addTests(loader.loadTestsFromName(f"patton_lab.harness.{name}.test_dialect"))
    return suite


if __name__ == "__main__":
    unittest.main()
