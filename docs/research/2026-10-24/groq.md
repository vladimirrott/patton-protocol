# Groq offering for a parallel coding-agent benchmark (budget about $15)

Read date for every source below: 2026-10-24.
Method: WebFetch returned HTTP 403 for console.groq.com, and groq.com/pricing redirects to the homepage (no price table). The HTML of the console.groq.com/docs pages was then fetched directly and converted to text. The console `llms-full.txt` is stale (no tables, lists deprecated models as live), so it was not used for numbers. Context7 `/llmstxt/console_groq_llms-full_txt` is that same stale file, so it was used only as a cross-check of the tool-use table.
No API key was read or used and no Groq API call was made.

Sources (S1..S9, all first-party):
- S1 https://console.groq.com/docs/models
- S2 https://console.groq.com/docs/rate-limits
- S3 https://console.groq.com/docs/prompt-caching
- S4 https://console.groq.com/docs/batch
- S5 https://console.groq.com/docs/service-tiers and https://console.groq.com/docs/flex-processing
- S6 https://console.groq.com/docs/tool-use/overview and https://console.groq.com/docs/tool-use/local-tool-calling
- S7 https://console.groq.com/docs/model/openai/gpt-oss-120b, .../openai/gpt-oss-20b, .../qwen/qwen3.8-27b, .../openai/gpt-oss-safeguard-20b, .../minimaxai/minimax-m2.7
- S8 https://console.groq.com/docs/deprecations
- S9 https://console.groq.com/docs/reasoning and https://console.groq.com/docs/structured-outputs

## 1. Models with tool calling

S6 states all hosted models support tool use. Live list (S1, S6):

| Model id | Status (S1) | Open weight | Context | Max output | Input $/M | Cached input $/M | Output $/M | Parallel tool calls (S6) |
|---|---|---|---|---|---|---|---|---|
| openai/gpt-oss-120b | Production | yes | 131,072 | 65,536 | 0.15 | 0.075 | 0.60 | No |
| openai/gpt-oss-20b | Production | yes | 131,072 | 65,536 | 0.075 | 0.037 | 0.30 | No |
| qwen/qwen3.8-27b | Preview | yes (Alibaba Qwen) | 131,072 | 16,384 | 0.80 | none listed | 4.00 | Yes |
| openai/gpt-oss-safeguard-20b | Preview | yes | 131,072 | 65,536 | 0.075 | 0.037 | 0.30 | No |
| minimaxai/minimax-m2.7 | Preview, "Enterprise" | yes (MiniMax) | 196,608 | 131,072 | Contact Sales | n/a | Contact Sales | Yes |
| meta-models/muse-spark-open | Preview, "Enterprise" | listed as open in name only; not confirmed | 131,072 | 131,072 | Contact Sales | n/a | Contact Sales | Yes |
| llama-3.3-70b-versatile | Production row, "Enterprise" | yes | 131,072 | 32,768 | Contact Sales | n/a | Contact Sales | Yes |
| llama-3.1-8b-instant | Production row, "Enterprise" | yes | 131,072 | 131,072 | Contact Sales | n/a | Contact Sales | Yes |

Notes:
- Prices: S7 per-model pages and S1 table. gpt-oss-20b cached input is shown as $0.037 (changelog also says $0.037 vs $0.075); true half would be $0.0375. Qwen 3.8 27B page shows no cached price and S3 lists caching only for the three gpt-oss ids.
- The gpt-oss-safeguard model is a safety classifier; ignore for coding agents.
- Enterprise models have no public price or self-serve limit in S1. Not usable on a $15 self-serve budget.
- Kimi K2, DeepSeek, Llama 4 and Qwen3-32B no longer exist on the live list (see deprecations).

Deprecations (S8):

| Model | Shutdown | Replacement named by Groq |
|---|---|---|
| groq/compound, groq/compound-mini | 2026-09-21 | none |
| qwen/qwen3.6-27b | 2026-09-14 | qwen/qwen3.8-27b |
| llama-3.1-8b-instant | 2026-08-16 | openai/gpt-oss-20b |
| llama-3.3-70b-versatile | 2026-08-16 | openai/gpt-oss-120b (or qwen3.6-27b) |
| qwen/qwen3-32b | 2026-07-17 | openai/gpt-oss-120b |
| meta-llama/llama-4-scout-17b-16e-instruct | 2026-07-17 | openai/gpt-oss-120b |
| moonshotai/kimi-k2-instruct-0905 | 2026-04-15 | openai/gpt-oss-120b |
| meta-llama/llama-4-maverick-17b-128e-instruct | 2026-03-09 | openai/gpt-oss-120b |
| meta-llama/llama-guard-4-12b | 2026-03-05 | openai/gpt-oss-safeguard-20b |

Unconfirmed: S8 says llama-3.1-8b-instant and llama-3.3-70b-versatile shut down 2026-08-16, yet S1 and S6 still list them as Enterprise/Contact Sales. Treat them as unavailable self-serve.

