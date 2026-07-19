"""Chainlit web UI for the deep research agent.

Runs the exact same ResearchAgent used by the CLI and eval suite - this file
only adds a browser interface on top. The on_step callback added to
agent.run()/research_article() is what makes this genuinely live: each
thinking block and tool call is pushed to the UI the moment it happens,
rather than only showing the final report after the whole run completes.

Run with: chainlit run app.py -w
"""
import asyncio

import chainlit as cl
from chainlit.input_widget import Select
from chainlit.server import app as fastapi_app

from deep_research.agent import ResearchAgent
from deep_research.api import router as concise_api_router
from deep_research.article_mode import research_article
from deep_research.concise_mode import answer_concisely
from deep_research.llm_client import DEFAULT_PROVIDER, LLMClient

# Backends offered in the settings dropdown. Kept as one list so the values and the
# initial_index lookup can't drift apart. groq is the free-tier option for comparison runs.
_PROVIDER_CHOICES = ["deepseek", "sarvam", "groq"]

# Adds POST /api/concise onto Chainlit's own FastAPI app - Hugging Face Spaces exposes only
# one port, and Chainlit already owns it, so a plain HTTP caller (GitHub Actions, another
# model) hits this route directly instead of needing a second port Spaces has no way to
# expose. Must happen at import time of this module, before Chainlit finishes mounting its
# catch-all SPA route.
fastapi_app.include_router(concise_api_router)

ASK_PROFILE = "Ask a question"
CONCISE_PROFILE = "Quick grounded answer"
ARTICLE_PROFILE = "Research an article"
WRITE_ARTICLE_PROFILE = "Write an article"
_ARTICLE_PROFILES = (ARTICLE_PROFILE, WRITE_ARTICLE_PROFILE)
_DONE = object()


@cl.set_chat_profiles
async def chat_profiles():
    return [
        cl.ChatProfile(
            name=ASK_PROFILE,
            markdown_description=(
                "Ask any research question directly. The agent decides how many searches "
                "it needs and when to stop."
            ),
            default=True,
        ),
        cl.ChatProfile(
            name=CONCISE_PROFILE,
            markdown_description=(
                "Short, fact-grounded answers instead of a full report. Definitions get a "
                "quick verified answer, ambiguous terms get every plausible meaning plus which "
                "one fits your context, and genuinely complex questions still get researched in "
                "full - you just get a grounded conclusion back, not the whole report."
            ),
        ),
        cl.ChatProfile(
            name=ARTICLE_PROFILE,
            markdown_description=(
                "Paste an article and the agent finds what's genuinely missing from it and "
                "researches those gaps in one session, then hands you the raw research "
                "findings - no article is written."
            ),
        ),
        cl.ChatProfile(
            name=WRITE_ARTICLE_PROFILE,
            markdown_description=(
                "Paste an article and the agent researches what's missing, then weaves the "
                "original article and the new findings into one complete Hindi-language article."
            ),
        ),
    ]


@cl.on_chat_start
async def on_chat_start():
    profile = cl.user_session.get("chat_profile")
    cl.user_session.set("llm_provider", DEFAULT_PROVIDER)
    await cl.ChatSettings(
        [
            Select(
                id="llm_provider",
                label="LLM backend",
                values=_PROVIDER_CHOICES,
                initial_index=_PROVIDER_CHOICES.index(DEFAULT_PROVIDER)
                if DEFAULT_PROVIDER in _PROVIDER_CHOICES
                else 0,
            )
        ]
    ).send()
    if profile in _ARTICLE_PROFILES:
        await cl.Message(
            content=(
                "Paste your article as: **first line = title**, then a blank line, then the "
                "article text. I'll find what's missing from it and research it."
            )
        ).send()
    else:
        await cl.Message(
            content="Ask a research question. I'll decompose it, search, and report back."
        ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    provider = settings["llm_provider"]
    cl.user_session.set("llm_provider", provider)
    await cl.Message(content=f"Switched LLM backend to **{provider}**.").send()


@cl.on_message
async def on_message(message: cl.Message):
    profile = cl.user_session.get("chat_profile")
    provider = cl.user_session.get("llm_provider") or DEFAULT_PROVIDER
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_step(step: dict):
        loop.call_soon_threadsafe(queue.put_nowait, step)

    used_backend = {}

    def run_blocking():
        try:
            llm_client = LLMClient(provider=provider)
            used_backend["provider"] = llm_client.provider
            used_backend["model"] = llm_client.model
            # Sarvam has taken more iterations than DeepSeek to reach an answer in
            # side-by-side testing, so give it a fixed 12-turn budget regardless of mode
            # (ask mode otherwise defaults to 8) rather than letting it run unbounded.
            max_iterations_kwargs = {"max_iterations": 12} if provider == "sarvam" else {}
            if profile in _ARTICLE_PROFILES:
                title, _, body = message.content.partition("\n\n")
                return research_article(
                    title.strip(),
                    body.strip(),
                    on_step=on_step,
                    write_hindi=(profile == WRITE_ARTICLE_PROFILE),
                    llm_client=llm_client,
                    **max_iterations_kwargs,
                )
            if profile == CONCISE_PROFILE:
                return answer_concisely(message.content, on_step=on_step, llm_client=llm_client, **max_iterations_kwargs)
            agent = ResearchAgent(llm_client=llm_client, **max_iterations_kwargs)
            return agent.run(message.content, on_step=on_step)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _DONE)

    run_task = asyncio.ensure_future(asyncio.to_thread(run_blocking))

    while True:
        step = await queue.get()
        if step is _DONE:
            break
        if "thinking" in step:
            async with cl.Step(name="Thinking", type="llm", default_open=True) as s:
                s.output = step["thinking"]
        elif "tool" in step:
            async with cl.Step(name=step["tool"], type="tool") as s:
                s.input = step["input"]
                s.output = step.get("result", "")

    result = await run_task
    if profile == WRITE_ARTICLE_PROFILE and result.get("hindi_article"):
        await cl.Message(content=result["hindi_article"], author="Final Hindi article").send()
        async with cl.Step(name="Enrichment research report (English)", type="tool") as s:
            s.output = result["report"]
    elif profile == ARTICLE_PROFILE:
        await cl.Message(content=result["report"], author="Enrichment research report").send()
    elif profile == CONCISE_PROFILE:
        await cl.Message(content=result["answer"], author=f"Answer ({result['category']})").send()
        if result.get("research_report"):
            async with cl.Step(name="Underlying research report", type="tool") as s:
                s.output = result["research_report"]
    else:
        await cl.Message(content=result["report"]).send()
    if result["flagged_claims"]:
        flagged = "\n".join(f"- {c}" for c in result["flagged_claims"])
        await cl.Message(
            content=f"**Flagged as unverified against retrieved sources:**\n{flagged}",
            author="Grounding check",
        ).send()
    await cl.Message(
        content=f"_Backend used: **{used_backend['provider']}** ({used_backend['model']})_",
        author="Backend info",
    ).send()
