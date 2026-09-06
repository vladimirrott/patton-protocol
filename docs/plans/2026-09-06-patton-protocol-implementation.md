# Patton Protocol Implementation Plan

> **For the implementing agent:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Build a standalone, harness-agnostic Agent Skills package that coordinates bounded coding-agent missions through a portable protocol and documented host adapters.

**Architecture:** Keep `SKILL.md` as the canonical portable entrypoint. Store mission, report, safety, and adapter detail in linked Markdown references. Use a dependency-free Python validator and fixture tests to enforce the canonical structure, internal links, vendor-neutral core, adapter capability labels, and serial fallback. Ship an original SVG mark without coupling the workflow to any model runtime.

**Tech Stack:** Markdown, YAML frontmatter, Python 3.11+ standard library, SVG, Git.

---

### Task 1: Establish the package skeleton and canonical metadata

**Files:**
- Create: `SKILL.md`
- Create: `agents/openai.yaml`
- Create: `references/.gitkeep` only if the implementation needs an empty directory before Task 2
- Create: `adapters/.gitkeep` only if the implementation needs an empty directory before Task 4
- Test: `tests/test_validate_protocol.py`

**Step 1: Write the failing test**

Add tests that require `SKILL.md` to have YAML frontmatter with `name: patton-protocol` and a non-empty `description`, and require the canonical file to include the portable lifecycle terms `scope`, `plan`, `dispatch`, `observe`, `reconcile`, `verify`, and `report`.

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_validate_protocol -v`

Expected: FAIL because `SKILL.md` and the validator do not exist.

**Step 3: Write minimal implementation**

Create the directory layout and a minimal canonical `SKILL.md` with valid frontmatter, a concise trigger description, and placeholder lifecycle headings. Add `agents/openai.yaml` with the Codex skill display metadata required by the current Codex skill convention. Do not put Claude, Codex, or Cursor commands in the core file.

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_validate_protocol -v`

Expected: The metadata tests pass; later validator tests may remain skipped until their tasks add the validator.

**Step 5: Commit**

```bash
git add SKILL.md agents/openai.yaml tests/test_validate_protocol.py
git commit -m "Scaffold the portable Patton skill"
```

### Task 2: Define mission and worker-report contracts

**Files:**
- Create: `references/mission-contract.md`
- Create: `references/worker-report.md`
- Modify: `SKILL.md`
- Modify: `tests/test_validate_protocol.py`

**Step 1: Write the failing test**

Add fixture-driven tests that require the mission contract to define objective, inputs, allowed paths, stopping condition, budget, timeout, retry limit, and evidence. Require the worker report contract to define mission identity, status, files changed, commands run, evidence, risks, and next action. Require `SKILL.md` to link both references with relative paths.

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_validate_protocol -v`

Expected: FAIL on missing contract files or missing required fields.

**Step 3: Write minimal implementation**

Document a small YAML-shaped report envelope and its allowed status values. State that workers return evidence and leads, not approval decisions. Define serial execution as the fallback when the host cannot spawn workers. Keep examples generic and free of host-specific tool names.

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_validate_protocol -v`

Expected: Contract and link tests pass.

**Step 5: Commit**

```bash
git add SKILL.md references/mission-contract.md references/worker-report.md tests/test_validate_protocol.py
git commit -m "Specify Patton mission and report contracts"
```

### Task 3: Implement the portable orchestration workflow

**Files:**
- Modify: `SKILL.md`
- Create: `references/safety-and-budgets.md`
- Modify: `tests/test_validate_protocol.py`

**Step 1: Write the failing test**

Add tests that require the core workflow to state: delegation thresholds, bounded missions, immutable ownership boundaries, evidence rechecking, conflict handling, partial-failure recovery, human approval for irreversible actions, and serial fallback. Add a test that rejects required vendor command syntax in `SKILL.md`.

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_validate_protocol -v`

Expected: FAIL because the workflow and safety reference do not yet contain the required rules.

**Step 3: Write minimal implementation**

Expand `SKILL.md` into the `scope → plan → dispatch → observe → reconcile → verify → report` lifecycle. Define Patton Prime and the functional roles Scout, Builder, Verifier, and Quartermaster. Add explicit limits for worker count, mission budget, timeout, retries, mutable-file overlap, approvals, and unresolved disagreement. Link the safety reference for detailed controls.

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_validate_protocol -v`

Expected: Portable workflow and safety tests pass.

**Step 5: Commit**

```bash
git add SKILL.md references/safety-and-budgets.md tests/test_validate_protocol.py
git commit -m "Add bounded portable orchestration"
```

