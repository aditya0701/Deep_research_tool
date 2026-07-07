"""Core research agent: an orchestration loop around a thinking-capable LLM with
search tools.

The model owns the reasoning - deciding what to search, whether a result is
enough, when to stop. This module owns everything the model cannot do itself:
actually executing tool calls (a model can request a search, it cannot make
the HTTP request), enforcing a hard iteration budget regardless of what the
model wants (a stopping condition must be code-enforced, not just prompted -
see TechDrishti's runaway-generation history), and running the grounding
check on the final report before returning it.
"""
import json
import re

from .grounding import check_grounding
from .llm_client import LLMClient
from .tools import calculate, fetch_page, get_current_date, news_search, web_search

MAX_ITERATIONS = 8

# Shared across every task framing (direct Q&A, article enrichment, ...) - these are
# constraints on how the agent researches, independent of what it's researching.
# Note rule 1 governs the *top-level* question/task, not the low-level search queries:
# the agent should never shy away from an interpretive top-level question, but the actual
# strings it sends to web_search/news_search must still be atomic fact lookups, since that
# part is a real constraint of how search engines work, not a limitation of its reasoning.
CORE_RULES = """Rules learned from a prior project's failures - follow them exactly:
1. Search engines return facts, not judgments. If your task is interpretive ("what is the
   strategic significance of X"), decompose it into concrete fact sub-questions first
   (pricing, specs, dates, quotes, deal terms), retrieve those, and do the synthesis/judgment
   yourself in your final answer - never search for the judgment directly. Do not water an
   interpretive angle down into a shallow one just because it isn't a single fact lookup -
   decompose it instead of dropping it.
2. Before comparing X to Y, confirm they are genuinely the same kind of thing - sharing a
   broad domain or category is not enough. Two things can occupy the same field while playing
   completely different roles in it (e.g. a bathroom tap and a sink are both plumbing fixtures
   in the same field, but a tap is a valve mechanism and a sink is a basin - comparing them
   head-to-head would be a category error even though "they're both bathroom fixtures" sounds
   like a match). This applies in any domain you research, not just obvious cases: a raw
   component, a tool built on top of that component, and an end-user product built using that
   tool can all sit in the same field while being three different kinds of thing to compare.
   Identify what specific kind of thing each side actually is before treating a comparison as
   valid - not just whether they share a category label. If you are not already certain what
   kind of thing an entity is, verify it with a search instead of guessing from the name or
   from your own training knowledge, which can be outdated or simply wrong for anything recent
   - a quick "what is X" search is cheap; a wrong assumption about what X even is poisons the
   whole comparison. Only once that's confirmed, fetch facts about each side independently via
   separate searches, then compare. Never invent a comparison target that wasn't named in the 
   task or in material you've actually retrieved - if no real, same-kind comparator is evident
   , search broadly for real matches or say the
   comparison target is unclear.
3. Every factual claim in your final report must be traceable to something you actually
   retrieved via a tool call in this conversation. Do not state a fact you did not retrieve.
   Cite by writing the actual source URL inline, next to the claim it supports (e.g. "...
   according to https://example.com/article"). Never cite with a bracketed reference number
   like [1] or [2] - whatever reads your final answer next (a person or another model/API
   response) often receives only this text, with no separate numbered source list attached to
   resolve those markers against. A bracket citation with nothing to point to is exactly as
   unverifiable as no citation at all.
4. You have a limited number of tool calls. Prioritize the highest-value searches first, and
   stop searching once you have enough to answer confidently rather than exhausting every
   possible angle.
5. If fetch_page returns an error for a URL, do not treat that source as unavailable yet -
   web_search and news_search usually return several results at once, so try a different URL
   from those same results first (this costs nothing extra). If none of them work, or a search
   itself comes back empty or unhelpful, rephrase the query and search again, within your
   budget, before concluding that a piece of information genuinely isn't available.
6. Always use the calculate tool for any arithmetic beyond trivial single-step math -
   percentages, CAGR, growth rates, differences between multiple figures. Do not compute these
   by hand in your reasoning, even if you are confident in the result - use the tool every
   time, not just when unsure.
"""

SYSTEM_PROMPT = f"""You are a deep research agent. Given a research question, investigate it
thoroughly using the tools available, then produce a well-organized, cited report.

{CORE_RULES}
When you are done researching, respond with your final report as plain text (no more tool
calls) organized with clear sections and inline source URLs.
"""