## 2. Rate limits

S2: "Rate limits apply at the organization level, not individual users." The tables are keyed by model id, so TPM/RPM are tracked per model id under the org. S2 does not state in words that two different models draw from separate buckets. Per-model rows strongly imply it, but I could not confirm it from prose. Verify with the `x-ratelimit-*` headers on two models in a smoke test before relying on it.
Headers (S2): `x-ratelimit-limit-requests` is RPD, `x-ratelimit-limit-tokens` is TPM; `retry-after` is set only on 429.
"Cached tokens do not count towards your rate limits" (S2, S3), but S3 adds they are subtracted after processing, so parallel bursts can still trip limits.

Free plan (S2 table):

| Model | RPM | RPD | TPM | TPD |
|---|---|---|---|---|
| openai/gpt-oss-120b | 30 | 1K | 8K | 200K |
| openai/gpt-oss-20b | 30 | 1K | 8K | 200K |
| openai/gpt-oss-safeguard-20b | 3 | 1K | 2K | 200K |
| qwen/qwen3.8-27b | 30 | 1K | 8K | 200K |

Developer plan (S1 "Rate limits (developer plan)" column; the S2 developer tab did not render in the fetched HTML, so RPD and TPD are NOT CONFIRMED):

| Model | RPM | TPM |
|---|---|---|
| openai/gpt-oss-120b | 1K | 250K |
| openai/gpt-oss-20b | 1K | 250K |
| qwen/qwen3.8-27b | 1K | 250K |
| openai/gpt-oss-safeguard-20b | 1K | 150K |

Consequence for this benchmark:
- Free tier is unusable: TPM 8K is below one 9,000-token call, and TPD 200K is about 22 such calls.
- Developer tier: 250K TPM / 9,000 ≈ 27 uncached calls per minute per model, which is about 2.3 runs/min (12 calls each) if nothing is cached. With 80 % cached (not counted), counted tokens per call ≈ 1,800 + 400 = 2,200, so about 113 calls/min, 9 runs/min. RPM 1K is not binding.
- Developer plan is required for Batch and Flex (S2).

## 3. Batch API (S4)

- 50 % discount vs synchronous; separate limits that do not touch synchronous limits.
- Window: 24 hours to 7 days, set by you. Groq processes "as many requests as our capacity allows".
- Chat models: gpt-oss-20b, gpt-oss-120b, llama-3.3-70b-versatile, llama-3.1-8b-instant, llama-guard-4-12b. Qwen 3.8 27B is not on the list.
- Discount does not stack with caching: all batch tokens bill at the 50 % rate (S4).
- Does not suit multi-turn agent loops. Each turn needs the previous answer and the tool result, and every turn would wait up to 24 h to 7 days, so a 12-turn run could take 12 windows. Fit only for single-shot, independent calls (for example judging finished transcripts).
- S5: the Batch API does not accept `service_tier`.

## 4. Service tiers (S5)

| Tier | Meaning | Price | Limits | Failure behaviour |
|---|---|---|---|---|
| on_demand (default) | standard | list price | table above | 429 with `retry-after`; occasional queue latency at peak |
| flex | best effort, higher throughput | same as on-demand (S5 flex page) | 10x on-demand limits; paid customers only; all models | fails fast with HTTP 498, error `capacity_exceeded`; retry with jittered backoff |
| auto | picks best tier available | n/a | n/a | n/a |
| performance | low latency, enterprise only | not public | n/a | n/a |

Flex gives no price discount, only headroom. Useful if 250K TPM throttles parallelism. LiteLLM would need `service_tier: "flex"` passed through and 498 treated as retryable (not confirmed that LiteLLM retries 498 by default).

## 5. Prompt caching (S3)

- Models: openai/gpt-oss-20b, openai/gpt-oss-120b, openai/gpt-oss-safeguard-20b only.
- Automatic, no code change, no extra fee. 50 % discount on cached input tokens; discount applies only on actual hits, hits not guaranteed.
- Exact prefix match; put system prompt, tool definitions and few-shot first. Expiry: 2 hours without use.
- Minimum prefix: "ranges from 128 to 1024 tokens depending on the specific model"; per-model value not stated (NOT CONFIRMED).
- Reporting: `usage.prompt_tokens_details.cached_tokens` (S3 example shows `cached_tokens: 4608`); hit rate = cached_tokens / prompt_tokens. First request shows 0.
- Caveat for LiteLLM: ensure it forwards and logs `prompt_tokens_details`; and do not put timestamps or run ids before the static prefix.

## 6. Tool-calling quirks (S6, S9, S7)

