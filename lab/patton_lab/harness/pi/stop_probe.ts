/**
 * S7 probe for Pi: refuse the agent's stop at `agent_before_settle` a fixed number of times.
 *
 *   PATTON_STOP_REFUSALS=<n>   how many settles to refuse (default 1)
 *   PATTON_STOP_ENTRY=1|0      1: append a custom_message the model sees (the documented way to make the context
 *                              runnable again); 0: return only {continue: true}
 *
 * Not part of the gate. It exists to measure whether a refusal makes Pi send another model request and whether
 * Pi caps repeated refusals.
 */
const LIMIT = Number(process.env.PATTON_STOP_REFUSALS ?? "1");
const WITH_ENTRY = (process.env.PATTON_STOP_ENTRY ?? "1") === "1";
let refused = 0;

export default function (pi: any) {
	pi.on("agent_before_settle", async () => {
		if (refused >= LIMIT) return undefined;
		refused += 1;
		if (!WITH_ENTRY) return { continue: true };
		return {
			continue: true,
			entries: [
				{
					type: "custom_message",
					customType: "patton-stop",
					content: `PATTON_STOP_REFUSED ${refused}: the gate refused this stop; continue the mission.`,
					display: true,
				},
			],
		};
	});
}
