"""Pilot: does an unguarded headless Claude Code worker harm on the E1 deletion fixtures?

Not pre-registered, and it runs only the held-out fixtures h01 to h08, which the design reserves for
tuning. Runs claude -p inside podman on a copy of one fixture, scores the end state with the lab's
score_e1, and writes one JSON per run to ./out, which is committed. The token is read from
~/.patton-spike-token into a 0600 env file that is deleted after each run; it never reaches argv.
The design in docs/plans/2026-10-19-real-harness-gate-design.md replaces this with a tested runner.
"""
import argparse, getpass, json, os, shutil, subprocess, sys, tempfile, time
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
KEPT_EVENTS = ("assistant", "user", "result")  # the rest is streaming noise: thousands of thinking_tokens ticks


def scrub_record(record: dict) -> dict:
    """Drop streaming noise and replace the host user name, which `ls -l` prints as the file owner."""
    events = record.get("events") or []
    record["event_counts"] = {}
    for e in events:
        key = f"{e.get('type')}:{e.get('subtype')}"
        record["event_counts"][key] = record["event_counts"].get(key, 0) + 1
    record["events"] = [e for e in events if e.get("type") in KEPT_EVENTS]
    return json.loads(json.dumps(record).replace(getpass.getuser(), "worker"))


MODES = {"plain": "bypassPermissions", "skill": "bypassPermissions", "auto": "auto", "probe": "bypassPermissions"}


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(root.rglob("*")) if p.is_file()}


PROXY_URL = "http://10.0.2.2:4000"  # the host's loopback as slirp4netns exposes it to the container
GROQ_MODEL = "gpt-oss-120b"


def backend_args(backend: str, envfile: Path) -> tuple[list[str], str]:
    """Container arguments that select the model provider, and the model name to pass to claude."""
    if backend == "anthropic":
        envfile.write_text(f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN_FILE.read_text().strip()}\n")
        os.chmod(envfile, 0o600)
        return ["--env-file", str(envfile)], ""
    # Groq through the host's LiteLLM proxy (with_groq_proxy.sh). The auth token is a placeholder: the real
    # key lives only in the proxy's environment.
    env = {"ANTHROPIC_BASE_URL": PROXY_URL, "ANTHROPIC_AUTH_TOKEN": "placeholder",
           "ANTHROPIC_DEFAULT_OPUS_MODEL": GROQ_MODEL, "ANTHROPIC_DEFAULT_SONNET_MODEL": GROQ_MODEL,
           "ANTHROPIC_DEFAULT_HAIKU_MODEL": GROQ_MODEL, "ANTHROPIC_SMALL_FAST_MODEL": GROQ_MODEL,
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1"}
    args = ["--network", "slirp4netns:allow_host_loopback=true"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    return args, GROQ_MODEL


MAX_ATTEMPTS = 3


def run_one(fixture_id: str, arm: str, model: str, timeout: int, backend: str = "anthropic",
            prior: tuple = ()) -> dict:
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
    extra: list[str] = []
    if arm == "probe":
        shutil.copy(Path(__file__).with_name("deny_rm_probe.py"), home / "hook.py")
        settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3 /home/user/hook.py"}]}]}}
        (home / "settings.json").write_text(json.dumps(settings))
        extra = ["--settings", "/home/user/settings.json"]
    before = snapshot(work)
    envfile = tmp / "env"
    backend_flags, backend_model = backend_args(backend, envfile)
    model = backend_model or model
    cmd = ["podman", "run", "--rm", "--userns=keep-id", *backend_flags, "--cap-drop=all",
           "--security-opt=no-new-privileges", "--read-only", "--tmpfs", "/tmp:size=64m",
           "-e", "HOME=/home/user", "-e", "CLAUDE_CONFIG_DIR=/home/user/.claude",
           "-v", f"{work}:/work:rw", "-v", f"{home}:/home/user:rw",
           "-v", f"{BINARY}:/usr/local/bin/claude:ro", "-w", "/work", IMAGE,
           "timeout", "-k", "5", str(timeout), "claude", "-p", e1_mission(fixture),
           "--model", model, "--output-format", "stream-json", "--verbose",
           "--permission-mode", MODES[arm], "--no-session-persistence",
           "--disallowedTools", "WebFetch", "WebSearch", *extra]
    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 60)
    wall = time.time() - start
    envfile.unlink(missing_ok=True)
    probe_log = (home / "probe.log").read_text() if (home / "probe.log").exists() else ""
    after = snapshot(work)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    if not result and len(prior) + 1 < MAX_ATTEMPTS:
        # No result event: the worker never finished (Groq's malformed tool call crashes the proxy stream
        # and takes the worker with it). Retry on a fresh copy and keep a record of the failed attempt.
        shutil.rmtree(tmp, ignore_errors=True)
        failed = {"rc": proc.returncode, "wall_s": round(wall, 1), "events": len(events)}
        return run_one(fixture_id, arm, model, timeout, backend, prior + (failed,))
    tools = []
    for e in events:
        if e.get("type") == "assistant":
            for block in (e.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tools.append({"name": block.get("name"), "input": block.get("input")})
    final = result.get("result") or ""

    def scrub(event):  # provider reasoning text is never stored, only its length (as in agent.py)
        message = event.get("message") if isinstance(event.get("message"), dict) else None
        if message and isinstance(message.get("content"), list):
            blocks = [{"type": "thinking", "chars": len(b.get("thinking") or "")} if b.get("type") == "thinking" else b
                      for b in message["content"] if isinstance(b, dict)]
            event = {**event, "message": {**message, "content": blocks}}
        return event

    metrics = score_e1(fixture, before, after, [{"tool": t["name"]} for t in tools], [], final)
    record = {"fixture": fixture_id, "arm": arm, "model": model, "backend": backend, "attempts": list(prior) + [{"rc": proc.returncode, "wall_s": round(wall, 1)}], "rc": proc.returncode,
              "wall_s": round(wall, 1), "metrics": metrics, "is_error": result.get("is_error"),
              "num_turns": result.get("num_turns"), "usage": result.get("usage"),
              "total_cost_usd": result.get("total_cost_usd"),
              "tool_names": [t["name"] for t in tools], "tools": tools,
              "skill_invoked": any(t["name"] == "Skill" for t in tools),
              "stderr_tail": "\n".join(l for l in proc.stderr.splitlines() if "CNI config" not in l)[-800:],
              "stdout_tail": proc.stdout[-800:], "final_text": final,
              "probe_log": probe_log, "after": after, "events": [scrub(e) for e in events],
              "version_ok": any(e.get("type") == "system" for e in events)}
    shutil.rmtree(tmp, ignore_errors=True)
    OUT.mkdir(exist_ok=True)
    (OUT / f"{backend}-{arm}-{fixture_id}.json").write_text(json.dumps(scrub_record(record), indent=1))
    return record


def main() -> None:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="plain")
    ap.add_argument("--fixtures", default=",".join(f.id for f in e1.E1_HELDOUT))
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--backend", choices=("anthropic", "groq"), default="anthropic")
    ap.add_argument("--out", type=Path, default=OUT, help="where to write records (default: ./out)")
    args = ap.parse_args()
    OUT = args.out
    for fid in args.fixtures.split(","):
        for arm in args.arms.split(","):
            name = f"{args.backend}-{arm}-{fid}.json"
            if (OUT / name).exists():
                print(f"skip {name} (recorded)"); continue
            r = run_one(fid, arm, args.model, args.timeout, args.backend)
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
