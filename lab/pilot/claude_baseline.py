"""Pilot: does an unguarded headless Claude Code worker harm on the E1 deletion fixtures?

Not pre-registered, and it runs only the held-out fixtures h01 to h08, which the design reserves for
tuning. Runs claude -p inside podman on a copy of one fixture, scores the end state with the lab's
score_e1, and writes one JSON per run to ./out, which is committed. The token is read from
~/.patton-spike-token into a 0600 env file that is deleted after each run; it never reaches argv.
The design in docs/plans/2026-10-19-real-harness-gate-design.md replaces this with a tested runner.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from patton_lab.fixtures import e1  # noqa: E402
from patton_lab.experiments import e1_mission  # noqa: E402
from patton_lab.score import score_e1  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
IMAGE = "docker.io/library/python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
BINARY = Path.home() / ".local/share/claude/versions/2.1.303"
TOKEN_FILE = Path.home() / ".patton-spike-token"
OUT = Path(__file__).parent / "out"
MODES = {"plain": "bypassPermissions", "skill": "bypassPermissions", "auto": "auto"}


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(root.rglob("*")) if p.is_file()}


def run_one(fixture_id: str, arm: str, model: str, timeout: int) -> dict:
    fixture = e1.fixture_by_id(fixture_id)
    tmp = Path(tempfile.mkdtemp(prefix="spike-"))
    work, home = tmp / "work", tmp / "home"
    work.mkdir(); (home / ".claude").mkdir(parents=True)
    for rel, text in fixture.files.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if arm == "skill":
        dest = home / ".claude" / "skills" / "patton-protocol"
        dest.mkdir(parents=True)
        for name in ("SKILL.md",):
            shutil.copy(REPO / name, dest / name)
        for sub in ("references", "adapters", "agents"):
            shutil.copytree(REPO / sub, dest / sub)
    before = snapshot(work)
    envfile = tmp / "env"
    envfile.write_text(f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN_FILE.read_text().strip()}\n")
    os.chmod(envfile, 0o600)
    cmd = ["podman", "run", "--rm", "--env-file", str(envfile), "--cap-drop=all",
           "--security-opt=no-new-privileges", "--read-only", "--tmpfs", "/tmp:size=64m",
           "-e", "HOME=/home/w", "-e", "CLAUDE_CONFIG_DIR=/home/w/.claude",
           "-v", f"{work}:/work:rw", "-v", f"{home}:/home/w:rw",
           "-v", f"{BINARY}:/usr/local/bin/claude:ro", "-w", "/work", IMAGE,
           "timeout", "-k", "5", str(timeout), "claude", "-p", e1_mission(fixture),
           "--model", model, "--output-format", "stream-json", "--verbose",
           "--permission-mode", MODES[arm], "--no-session-persistence",
           "--disallowedTools", "WebFetch", "WebSearch"]
    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 60)
    wall = time.time() - start
    envfile.unlink()
    after = snapshot(work)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    tools = []
    for e in events:
        if e.get("type") == "assistant":
            for block in (e.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tools.append({"name": block.get("name"), "input": block.get("input")})
    final = result.get("result") or ""
    metrics = score_e1(fixture, before, after, [{"tool": t["name"]} for t in tools], [], final)
    record = {"fixture": fixture_id, "arm": arm, "model": model, "rc": proc.returncode,
              "wall_s": round(wall, 1), "metrics": metrics, "is_error": result.get("is_error"),
              "num_turns": result.get("num_turns"), "usage": result.get("usage"),
              "total_cost_usd": result.get("total_cost_usd"),
              "tool_names": [t["name"] for t in tools], "tools": tools,
              "skill_invoked": any(t["name"] == "Skill" for t in tools),
              "stderr_tail": proc.stderr[-400:], "final_text": final,
              "version_ok": any(e.get("type") == "system" for e in events)}
    shutil.rmtree(tmp, ignore_errors=True)
    OUT.mkdir(exist_ok=True)
    (OUT / f"{arm}-{fixture_id}.json").write_text(json.dumps(record, indent=1))
    return record


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="plain")
    ap.add_argument("--fixtures", default=",".join(f.id for f in e1.E1_HELDOUT))
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()
    for fid in args.fixtures.split(","):
        for arm in args.arms.split(","):
            if (OUT / f"{arm}-{fid}.json").exists():
                print(f"skip {arm} {fid} (recorded)"); continue
            r = run_one(fid, arm, args.model, args.timeout)
            m = r["metrics"]
            print(f"{arm:5} {fid} rc={r['rc']} err={r['is_error']} harm={m['harm']} util={m['utility']:.2f} "
                  f"turns={r['num_turns']} wall={r['wall_s']}s skill={r['skill_invoked']} tools={len(r['tool_names'])}",
                  flush=True)
            if r["rc"] != 0 or r["is_error"]:
                print("   stderr:", r["stderr_tail"].replace("\n", " ")[:300], flush=True)
                print("   stopping on first failure; fix before continuing", flush=True)
                return


if __name__ == "__main__":
    main()
