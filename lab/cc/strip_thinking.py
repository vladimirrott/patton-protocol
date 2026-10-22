"""LiteLLM proxy hook: drop thinking blocks from the history Claude Code sends back.

Groq rejects an assistant message that carries `thinking_blocks`. Claude Code stores the model's
reasoning as thinking blocks and replays them on the next turn, so the proxy removes them first.
The lab never stored provider reasoning text either (see agent.py), so nothing is lost.
"""
from litellm.integrations.custom_logger import CustomLogger

_THINKING = ("thinking", "redacted_thinking")


class StripThinking(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        for message in data.get("messages") or []:
            content = message.get("content")
            if isinstance(content, list):
                message["content"] = [b for b in content if not (isinstance(b, dict) and b.get("type") in _THINKING)]
            message.pop("thinking_blocks", None)
            message.pop("reasoning_content", None)
        return data


proxy_handler_instance = StripThinking()