TOOLS = [
    {
        "name": "web_search",
        "description": (
            "General-purpose web search for a specific factual query. Use atomic, "
            "fact-seeking queries (e.g. 'GLM-5.2 pricing'), not compound or interpretive "
            "questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "news_search",
        "description": (
            "Search recent news coverage for a specific query. Best for recency-sensitive "
            "facts (announcements, deals, launches)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "fetch_page",
        "description": (
            "Fetch and read the full body text of a specific URL, e.g. one returned by "
            "web_search or news_search, when the snippet isn't enough detail. Works for "
            "both regular web pages and PDFs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "get_current_date",
        "description": (
            "Returns today's date. Use this whenever a task depends on recency (how long ago "
            "something happened, whether it's still current) rather than assuming today's date "
            "from your own training knowledge, which has a cutoff."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "calculate",
        "description": (
            "Safely evaluates a purely arithmetic expression (+ - * / ** % and parentheses "
            "only, e.g. '(490-310)/310*100'). Use this for any percentage change, CAGR, or "
            "growth-rate math instead of doing it by hand - multi-step arithmetic done in "
            "reasoning is error-prone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
]

_TOOL_FUNCS = {
    "web_search": web_search,
    "news_search": news_search,
    "fetch_page": fetch_page,
    "get_current_date": get_current_date,
    "calculate": calculate,
}

# Only genuine retrieval tools count as "evidence" for the grounding check - calculate
# operates on data the model already has (or invented), so treating its output as
# retrieved evidence would create a loophole: a fabricated number run through calculate
# would make itself look grounded just by being present in the conversation.
_EVIDENCE_TOOLS = {"web_search", "news_search", "fetch_page"}

# When the search budget runs out, retrieval specifically should stop - but calculate
# doesn't search, doesn't hit the network, and can't cause a runaway loop, so cutting it
# off too just produces an incomplete report instead of actually stopping research.
# Confirmed live: without this, the model never got a chance to use calculate at all,
# because the forced-final call removed every tool indiscriminately.
_UTILITY_TOOLS = [t for t in TOOLS if t["name"] not in _EVIDENCE_TOOLS]

# Observed live with Sarvam's sarvam-30b: instead of routing a tool call through the
# API's structured tool_calls field, it occasionally leaks one as raw text in
# message.content, using a Hermes/Qwen-style <tool_call> block from its own fine-tuning
# data rather than the schema this agent actually gave it (e.g. tool name "search" with
# a "numResults" arg, neither of which are ours). DeepSeek's server-side parsing doesn't
# do this. Without recovering it, message.tool_calls is empty so the loop treats that
# raw text as the model's finished answer and it leaks verbatim into the final report
# instead of the search ever actually happening.
_FALLBACK_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_FALLBACK_ARG_PAIR_RE = re.compile(r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>", re.DOTALL)
_FALLBACK_TOOL_ALIASES = {
    "search": "web_search",
    "web_search": "web_search",
    "websearch": "web_search",
    "news_search": "news_search",
    "newssearch": "news_search",
    "fetch_page": "fetch_page",
    "fetch": "fetch_page",
    "get_current_date": "get_current_date",
    "calculate": "calculate",
    "calc": "calculate",
}
_FALLBACK_ARG_KEYS = {
    "web_search": ("query", "max_results", "numResults", "num_results"),
    "news_search": ("query", "max_results", "numResults", "num_results"),
    "fetch_page": ("url",),
    "calculate": ("expression",),
    "get_current_date": (),
}


def _parse_fallback_tool_calls(content: str) -> list[dict] | None:
    """Best-effort recovery of tool calls a provider leaked as raw text (see note above)
    instead of returning them in the API's structured tool_calls field. Only keeps
    arguments our own tool functions actually accept - extra fields the model invented
    (like "numResults") are dropped rather than passed through and blowing up as a
    TypeError."""
    matches = _FALLBACK_TOOL_CALL_RE.findall(content)
    if not matches:
        return None
    calls = []
    for raw in matches:
        raw = raw.strip()
        name, args = None, {}
        try:
            parsed = json.loads(raw)
            name = parsed.get("name")
            args = dict(parsed.get("arguments") or {})
        except (json.JSONDecodeError, AttributeError, TypeError):
            first_line, _, rest = raw.partition("\n")
            name = first_line.strip()
            for key, value in _FALLBACK_ARG_PAIR_RE.findall(rest):
                args[key.strip()] = value.strip()
        real_name = _FALLBACK_TOOL_ALIASES.get((name or "").strip().lower())
        if not real_name:
            continue
        allowed = _FALLBACK_ARG_KEYS[real_name]
        clean_args = {k: v for k, v in args.items() if k in allowed}
        if "query" in allowed and "query" not in clean_args:
            continue
        if "url" in allowed and "url" not in clean_args:
            continue
        if "expression" in allowed and "expression" not in clean_args:
            continue
        for numeric_key in ("max_results", "numResults", "num_results"):
            if numeric_key in clean_args:
                try:
                    clean_args["max_results"] = int(clean_args.pop(numeric_key))
                except (TypeError, ValueError):
                    clean_args.pop(numeric_key, None)
        calls.append({"name": real_name, "arguments": clean_args})
    return calls or None


# Belt-and-suspenders: matches a closed <tool_call>...</tool_call> block (in case one
# slips through unrecovered, e.g. an unmappable tool name) as well as a dangling,
# never-closed <tool_call> (a response truncated mid-tag). Applied right before any text
# is accepted as a final report, in `_finish`, so a leaked tag can never reach the user
# regardless of which code path produced the text.
_FALLBACK_DANGLING_TAG_RE = re.compile(r"<tool_call>.*$", re.DOTALL)


def _strip_leaked_tool_call_artifacts(text: str) -> str:
    if not text:
        return text
    text = _FALLBACK_TOOL_CALL_RE.sub("", text)
    text = _FALLBACK_DANGLING_TAG_RE.sub("", text)
    return text.strip()


def extract_sources(trace: list[dict]) -> list[str]:
    """Pulls every URL actually retrieved via web_search/news_search/fetch_page out of the
    trace, independent of whatever citation style the model's own final text used. Exists
    because CORE_RULES telling the model "cite with real URLs, not bracket numbers" is a
    prompt instruction, not a code-enforced one - a caller that needs a resolvable source
    list (an API response consumed by another program, not read as prose by a person) can't
    rely on the model always complying, so this reconstructs the list directly from what was
    genuinely fetched rather than trusting the model's own citations."""
    urls: list[str] = []
    seen = set()

    def _add(url):
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    for step in trace:
        tool = step.get("tool")
        if tool in ("web_search", "news_search"):
            try:
                items = json.loads(step.get("result", ""))
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    _add(item.get("url"))
        elif tool == "fetch_page":
            # fetch_page returns plain page text on success, or a JSON dict with an "error"
            # key on failure (see tools.py) - only a successful fetch is genuine evidence, so
            # a failed one must not be listed as a source just because the URL was attempted.
            result = step.get("result", "")
            try:
                parsed = json.loads(result)
                failed = isinstance(parsed, dict) and "error" in parsed
            except (json.JSONDecodeError, TypeError):
                failed = False
            if failed:
                continue
            try:
                args = json.loads(step.get("input", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}
            _add(args.get("url"))
    return urls


class ResearchAgent:
    def __init__(
        self,
        llm_client: LLMClient | None = None,
        max_iterations: int = MAX_ITERATIONS,
        system_prompt: str | None = None,
    ):
        self.llm = llm_client or LLMClient()
        self.max_iterations = max_iterations
        self.system_prompt = system_prompt or SYSTEM_PROMPT

    def _execute_tool_call(self, tool_call, retrieved_texts: list[str]) -> str:
        func = _TOOL_FUNCS.get(tool_call.function.name)
        try:
            args = json.loads(tool_call.function.arguments)
            result = func(**args) if func else f"Unknown tool: {tool_call.function.name}"
        except Exception as e:
            result = f"Tool error: {e}"
        result_text = result if isinstance(result, str) else json.dumps(result)
        if tool_call.function.name in _EVIDENCE_TOOLS:
            retrieved_texts.append(result_text)
        return result_text

    def run(self, question: str, on_step=None) -> dict:
        """`on_step`, if given, is called synchronously with each trace entry the moment it
        happens (not just once at the end) - lets a caller (e.g. a UI running this in a
        background thread) show the agent's reasoning and searches live instead of only
        after the whole run finishes."""
        messages = [{"role": "user", "content": question}]
        retrieved_texts = []
        trace = []

        def emit(step):
            trace.append(step)
            if on_step:
                on_step(step)

        for i in range(self.max_iterations):
            response = self.llm.call(system=self.system_prompt, messages=messages, tools=TOOLS)
            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason

            # "length" means the response was cut off by max_tokens mid-generation, not that
            # the model chose to stop - previously invisible, so a truncated reasoning pass or
            # a truncated final answer looked identical to a clean one. Surfaced in the trace
            # so a cut-off report is diagnosable instead of silently accepted as complete.
            if finish_reason == "length":
                emit({"iteration": i, "warning": "response cut off at max_tokens (finish_reason=length)"})

            # DeepSeek-specific field, not part of the OpenAI spec - read defensively rather
            # than assumed present, especially alongside a tool call in the same turn.
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning:
                emit({"iteration": i, "thinking": reasoning})

            messages.append(message.model_dump(exclude_none=True))

            if not message.tool_calls:
                fallback_calls = _parse_fallback_tool_calls(message.content or "")
                if not fallback_calls and finish_reason == "length":
                    # The response was cut off by max_tokens before finishing - possibly
                    # mid-way through a leaked <tool_call> block (no closing tag left to
                    # match on), possibly mid-sentence in what was meant to be the real
                    # final report. Either way this text is not a genuine, complete answer
                    # and must not be accepted as one (that's exactly how a truncated
                    # "search" fragment or a cut-off enrichment report used to leak through
                    # as the final displayed result). Ask for a fresh, shorter attempt
                    # instead, spending one more iteration rather than trusting a fragment.
                    emit(
                        {
                            "iteration": i,
                            "warning": "response was truncated by max_tokens with no usable tool "
                            "call - discarding the fragment and asking the model to retry more "
                            "concisely instead of treating it as a final answer",
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": "Your previous response was cut off before it finished (hit "
                            "the token limit). Continue from where you left off, but be more "
                            "concise - either make one tool call, or if you were writing your "
                            "final report, write a more compact version that fits.",
                        }
                    )
                    continue
                if fallback_calls:
                    emit(
                        {
                            "iteration": i,
                            "warning": "provider returned a tool call as raw text instead of a "
                            "structured tool call - recovered and executed it anyway",
                        }
                    )
                    assistant_msg = messages[-1]
                    leftover = _FALLBACK_TOOL_CALL_RE.sub("", assistant_msg.get("content") or "").strip()
                    if leftover:
                        # The model folded its reasoning into `content` instead of the
                        # dedicated reasoning_content field this turn (observed: only the
                        # first turn reliably uses that field) - surface it as a thinking
                        # step instead of silently dropping it when the tag is stripped out,
                        # which previously made every turn after the first look thinking-less.
                        emit({"iteration": i, "thinking": leftover})
                    assistant_msg["content"] = leftover or None
                    synthetic_tool_calls = []
                    for j, call in enumerate(fallback_calls):
                        fake_id = f"fallback_{i}_{j}"
                        synthetic_tool_calls.append(
                            {
                                "id": fake_id,
                                "type": "function",
                                "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
                            }
                        )
                    assistant_msg["tool_calls"] = synthetic_tool_calls
                    for tc, call in zip(synthetic_tool_calls, fallback_calls):
                        func = _TOOL_FUNCS.get(call["name"])
                        try:
                            result = func(**call["arguments"])
                        except Exception as e:
                            result = f"Tool error: {e}"
                        result_text = result if isinstance(result, str) else json.dumps(result)
                        if call["name"] in _EVIDENCE_TOOLS:
                            retrieved_texts.append(result_text)
                        emit(
                            {
                                "iteration": i,
                                "tool": call["name"],
                                "input": json.dumps(call["arguments"]),
                                "result": result_text,
                            }
                        )
                        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_text})
                    continue
                return self._finish(question, message.content or "", retrieved_texts, trace, i + 1, forced=False)

            for tool_call in message.tool_calls:
                result_text = self._execute_tool_call(tool_call, retrieved_texts)
                emit(
                    {
                        "iteration": i,
                        "tool": tool_call.function.name,
                        "input": tool_call.function.arguments,
                        "result": result_text,
                    }
                )
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_text})

        # Hard budget exhausted - code-enforced stop on searching specifically, not left to
        # the model's own judgment. Computation/formatting is still allowed for one more turn
        # (see _UTILITY_TOOLS above) so it can finish writing up what it already found.
        messages.append(
            {
                "role": "user",
                "content": "You've reached the search budget - no more web_search, news_search, "
                "or fetch_page calls. You may still use calculate if needed, then provide your "
                "final report.",
            }
        )
        response = self.llm.call(system=self.system_prompt, messages=messages, tools=_UTILITY_TOOLS)
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        # Mirrors the main loop's structured-tool_calls handling below, plus the same
        # text-leaked-as-tool_call recovery used there - this forced-budget branch used to
        # skip that recovery entirely (it only ever checked message.tool_calls), which is
        # exactly how a leaked <tool_call> reached the displayed enrichment report: article
        # mode runs a longer budget than ask mode and hits this branch far more often.
        fallback_calls = None if message.tool_calls else _parse_fallback_tool_calls(message.content or "")
        if message.tool_calls or fallback_calls:
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    result_text = self._execute_tool_call(tool_call, retrieved_texts)
                    emit(
                        {
                            "iteration": self.max_iterations,
                            "tool": tool_call.function.name,
                            "input": tool_call.function.arguments,
                            "result": result_text,
                        }
                    )
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_text})
            else:
                assistant_msg = messages[-1]
                leftover = _FALLBACK_TOOL_CALL_RE.sub("", assistant_msg.get("content") or "").strip()
                if leftover:
                    emit({"iteration": self.max_iterations, "thinking": leftover})
                synthetic_tool_calls = []
                for j, call in enumerate(fallback_calls):
                    fake_id = f"fallback_final_{j}"
                    synthetic_tool_calls.append(
                        {
                            "id": fake_id,
                            "type": "function",
                            "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
                        }
                    )
                    if call["name"] in _EVIDENCE_TOOLS:
                        # Search budget is already exhausted at this point - don't actually
                        # search, just tell the model that so it writes up what it has.
                        result_text = (
                            "Search budget exhausted - no further searching allowed. Write your "
                            "final report using only what you've already retrieved."
                        )
                    else:
                        func = _TOOL_FUNCS.get(call["name"])
                        try:
                            raw_result = func(**call["arguments"])
                        except Exception as e:
                            raw_result = f"Tool error: {e}"
                        result_text = raw_result if isinstance(raw_result, str) else json.dumps(raw_result)
                    emit(
                        {
                            "iteration": self.max_iterations,
                            "tool": call["name"],
                            "input": json.dumps(call["arguments"]),
                            "result": result_text,
                        }
                    )
                    messages.append({"role": "tool", "tool_call_id": fake_id, "content": result_text})
                assistant_msg["content"] = leftover or None
                assistant_msg["tool_calls"] = synthetic_tool_calls
            messages.append(
                {"role": "user", "content": "Provide your final report now, as plain text, no more tool calls."}
            )
            response = self.llm.call(system=self.system_prompt, messages=messages, tools=[])
            message = response.choices[0].message

        if response.choices[0].finish_reason == "length":
            # Unlike the main loop, this final step is past the hard search-budget cap - but
            # that cap is about searching, not about finishing the writeup, so one bounded
            # retry here doesn't reopen the search budget, it just gives the model one more
            # shot at a report that fits instead of silently accepting a report that stops
            # mid-sentence (previously the only outcome here on truncation).
            trace.append({"warning": "final report cut off at max_tokens (finish_reason=length) - retrying once"})
            messages.append(message.model_dump(exclude_none=True))
            messages.append(
                {
                    "role": "user",
                    "content": "That was cut off before finishing. Write a more compact final "
                    "report that fits within the token limit - trim detail rather than leaving "
                    "it unfinished.",
                }
            )
            response = self.llm.call(system=self.system_prompt, messages=messages, tools=[])
            message = response.choices[0].message
            if response.choices[0].finish_reason == "length":
                trace.append({"warning": "retry also cut off at max_tokens - using it anyway"})
        final_text = message.content or ""
        return self._finish(question, final_text, retrieved_texts, trace, self.max_iterations, forced=True)

    @staticmethod
    def _finish(question, final_text, retrieved_texts, trace, iterations_used, forced):
        final_text = _strip_leaked_tool_call_artifacts(final_text)
        grounded_report, dropped = check_grounding(final_text, retrieved_texts)
        trace.append({"action": "forced_final_answer" if forced else "final_answer"})
        return {
            "question": question,
            "report": grounded_report,
            "flagged_claims": dropped,
            "iterations_used": iterations_used,
            "trace": trace,
            "sources": extract_sources(trace),
        }
