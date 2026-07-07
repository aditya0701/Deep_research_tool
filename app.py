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

from deep_research.agent import ResearchAgent
from deep_research.article_mode import research_article

ASK_PROFILE = "Ask a question"
ARTICLE_PROFILE = "Research an article"
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
            name=ARTICLE_PROFILE,
            markdown_description=(
                "Paste an article and the agent finds what's genuinely missing from it and "
                "researches those gaps in one session."
            ),
        ),
    ]


@cl.on_chat_start
async def on_chat_start():
    profile = cl.user_session.get("chat_profile")
    if profile == ARTICLE_PROFILE:
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


@cl.on_message
async def on_message(message: cl.Message):
    profile = cl.user_session.get("chat_profile")
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_step(step: dict):
        loop.call_soon_threadsafe(queue.put_nowait, step)

    def run_blocking():
        try:
            if profile == ARTICLE_PROFILE:
                title, _, body = message.content.partition("\n\n")
                return research_article(title.strip(), body.strip(), on_step=on_step)
            agent = ResearchAgent()
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
    await cl.Message(content=result["report"]).send()
    if result["flagged_claims"]:
        flagged = "\n".join(f"- {c}" for c in result["flagged_claims"])
        await cl.Message(
            content=f"**Flagged as unverified against retrieved sources:**\n{flagged}",
            author="Grounding check",
        ).send()
