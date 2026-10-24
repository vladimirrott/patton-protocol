/**
 * Patton gate for Pi (earendil-works/pi 1.4.0): on every tool call, ask the shared bridge and block unless it allows.
 *
 *   pi --mode json -e /repo/lab/patton_lab/harness/pi/gate.ts ...
 *
 * The bridge runs synchronously: `python3 <PATTON_BRIDGE> pi`, the call as JSON on stdin, one JSON verdict on stdout.
 * Only exit 0 with exactly {"block": false} allows. A spawn error, a timeout (PATTON_BRIDGE_TIMEOUT_MS, default
 * 15000), a non-zero exit, a signal, or output that is not a verdict blocks the call with a reason the model sees.
 * Pi also blocks when a tool_call handler throws ("A tool_call handler failure blocks the tool as a fail-safe",
 * docs/extensions.md), and the handler catches everything itself so the reason stays ours.
 */
import { spawnSync } from "node:child_process";

const DIALECT = "pi";
const BRIDGE = process.env.PATTON_BRIDGE || "/repo/lab/patton_lab/harness/bridge.py";
const PYTHON = process.env.PATTON_PYTHON || "python3";
const RAW_TIMEOUT = Number(process.env.PATTON_BRIDGE_TIMEOUT_MS);
const TIMEOUT_MS = Number.isFinite(RAW_TIMEOUT) && RAW_TIMEOUT > 0 ? RAW_TIMEOUT : 15000;

type Verdict = { block: boolean; reason: string };

function refuse(why: string): Verdict {
	return { block: true, reason: `Patton gate: ${why}, so this call is blocked.` };
}

function tail(text: unknown): string {
	const s = String(text ?? "").trim();
	return s ? ` (${s.slice(-300)})` : "";
}

export function askBridge(payload: unknown): Verdict {
	let r;
	try {
		r = spawnSync(PYTHON, [BRIDGE, DIALECT], {
			input: JSON.stringify(payload),
			encoding: "utf8",
			timeout: TIMEOUT_MS,
			killSignal: "SIGKILL",
			maxBuffer: 1 << 20,
			stdio: ["pipe", "pipe", "pipe"],
		});
	} catch (err) {
		return refuse(`the bridge could not be started (${String(err).slice(0, 200)})`);
	}
	if (r.error) {
		const code = (r.error as NodeJS.ErrnoException).code;
		if (code === "ETIMEDOUT") return refuse(`the bridge did not answer within ${TIMEOUT_MS} ms`);
		return refuse(`the bridge failed to run (${code || r.error.message})`);
	}
	if (r.status !== 0) {
		const how = r.status === null ? `was killed by ${r.signal}` : `exited with status ${r.status}`;
		return refuse(`the bridge ${how}${tail(r.stderr)}`);
	}
	let out: any;
	try {
		out = JSON.parse(String(r.stdout).trim());
	} catch {
		return refuse("the bridge printed no readable verdict");
	}
	if (out && out.block === false) return { block: false, reason: "" };
	if (out && out.block === true && typeof out.reason === "string" && out.reason) return { block: true, reason: out.reason };
	return refuse("the bridge printed no readable verdict");
}

export default function (pi: any) {
	pi.on("tool_call", async (event: any, ctx: any) => {
		let verdict: Verdict;
		try {
			verdict = askBridge({
				tool: event.toolName,
				input: event.input,
				cwd: (ctx && ctx.cwd) || process.cwd(),
				session: `pi-${process.pid}`,
			});
		} catch (err) {
			verdict = refuse(`the extension failed (${String(err).slice(0, 200)})`);
		}
		return verdict.block ? { block: true, reason: verdict.reason } : undefined;
	});
}
