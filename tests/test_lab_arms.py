"""Tests for lab/patton_lab/arms.py: frozen arm texts and the placebo ablation."""

from pathlib import Path
import re
import sys
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = PACKAGE_ROOT / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab import arms  # noqa: E402

PATTERN = re.compile(r"approv|irreversib|deletion", re.IGNORECASE)


def squash(text):
    return " ".join(text.split())


class ArmTextTests(unittest.TestCase):
    def test_arm_list(self):
        self.assertEqual(arms.ARMS, ("A", "A2", "B", "C", "D", "E", "F", "G"))

    def test_every_arm_starts_with_the_shared_preamble(self):
        for arm in arms.ARMS:
            self.assertTrue(arms.system_prompt(arm).startswith(arms.PREAMBLE), arm)

    def test_a_and_a2_are_identical(self):
        self.assertEqual(arms.system_prompt("A"), arms.system_prompt("A2"))
        self.assertNotIn("Patton", arms.system_prompt("A"))

    def test_b_carries_the_skill_body_without_frontmatter(self):
        prompt = arms.system_prompt("B")
        self.assertIn("## Lifecycle", prompt)
        self.assertNotIn("description: Coordinate", prompt)
        self.assertIn("Explicit human approval remains required before a merge, deployment, deletion", squash(prompt))

    def test_g_prompt_equals_b(self):
        self.assertEqual(arms.system_prompt("G"), arms.system_prompt("B"))

    def test_c_has_no_approval_text_and_keeps_most_of_b(self):
        b = arms.system_prompt("B")
        c = arms.system_prompt("C")
        self.assertIsNone(PATTERN.search(c), PATTERN.search(c) and c[PATTERN.search(c).start() - 80:][:200])
        self.assertGreaterEqual(len(c), 0.80 * len(b))
        self.assertIn("## Lifecycle", c)

    def test_c_protocol_docs_carry_no_approval_text_either(self):
        for name, text in arms.protocol_docs("C").items():
            self.assertIsNone(PATTERN.search(text), name)

    def test_d_is_b_plus_the_frozen_clause(self):
        clause = (LAB_ROOT / "arms" / "D_clause.md").read_text(encoding="utf-8").strip()
        b = arms.system_prompt("B")
        d = arms.system_prompt("D")
        self.assertTrue(d.startswith(b))
        self.assertEqual(d[len(b):].strip(), "## Reversibility\n\n" + clause)
        self.assertIn(clause, arms.protocol_docs("D")["references/safety-and-budgets.md"])

    def test_e_inlines_every_reference_and_the_adapter(self):
        e = arms.system_prompt("E")
        for ref in sorted((PACKAGE_ROOT / "references").glob("*.md")) + [PACKAGE_ROOT / "adapters" / "claude-code.md"]:
            body = ref.read_text(encoding="utf-8").strip()
            self.assertIn(body, e, ref.name)

    def test_placebo_keeps_table_rows_without_approval_text(self):
        safety = arms.protocol_docs("C")["references/safety-and-budgets.md"]
        self.assertIn("| Worker count |", safety)
        self.assertIn("| Timeout |", safety)
        self.assertNotIn("| Approvals |", safety)

    def test_table_ablation(self):
        table = "| a | b |\n| --- | --- |\n| keep | 1 |\n| approval | 2 |"
        self.assertEqual(arms.ablate(table), "| a | b |\n| --- | --- |\n| keep | 1 |")
        self.assertEqual(arms.ablate("| a | b |\n| --- | --- |\n| approval | 2 |"), "")

    def test_f_is_short_and_keeps_the_core_approval_sentences(self):
        f = arms.system_prompt("F")
        self.assertLess(len(arms.arm_text("F").encode("utf-8")), 2500)
        skill = squash(arms.skill_body())
        for sentence in arms.F_REQUIRED_SENTENCES:
            self.assertIn(squash(sentence), skill, "the required sentence must come from SKILL.md")
            self.assertIn(squash(sentence), squash(f), sentence)

    def test_protocol_docs_by_arm(self):
        self.assertIsNone(arms.protocol_docs("A"))
        self.assertIsNone(arms.protocol_docs("A2"))
        self.assertIsNone(arms.protocol_docs("F"))
        b_docs = arms.protocol_docs("B")
        self.assertIn("SKILL.md", b_docs)
        self.assertIn("references/worker-report.md", b_docs)
        self.assertEqual(arms.protocol_docs("G"), b_docs)
        self.assertEqual(arms.protocol_docs("E"), b_docs)

    def test_hashes_are_stable_and_distinct(self):
        first = {arm: arms.arm_hash(arm) for arm in arms.ARMS}
        second = {arm: arms.arm_hash(arm) for arm in arms.ARMS}
        self.assertEqual(first, second)
        self.assertEqual(first["A"], first["A2"])
        self.assertNotEqual(first["B"], first["G"], "G's hash must include its enforcement")
        self.assertEqual(len({first[a] for a in ("A", "B", "C", "D", "E", "F")}), 6)

    def test_unknown_arm_raises(self):
        with self.assertRaises(KeyError):
            arms.system_prompt("Z")


class AblationTests(unittest.TestCase):
    def test_removes_matching_sentences_and_bullets_only(self):
        text = (
            "# Title\n\nKeep this. Drop the approval part. Keep that.\n\n"
            "- keep bullet\n- drop irreversible\n  continued line\n- keep two\n"
        )
        out = arms.ablate(text)
        self.assertIn("Keep this.", out)
        self.assertIn("Keep that.", out)
        self.assertNotIn("approval", out)
        self.assertNotIn("irreversible", out)
        self.assertNotIn("continued line", out)
        self.assertIn("- keep bullet", out)
        self.assertIn("- keep two", out)
        self.assertIn("# Title", out)

    def test_a_paragraph_that_is_all_approval_text_disappears(self):
        self.assertEqual(arms.ablate("Approval needed.\n\nKeep.").strip(), "Keep.")


if __name__ == "__main__":
    unittest.main()


class RoleTests(unittest.TestCase):
    def test_roles_change_only_the_intro(self):
        for arm in ("B", "C", "D", "E", "G"):
            builder = arms.system_prompt(arm)
            verifier = arms.system_prompt(arm, role="verifier")
            self.assertIn("as a Builder", builder)
            self.assertIn("as a Verifier", verifier)
            self.assertEqual(builder.replace("as a Builder", "as a Verifier"), verifier)

    def test_generic_roles_for_the_no_protocol_arm(self):
        self.assertIn("Verifier", arms.system_prompt("A", role="verifier"))
        self.assertIn("lead engineer", arms.system_prompt("A", role="prime"))
        self.assertNotIn("Patton", arms.system_prompt("A", role="prime"))

    def test_hash_depends_on_role(self):
        self.assertNotEqual(arms.arm_hash("B"), arms.arm_hash("B", role="prime"))

    def test_unknown_role_raises(self):
        with self.assertRaises(KeyError):
            arms.system_prompt("B", role="janitor")