### Task 4: Document host adapters without shrinking the compatibility claim

**Files:**
- Create: `references/harness-adapters.md`
- Create: `adapters/claude-code.md`
- Create: `adapters/codex.md`
- Create: `adapters/cursor.md`
- Modify: `SKILL.md`
- Modify: `tests/test_validate_protocol.py`

**Step 1: Write the failing test**

Add tests that require each adapter to label capabilities as `native`, `prompt-mediated`, or `unavailable`, and to document loading, worker definition, parallel dispatch, serial fallback, and approval boundaries. Require the core skill to state that any Agent Skills-compatible host is supported in principle.

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_validate_protocol -v`

Expected: FAIL because adapter files and capability labels do not exist.

**Step 3: Write minimal implementation**

Describe Claude Code custom subagents and agent teams, Codex custom agents and skills, and Cursor plugin agents and skills. Record known limitations, especially nested-spawn differences. Keep adapters explanatory; they must not become required dependencies of the portable workflow. Link the adapter overview from `SKILL.md`.

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_validate_protocol -v`

Expected: Adapter coverage tests pass.

**Step 5: Commit**

```bash
git add SKILL.md references/harness-adapters.md adapters/ tests/test_validate_protocol.py
git commit -m "Document Claude Codex and Cursor adapters"
```

### Task 5: Add protocol validator and adversarial fixtures

**Files:**
- Create: `scripts/validate_protocol.py`
- Modify: `tests/test_validate_protocol.py`
- Create: `tests/fixtures/invalid-missing-frontmatter/SKILL.md`
- Create: `tests/fixtures/invalid-broken-link/SKILL.md`
- Create: `tests/fixtures/invalid-vendor-core/SKILL.md`
- Create: `tests/fixtures/invalid-report-schema/references/worker-report.md`

**Step 1: Write the failing test**

Add tests for the validator's exit code and diagnostics on missing frontmatter, broken relative links, vendor-only required commands, missing report fields, and malformed adapter capability labels. Add valid-package tests for the real repository tree and serial-fallback wording.

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_validate_protocol -v`

Expected: FAIL because `scripts/validate_protocol.py` does not exist.

**Step 3: Write minimal implementation**

Implement a standard-library validator that accepts a package root, parses frontmatter without third-party dependencies, resolves Markdown links within the root, checks required contract terms, scans only the canonical core for forbidden vendor-required syntax, validates adapter capability labels, and returns nonzero with file-specific diagnostics on invalid fixtures.

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_validate_protocol -v`
Run: `python3 scripts/validate_protocol.py .`

Expected: All tests pass and the real package reports `valid`.

**Step 5: Commit**

```bash
git add scripts/validate_protocol.py tests/
git commit -m "Validate the Patton protocol package"
```

### Task 6: Add visual identity and package documentation

**Files:**
- Create: `assets/logo.svg`
- Create: `README.md`
- Modify: `tests/test_validate_protocol.py`

**Step 1: Write the failing test**

Add tests that require the README to identify the canonical `SKILL.md`, state compatibility with any Agent Skills-compliant host, link adapter notes, and explain serial fallback. Validate the SVG has a viewBox, accessible title, and no external references.

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_validate_protocol -v`

Expected: FAIL because the README and logo do not exist.

**Step 3: Write minimal implementation**

Create an original abstract SVG mark: a five-point star with three branching arrows, olive, cream, and signal red. Write README installation and usage guidance around the canonical skill package. State that host adapters document capabilities, not a hard support whitelist, and disclose that runtime spawning remains host-dependent.

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_validate_protocol -v`
Run: `python3 scripts/validate_protocol.py .`

Expected: All tests pass and validation remains `valid`.

**Step 5: Commit**

```bash
git add README.md assets/logo.svg tests/test_validate_protocol.py
git commit -m "Add Patton Protocol identity and usage docs"
```

### Task 7: Run the complete verification matrix

**Files:**
- Modify: `README.md` only if measured commands expose a stale claim

**Step 1: Run the complete checks**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_protocol.py .
git diff --check
```

Expected: All tests pass, validator reports `valid`, and Git reports no whitespace errors.

**Step 2: Inspect the final package**

Run: `find . -maxdepth 3 -type f | sort`

Confirm that the canonical `SKILL.md` stands alone, every linked resource is present, adapters remain optional, and no generated cache or vendor credential enters the repository.

**Step 3: Commit any measured documentation correction**

```bash
git add README.md
git commit -m "Align package documentation with validation"
```

Skip this commit when no correction is needed.
