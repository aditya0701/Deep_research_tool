"""Thin wrapper around OpenAI-compatible chat completions APIs (DeepSeek, Sarvam).

As of the 2026-04-24 V4 release, `deepseek-chat`/`deepseek-reasoner` are
deprecated (removed 2026-07-24) in favor of `deepseek-v4-flash` /
`deepseek-v4-pro`, which fold chat/reasoner into one model with a
`thinking: {"type": "enabled"|"disabled"}` toggle sent via `extra_body`
(not part of the OpenAI request spec). When thinking is on, reasoning comes
back as a `reasoning_content` field alongside `content`, not a typed content
block - read defensively (`getattr(..., None)`) since it isn't part of the
official OpenAI spec DeepSeek's API otherwise mirrors.

Sarvam (https://api.sarvam.ai/v1) is also OpenAI-compatible and returns
reasoning the same way via `reasoning_content`, so agent.py's defensive
getattr read covers both providers unchanged. Switch providers with the
`LLM_PROVIDER` env var (`deepseek` default, or `sarvam`) - this exists to
A/B the same agent loop against both backends, not as a permanent
multi-provider abstraction.

Tool schemas are accepted here in the same flat shape agent.py already uses
(name, description, input_schema) and converted internally to OpenAI's
nested {"type": "function", "function": {...}} shape - so agent.py's TOOLS
definition doesn't need to know which provider it's talking to.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
# Sarvam credentials are kept in a separate untracked file rather than .env so the
# DeepSeek-only default setup (.env.example) doesn't need to change for people not
# testing Sarvam. load_dotenv() never overwrites a var already set in the environment.
load_dotenv(Path(__file__).resolve().parent.parent / ".sarvanenv")

DEFAULT_PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek").strip().lower()
DEFAULT_THINKING = os.environ.get("DEEPSEEK_THINKING", "true").strip().lower() in ("1", "true", "yes")

# Per-provider base_url / api key / default model / max_tokens ceiling. Adding a
# provider means adding an entry here - LLMClient itself stays generic.
# Sarvam's starter subscription tier hard-caps max_tokens at 4096 (verified live -
# 8000 gets rejected with a 400), well under DeepSeek's ceiling, so each provider
# gets its own default rather than one shared constant.
_PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": os.environ.get("RESEARCH_AGENT_MODEL", "deepseek-v4-flash"),
        "default_max_tokens": 8000,
    },
    "sarvam": {
        "base_url": "https://api.sarvam.ai/v1",
        "api_key_env": "SARVAM_API_KEY",
        "default_model": os.environ.get("SARVAM_MODEL", "sarvam-105b"),
        "default_max_tokens": 4096,
    },
}
DEFAULT_MAX_TOKENS = int(
    os.environ.get("RESEARCH_AGENT_MAX_TOKENS", _PROVIDERS[DEFAULT_PROVIDER]["default_max_tokens"])
)


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
    def __init__(
        self,
        model: str | None = None,
        thinking: bool = DEFAULT_THINKING,
        provider: str | None = None,
        max_tokens: int | None = None,
    ):
        self.provider = (provider or DEFAULT_PROVIDER).strip().lower()
        if self.provider not in _PROVIDERS:
            raise ValueError(f"Unknown LLM_PROVIDER {self.provider!r}; expected one of {list(_PROVIDERS)}")
        config = _PROVIDERS[self.provider]
        self.client = OpenAI(api_key=os.environ[config["api_key_env"]], base_url=config["base_url"])
        self.model = model or config["default_model"]
        self.thinking = thinking
        # RESEARCH_AGENT_MAX_TOKENS in .env is DeepSeek-tuned (8000) and is always present
        # once set, so it can't be used as "unset -> use this provider's default" - it would
        # silently override Sarvam's lower ceiling too. Instead treat each provider's
        # default_max_tokens as a hard cap: honor an explicit override up to that cap, but
        # never send more than the provider actually accepts.
        requested = max_tokens or int(os.environ.get("RESEARCH_AGENT_MAX_TOKENS", config["default_max_tokens"]))
        self.max_tokens = min(requested, config["default_max_tokens"])

    def call(self, system: str, messages: list, tools: list, max_tokens: int | None = None):
        max_tokens = max_tokens or self.max_tokens
        full_messages = [{"role": "system", "content": system}] + messages
        kwargs = dict(model=self.model, messages=full_messages, max_tokens=max_tokens)
        if tools:
            kwargs["tools"] = _to_openai_tools(tools)
        # V4 unifies deepseek-chat/deepseek-reasoner into one model with a thinking toggle,
        # sent via extra_body since it isn't part of the OpenAI-spec request shape. Legacy
        # model names (deepseek-chat/deepseek-reasoner, deprecated 2026-07-24) don't accept
        # this field, so only send it for the new v4 models. Sarvam has no equivalent toggle
        # (it always reasons), so this only ever applies to the deepseek provider.
        if self.provider == "deepseek" and self.model.startswith("deepseek-v4"):
            kwargs["extra_body"] = {"thinking": {"type": "enabled" if self.thinking else "disabled"}}
        return self.client.chat.completions.create(**kwargs)
