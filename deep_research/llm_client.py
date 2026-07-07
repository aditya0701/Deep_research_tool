"""Thin wrapper around DeepSeek's OpenAI-compatible chat completions API.

As of the 2026-04-24 V4 release, `deepseek-chat`/`deepseek-reasoner` are
deprecated (removed 2026-07-24) in favor of `deepseek-v4-flash` /
`deepseek-v4-pro`, which fold chat/reasoner into one model with a
`thinking: {"type": "enabled"|"disabled"}` toggle sent via `extra_body`
(not part of the OpenAI request spec). When thinking is on, reasoning comes
back as a `reasoning_content` field alongside `content`, not a typed content
block - read defensively (`getattr(..., None)`) since it isn't part of the
official OpenAI spec DeepSeek's API otherwise mirrors.

Tool schemas are accepted here in the same flat shape agent.py already uses
(name, description, input_schema) and converted internally to OpenAI's
nested {"type": "function", "function": {...}} shape - so agent.py's TOOLS
definition doesn't need to know which provider it's talking to.
"""
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DEFAULT_MODEL = os.environ.get("RESEARCH_AGENT_MODEL", "deepseek-v4-flash")
DEFAULT_MAX_TOKENS = int(os.environ.get("RESEARCH_AGENT_MAX_TOKENS", "8000"))
DEFAULT_THINKING = os.environ.get("DEEPSEEK_THINKING", "true").strip().lower() in ("1", "true", "yes")


def _to_openai_tools(tools: list) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


class LLMClient:
    def __init__(self, model: str = DEFAULT_MODEL, thinking: bool = DEFAULT_THINKING):
        self.client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
        self.model = model
        self.thinking = thinking

    def call(self, system: str, messages: list, tools: list, max_tokens: int = DEFAULT_MAX_TOKENS):
        full_messages = [{"role": "system", "content": system}] + messages
        kwargs = dict(model=self.model, messages=full_messages, max_tokens=max_tokens)
        if tools:
            kwargs["tools"] = _to_openai_tools(tools)
        # V4 unifies deepseek-chat/deepseek-reasoner into one model with a thinking toggle,
        # sent via extra_body since it isn't part of the OpenAI-spec request shape. Legacy
        # model names (deepseek-chat/deepseek-reasoner, deprecated 2026-07-24) don't accept
        # this field, so only send it for the new v4 models.
        if self.model.startswith("deepseek-v4"):
            kwargs["extra_body"] = {"thinking": {"type": "enabled" if self.thinking else "disabled"}}
        return self.client.chat.completions.create(**kwargs)
