"""Mode 3: concise, fact-grounded answers - for a caller (a person, or another model
orchestrating this one as a tool) that wants a short, cited answer, not a full report.

Reuses the same ResearchAgent/CORE_RULES as ask mode, but with a different final-answer
contract. The agent classifies its own query - SIMPLE (factual lookup/definition),
AMBIGUOUS (a term with multiple unrelated meanings, given some disambiguating context), or
COMPLEX (interpretive/multi-part, needs real decomposition) - and that classification decides
whether a second pass runs at all. Making the model announce the category in a fixed,
code-checked tag (rather than just trusting it to "keep it short for simple questions") is
the same lesson CORE_RULES and article_mode.py both apply elsewhere in this project: a
branching decision that matters has to be code-enforced, not left to the model to remember
every time.

SIMPLE and AMBIGUOUS answers are already short - the agent writes them directly and they're
returned as-is. Only COMPLEX queries get the second stage: the agent's own final message
doubles as the plain-prose research report (no headers/bullets/bold, exactly like article
mode's enrichment report) - the report is then handed to a second LLM call (no tools, no
search budget) whose only job is to turn "here's what I found" into "here's the answer, and
here's the fact backing each part of it." That second pass is its own LLM call rather than
folded into the research loop for the same reason article_mode.py's Hindi writer is: turning
findings into a grounded conclusion is a writing/reasoning-over-fixed-text job, not a
research-decision job, and mixing the two let prose quality and research judgment degrade
each other in earlier versions of this project.
"""
from .agent import CORE_RULES, ResearchAgent
from .grounding import check_grounding
from .llm_client import LLMClient

SIMPLE_TAG = "SIMPLE_ANSWER"
AMBIGUOUS_TAG = "AMBIGUOUS_ANSWER"
COMPLEX_TAG = "COMPLEX_REPORT"
_CATEGORY_NAMES = {SIMPLE_TAG: "simple", AMBIGUOUS_TAG: "ambiguous", COMPLEX_TAG: "complex"}

CONCISE_SYSTEM_PROMPT = f"""You are a fact-finding research agent that gives short, precise,
fact-grounded answers instead of long reports. You may be called directly by a person, or as
a tool by another AI model that needs a grounded answer to hand back to its own user - either
way, your final answer is consumed programmatically, so follow the output contract below
exactly.

First decide which of these three categories the query belongs to - this decision controls
how much you research and how you must format your final answer:

1. SIMPLE - a plain factual lookup or definition ("what is X", "when was Y founded", "what
   does Z stand for", "how does W work"). Verify it with a quick search (never answer purely
   from your own training knowledge, which can be outdated or simply wrong for anything
   recent) and give a short, direct answer - a sentence or two, not a report. Do not
   over-research a question that only needs one or two lookups.

2. AMBIGUOUS - you are given a word or term, plus some surrounding context, and that word has
   more than one common, unrelated meaning (e.g. "lean" could be a programming language, a
   codeine-based drug, or a body type; "python" could be a programming language or a snake).
   For these: identify every plausible meaning the word could have, briefly define each one
   and name what category/domain it belongs to, then explicitly state which meaning applies
   given the context you were given, and why. Do not silently pick one meaning and answer as
   if there were no ambiguity - the disambiguation itself is the point of the answer. If a
   meaning is unfamiliar or you're not certain it exists, verify it with a search rather than
   guessing.

3. COMPLEX - an interpretive, multi-part, or judgment question that no single lookup can
   answer (e.g. "how does X's pricing strategy compare to Y's", "what's the strategic
   significance of X's regulatory ban for Y"). These need real research.

Only escalate to the COMPLEX path when the query genuinely requires it - most direct questions
are SIMPLE or AMBIGUOUS and should get a short answer, not a research report.

{CORE_RULES}

For COMPLEX queries only: research thoroughly using the rules above, then write your findings
as a plain-prose report - no headers, no bullet points, no bold text, just prose covering each
sub-fact you found with inline source URLs. This report is read by another AI step next, never
directly by a person, so skip all visual formatting.

Once you've decided the category, your FINAL answer (the one with no more tool calls) MUST
start on its own first line with exactly one of these three tags, so the category is
unambiguous to whatever reads your answer next - a person or another model:
{SIMPLE_TAG}
{AMBIGUOUS_TAG}
{COMPLEX_TAG}

followed by a blank line, then the content described above for that category. Do not add any
other text before the tag or explain the tag itself.
"""

