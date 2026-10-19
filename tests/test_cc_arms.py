"""The arm registry, run planning and the mission text."""

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = REPO_ROOT / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab.cc import arms  # noqa: E402
from patton_lab.experiments import e1_mission  # noqa: E402
from patton_lab.fixtures import e1  # noqa: E402


class ArmTests(unittest.TestCase):
    def test_the_registry_holds_exactly_the_arms_of_section_six(self):
        self.assertEqual(set(arms.ARMS), {"R0", "R0S", "R1", "R1S", "R1D", "R3", "H-R0", "H-R1",
                                          "O-R0", "O-R0S", "O-R1", "O-R1S", "O-R1D"})

    def test_skill_is_installed_only_in_the_s_arms(self):
        for name, arm in arms.ARMS.items():
            self.assertEqual(arm.skill, name.endswith("S"), name)

    def test_gates_and_approvers(self):
        for name in ("R0", "R0S", "H-R0", "O-R0", "O-R0S"):
            self.assertEqual((arms.ARMS[name].gate, arms.ARMS[name].approver), ("none", "none"), name)
        for name in ("R1", "R1S", "H-R1", "O-R1", "O-R1S"):
            self.assertEqual((arms.ARMS[name].gate, arms.ARMS[name].approver), ("hook", "oracle"), name)
        for name in ("R1D", "O-R1D"):
            self.assertEqual((arms.ARMS[name].gate, arms.ARMS[name].approver), ("hook", "deny-all"), name)
        self.assertEqual((arms.ARMS["R3"].gate, arms.ARMS["R3"].approver), ("none", "none"))

    def test_permission_modes(self):
        self.assertEqual(arms.ARMS["R3"].permission_mode, "auto")
        for name, arm in arms.ARMS.items():
            if name != "R3":
                self.assertEqual(arm.permission_mode, "bypassPermissions", name)

    def test_models_and_backends(self):
        self.assertEqual({a.model for n, a in arms.ARMS.items() if n[0] in "R"}, {"claude-sonnet-5-5"})
        self.assertEqual({a.model for n, a in arms.ARMS.items() if n.startswith("H-")}, {"claude-haiku-5-5"})
        for name, arm in arms.ARMS.items():
            self.assertEqual(arm.backend, "groq" if name.startswith("O-") else "anthropic", name)
            if name.startswith("O-"):
                self.assertEqual(arm.model, "gpt-oss-120b")

    def test_the_auto_mode_arm_is_sonnet_only(self):
        self.assertNotIn("O-R3", arms.ARMS)
        self.assertEqual(arms.ARMS["R3"].backend, "anthropic")

    def test_the_mission_is_the_labs_mission(self):
        for fixture in e1.E1_FIXTURES + e1.E1_HELDOUT:
            self.assertEqual(arms.mission(fixture.id), e1_mission(fixture))


class PlanTests(unittest.TestCase):
    ARMS = ["R0", "R1", "R1D"]
    FIXTURES = ["f01", "f02", "f03", "f04"]

    def test_every_triple_appears_exactly_once(self):
        blocks = arms.plan_blocks(self.ARMS, self.FIXTURES, 3, seed=7)
        flat = [t for block in blocks for t in block]
        self.assertEqual(len(flat), 3 * 4 * 3)
        self.assertEqual(len(set(flat)), len(flat))
        self.assertEqual(set(flat), {(a, f, r) for a in self.ARMS for f in self.FIXTURES for r in range(3)})

    def test_blocks_hold_one_fixture_and_rep_with_every_arm(self):
        for block in arms.plan_blocks(self.ARMS, self.FIXTURES, 2, seed=1):
            self.assertEqual(len({(f, r) for _a, f, r in block}), 1)
            self.assertEqual(sorted(a for a, _f, _r in block), sorted(self.ARMS))

    def test_a_rep_is_finished_before_the_next_one_starts(self):
        reps = [block[0][2] for block in arms.plan_blocks(self.ARMS, self.FIXTURES, 3, seed=2)]
        self.assertEqual(reps, sorted(reps))

    def test_the_plan_is_reproducible_and_depends_on_the_seed(self):
        a = arms.plan_blocks(self.ARMS, self.FIXTURES, 3, seed=5)
        self.assertEqual(a, arms.plan_blocks(self.ARMS, self.FIXTURES, 3, seed=5))
        self.assertNotEqual(a, arms.plan_blocks(self.ARMS, self.FIXTURES, 3, seed=6))

    def test_fixture_order_inside_a_rep_is_shuffled(self):
        orders = set()
        for seed in range(10):
            blocks = arms.plan_blocks(self.ARMS, self.FIXTURES, 1, seed=seed)
            orders.add(tuple(block[0][1] for block in blocks))
        self.assertGreater(len(orders), 1)
        self.assertTrue(any(order != tuple(self.FIXTURES) for order in orders))

    def test_arm_order_inside_blocks_is_shuffled(self):
        orders = {tuple(a for a, _f, _r in block) for block in arms.plan_blocks(self.ARMS, self.FIXTURES, 3, seed=3)}
        self.assertGreater(len(orders), 1)


if __name__ == "__main__":
    unittest.main()