- S6 table: gpt-oss-120b, gpt-oss-20b, safeguard: parallel tool use "No"; Qwen 3.8 27B, MiniMax M2.7, Muse, Llama: "Yes". The body of S6 says "many models support parallel tool use". Send one tool call per turn to gpt-oss to be safe.
- Invalid tool call: HTTP 400 with `failed_generation` in the body (S6). Docs recommend retry; temperature 0 to 0.5 for tool calling (S6 examples).
- `reasoning_format` must be `parsed` or `hidden` when tools or JSON mode are on; `raw` gives a 400 (S9). `include_reasoning` and `reasoning_format` are mutually exclusive.
- `reasoning_effort`: gpt-oss supports low, medium, high only. Qwen 3.8 27B also supports `none` and `default` (default returns no reasoning tokens). Lower effort cuts output tokens, which are billed.
- Harmony: S7 says use the Harmony chat format (System > Developer > User > Assistant) for gpt-oss. Via the chat-completions endpoint Groq applies it server side; the docs give no extra client steps (NOT CONFIRMED beyond that).
- Strict JSON schema: `strict: true` on gpt-oss-20b, gpt-oss-120b, qwen3.8-27b, muse. But "Streaming and tool use are not currently supported with Structured Outputs" (S9), so do not combine `response_format` json_schema with `tools`. Tool-parameter `strict` flag is not documented in these pages (NOT CONFIRMED).
- Built-in tools (browser search, code execution) only on gpt-oss models; browser search is incompatible with structured outputs.
- MiniMax guidance (S7): keep thinking content in history for multi-turn; irrelevant for self-serve.

## 7. Cost per agent run

Inputs: n = 12 calls; I = 12 × 9,000 = 108,000 input tokens; O = 12 × 400 = 4,800 output tokens.
Formula: cost = I·p_in/10⁶ + O·p_out/10⁶. With cache hit share h of input tokens: I·(h·p_c + (1−h)·p_in)/10⁶ + O·p_out/10⁶.
Hit share h is an assumption, not a measured value. In a growing prompt every call after the first re-sends the previous prompt, so h up to about 0.9 is plausible if requests land on warm cache and the prefix is stable.

gpt-oss-120b (0.15 / 0.075 / 0.60):
- Input: 108,000 × 0.15 / 10⁶ = $0.01620. Output: 4,800 × 0.60 / 10⁶ = $0.00288. Total no cache: $0.01908.
- h = 0.5: 108,000 × (0.5×0.075 + 0.5×0.15)/10⁶ = $0.01215; total $0.01503.
- h = 0.8: 108,000 × (0.8×0.075 + 0.2×0.15)/10⁶ = $0.00972; total $0.01260.
- h = 0.9: $0.00891 input; total $0.01179.
- Batch (50 %, no cache stacking): $0.00954 (not usable for multi-turn).

gpt-oss-20b (0.075 / 0.037 / 0.30):
- Input: $0.00810. Output: $0.00144. Total no cache: $0.00954.
- h = 0.5: $0.00605 input; total $0.00749.
- h = 0.8: $0.00482 input; total $0.00626.
- h = 0.9: $0.00441 input; total $0.00585.
- Batch: $0.00477.

qwen/qwen3.8-27b (0.80 / no cache / 4.00):
- Input: 108,000 × 0.80 / 10⁶ = $0.08640. Output: 4,800 × 4.00 / 10⁶ = $0.01920. Total: $0.10560. No caching, no batch.

Enterprise-only models (MiniMax M2.7, Muse Spark Open, Llama 3.x): no public price, cannot be costed.

Runs affordable on $15 (15 / cost, rounded):

| Model | No cache | h = 0.8 |
|---|---|---|
| gpt-oss-120b | 786 | 1,190 |
| gpt-oss-20b | 1,572 | 2,397 |
| qwen3.8-27b | 142 | 142 |

Sensitivity: gpt-oss and Qwen 3.8 are reasoning models and reasoning tokens bill as output. If the real output is 1,000 tokens/call (reasoning included) rather than 400, cost without cache rises to $0.0234 (120b), $0.0117 (20b), $0.1344 (qwen), cutting 120b to about 641 runs. Measure `usage.completion_tokens` in a 5-run pilot before sizing the benchmark.

## Recommendation

1. Developer plan is mandatory (free TPM 8K is below one call). Confirm RPD/TPD on the console limits page.
2. Use openai/gpt-oss-120b as primary (production, open weight, cached input, about $0.013 to $0.019 per run) and gpt-oss-20b as the cheap arm. Qwen 3.8 27B is about 5.5× the cost of 120b per run (0.1056 / 0.01908) and is Preview.
3. Skip Batch for agent loops. Consider flex only if 250K TPM throttles parallelism, and handle 498.
4. Keep the system prompt and tool definitions first and byte-stable so caching engages; log `prompt_tokens_details.cached_tokens` through LiteLLM.

## Not confirmed

- groq.com/pricing (no price table in the fetched page; prices come from per-model docs pages).
- Developer-plan RPD and TPD.
- Whether different model ids have fully independent buckets (implied by per-model rows, not stated).
- Minimum cacheable prefix per model.
- Whether Muse Spark Open is open-weight.
- Whether Llama 3.x are still served at all after the 2026-08-16 shutdown date.
