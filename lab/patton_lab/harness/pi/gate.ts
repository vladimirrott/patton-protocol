/**
 * Patton gate for Pi (earendil-works/pi 1.4.0): on every tool call, ask the shared bridge and block unless it allows;
 * at the agent's stop, ask the bridge whether the gate refuses it.
 *
 *   pi --mode json -e /repo/lab/patton_lab/harness/pi/gate.ts ...
 *
 * The bridge runs synchronously: `python3 <PATTON_BRIDGE> pi`, the event as JSON on stdin, one JSON verdict on stdout.
 *
 * Tool calls fail closed. Only exit 0 with {"block": false} allows. A spawn error, a timeout
 * (PATTON_BRIDGE_TIMEOUT_MS, default 15000), a non-zero exit, a signal, or output that is not a verdict blocks the
 * call with a reason the model sees. Pi also blocks when a tool_call handler throws ("A tool_call handler failure
 * blocks the tool as a fail-safe", docs/extensions.md), and the handler catches everything itself so the reason
 * stays ours. A verdict may carry "context", the gate's discipline reminder: it is appended to the tool's result at
 * `tool_result` (Pi's way to annotate a result), or to the refusal when the call is blocked.
 *
 * Stops fail open. At `agent_before_settle` the extension sends {"event": "Stop", "final": <the last assistant
 * text>} and refuses the stop only on exit 0 with {"block": true, "reason"}: it then appends the reason as a
 * custom_message, which the model reads in its next request, and returns continue: true (a bare continue: true is
 * rejected by Pi as "without runnable model context"). Anything else, including a timeout
 * (PATTON_BRIDGE_STOP_TIMEOUT_MS, default 25000), lets the agent stop. Pi has no cap on refused settles, so the
 * bridge caps them (PATTON_BRIDGE_STOP_CAP, default 4).
 */
import { spawnSync } from "node:child_process";

const DIALECT = "pi";
const BRIDGE = process.env.PATTON_BRIDGE || "/repo/lab/patton_lab/harness/bridge.py";
const PYTHON = process.env.PATTON_PYTHON || "python3";

function ms(name: string, fallback: number): number {
	const raw = Number(process.env[name]);
	return Number.isFinite(raw) && raw > 0 ? raw : fallback;
}

const TIMEOUT_MS = ms("PATTON_BRIDGE_TIMEOUT_MS", 15000);
const STOP_TIMEOUT_MS = ms("PATTON_BRIDGE_STOP_TIMEOUT_MS", 25000);
const SESSION = `pi-${process.pid}`;

type Verdict = { block: boolean; reason: string; context: string };

function refuse(why: string): Verdict {
	return { block: true, reason: `Patton gate: ${why}, so this call is blocked.`, context: "" };
}

function tail(text: unknown): string {
	const s = String(text ?? "").trim();
	return s ? ` (${s.slice(-300)})` : "";
}

function spawnBridge(payload: unknown, timeout: number) {
	return spawnSync(PYTHON, [BRIDGE, DIALECT], {
		input: JSON.stringify(payload),
		encoding: "utf8",
		timeout,
		killSignal: "SIGKILL",
		maxBuffer: 1 << 20,
		stdio: ["pipe", "pipe", "pipe"],
	});
}

export function askBridge(payload: unknown): Verdict {
	let r;
	try {
		r = spawnBridge(payload, TIMEOUT_MS);
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
	const context = out && typeof out.context === "string" ? out.context : "";
	if (out && out.block === false) return { block: false, reason: "", context };
	if (out && out.block === true && typeof out.reason === "string" && out.reason) {
		return { block: true, reason: out.reason, context };
	}
	return refuse("the bridge printed no readable verdict");
}

/** The gate's reason for refusing this stop, or null to let the agent stop. Never throws. */
export function askStop(payload: unknown): string | null {
	try {
		const r = spawnBridge(payload, STOP_TIMEOUT_MS);
		if (r.error || r.status !== 0) {
			console.error(`Patton gate: the stop was not checked (${r.error ? r.error.message : `exit ${r.status}`})`);
			return null;
		}
		const out = JSON.parse(String(r.stdout).trim());
		return out && out.block === true && typeof out.reason === "string" && out.reason ? out.reason : null;
	} catch (err) {
		console.error(`Patton gate: the stop was not checked (${String(err).slice(0, 200)})`);
		return null;
	}
}

function text(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.filter((p: any) => p && p.type === "text" && typeof p.text === "string")
		.map((p: any) => p.text)
		.join("\n");
}

/** The last assistant message's text in a list of agent messages. */
export function lastAssistantText(messages: unknown): string {
	if (!Array.isArray(messages)) return "";
	for (let i = messages.length - 1; i >= 0; i--) {
		const m: any = messages[i];
		if (m && m.role === "assistant") {
			const t = text(m.content);
			if (t.trim()) return t;
		}
	}
	return "";
}

export default function (pi: any) {
	const contexts = new Map<string, string>();

	pi.on("tool_call", async (event: any, ctx: any) => {
		let verdict: Verdict;
		try {
			verdict = askBridge({
				tool: event.toolName,
				input: event.input,
				cwd: (ctx && ctx.cwd) || process.cwd(),
				session: SESSION,
			});
		} catch (err) {
			verdict = refuse(`the extension failed (${String(err).slice(0, 200)})`);
		}
		if (verdict.block) {
			return { block: true, reason: verdict.context ? `${verdict.reason}\n\n${verdict.context}` : verdict.reason };
		}
		if (verdict.context && event && event.toolCallId) contexts.set(String(event.toolCallId), verdict.context);
		return undefined;
	});

	pi.on("tool_result", async (event: any) => {
		const key = event && event.toolCallId ? String(event.toolCallId) : "";
		const context = contexts.get(key);
		if (!context) return undefined;
		contexts.delete(key);
		const content = Array.isArray(event.content) ? event.content : [];
		const result: any = { content: [...content, { type: "text", text: context }] };
		// Pi drops structuredContent when a handler replaces content without it (docs/extensions.md).
		if (event.structuredContent !== undefined) result.structuredContent = event.structuredContent;
		return result;
	});

	pi.on("agent_before_settle", async (event: any, ctx: any) => {
		try {
			const context = (event && event.context) || {};
			const reason = askStop({
				event: "Stop",
				final: lastAssistantText(context.contextMessages || context.llmMessages),
				cwd: (ctx && ctx.cwd) || process.cwd(),
				session: SESSION,
			});
			if (!reason) return undefined;
			return {
				continue: true,
				entries: [{ type: "custom_message", customType: "patton-stop", content: reason, display: true }],
			};
		} catch (err) {
			console.error(`Patton gate: the stop was not checked (${String(err).slice(0, 200)})`);
			return undefined;
		}
	});
}
