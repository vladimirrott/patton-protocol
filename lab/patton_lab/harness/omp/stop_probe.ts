/**
 * S7 probe for oh-my-pi: refuse the main session's stop at `session_stop` a fixed number of times.
 *
 *   PATTON_STOP_REFUSALS=<n>        how many stops to refuse (default 1)
 *   PATTON_STOP_FORM=block|continue block: {decision: "block", reason} (Claude/Codex form, documented uncapped);
 *                                   continue: {continue: true, additionalContext} (advisory, documented cap 8)
 *
 * Not part of the gate. It measures whether a refusal makes omp send another model request and how omp caps
 * repeated refusals.
 */
const LIMIT = Number(process.env.PATTON_STOP_REFUSALS ?? "1");
const FORM = process.env.PATTON_STOP_FORM ?? "block";
let refused = 0;

export default function (pi: any) {
	pi.on("session_stop", async () => {
		if (refused >= LIMIT) return undefined;
		refused += 1;
		const text = `PATTON_STOP_REFUSED ${refused}: the gate refused this stop; continue the mission.`;
		if (FORM === "continue") return { continue: true, additionalContext: text };
		return { decision: "block", reason: text };
	});
}