CONCLUSION_SYSTEM_PROMPT = """You will be given the original question and a plain-prose
research report - findings a researcher gathered while investigating it, with inline source
URLs. You have no search tools; you may only use what is written in the report.

Your job: write a short, direct conclusion that actually answers the original question - the
answer itself, not a summary of the research process. State the conclusion plainly, and back
up every factual claim in it with the specific fact(s) from the report that support it, inline
- e.g. "X is cheaper than Y (the report puts X at $10/month against Y's $15/month, per
https://example.com/pricing)." A reader should be able to see, for every claim in your
conclusion, exactly what fact grounds it - and where that fact came from.

Preserve the report's source URLs verbatim when you cite them - copy the actual URL, don't
paraphrase or drop it. Never invent a bracketed reference number like [1] or [2] in place of a
URL, even if the report itself used one: your conclusion is often read with no separate
numbered source list attached, so a bracket marker with nothing to resolve it against is
functionally an uncited claim.

Do not introduce any claim that isn't traceable to the report. If the report marks something
"[UNVERIFIED: ...]" or otherwise flags it as unconfirmed, carry that uncertainty into your
conclusion explicitly rather than stating it as settled fact. If the report doesn't contain
enough to answer part of the original question, say so plainly instead of filling the gap
yourself.

Output ONLY the conclusion as plain text - no headers, no bullet points, no meta-commentary
about what you did.
"""


def _write_conclusion(question: str, report: str, llm_client: LLMClient) -> str:
    task = f"Original question: {question}\n\n--- Research report ---\n{report}"
    response = llm_client.call(system=CONCLUSION_SYSTEM_PROMPT, messages=[{"role": "user", "content": task}], tools=[])
    return response.choices[0].message.content or ""


def answer_concisely(
    question: str,
    max_iterations: int = 8,
    on_step=None,
    llm_client: LLMClient | None = None,
) -> dict:
    """Runs the classify-then-answer research pass, and for COMPLEX queries only, the
    second grounded-conclusion pass. Falls back to treating the whole answer as a plain
    "simple" answer if the model doesn't emit a recognized tag on the first line - a
    malformed tag is a reason to degrade gracefully, not to crash the caller."""
    llm_client = llm_client or LLMClient()
    agent = ResearchAgent(llm_client=llm_client, max_iterations=max_iterations, system_prompt=CONCISE_SYSTEM_PROMPT)
    result = agent.run(question, on_step=on_step)
    raw = result["report"]

    tag, _, rest = raw.partition("\n")
    tag = tag.strip()
    content = rest.lstrip("\n").strip()
    if tag not in _CATEGORY_NAMES:
        tag, content = SIMPLE_TAG, raw

    flagged_claims = list(result["flagged_claims"])
    out = {
        "question": question,
        "category": _CATEGORY_NAMES[tag],
        "iterations_used": result["iterations_used"],
        "trace": result["trace"],
        "sources": result["sources"],
    }

    if tag == COMPLEX_TAG:
        conclusion = _write_conclusion(question, content, llm_client=llm_client)
        grounded_conclusion, extra_flagged = check_grounding(conclusion, [content])
        out["research_report"] = content
        out["answer"] = grounded_conclusion
        flagged_claims.extend(extra_flagged)
    else:
        out["answer"] = content

    out["flagged_claims"] = flagged_claims
    return out
