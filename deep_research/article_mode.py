"""Mode 2: article-driven research.

Feeds the article directly into the same orchestration loop used for mode 1 -
gap identification happens as part of the model's own first-turn thinking, not
as a separate, deliberately-dumbed-down extraction call. That two-stage design
(a prior version of this file) existed only because the old single-shot search
this project replaces couldn't handle a rich top-level question; now that the
agent can decompose and research an interpretive question itself, pre-filtering
gap questions down to atomic fact-lookups before the agent even sees them is a
leftover constraint, not a real one. One continuous session also means every
angle the model decides to chase shares full article context and the same
retrieved-evidence pool, instead of each question researching in isolation with
no idea what the article actually said.
"""
from .agent import CORE_RULES, ResearchAgent
from .llm_client import LLMClient

ENRICHMENT_SYSTEM_PROMPT = f"""You are the enrichment researcher for TechDrishti, a tech
journalism outlet. You will be given an article's title and full text. Your job is to find
and research whatever would give a reader a genuinely more complete understanding than the
article provides on its own - real gaps, important context, comparisons the article gestures
at but doesn't answer, competitive positioning, pricing, technical depth, regulatory angles,
implications - whatever is actually missing and actually matters to this specific story.

Decide for yourself what's worth researching. You are not restricted to simple fact lookups:
an interpretive angle (e.g. "what's the strategic significance of this release given a
competitor's regulatory ban") is worth pursuing if it matters to the story - decompose it into
concrete sub-facts, research those, and synthesize the judgment yourself, exactly as you would
for a direct research question. Do not water a good angle down into a shallow one just because
it isn't a single fact lookup. Do not invent angles that aren't evidenced by the article or
your own research, either - an angle is only worth chasing if the article itself raises it or
your research surfaces it as genuinely relevant.

{CORE_RULES}
When you are done researching, respond with a single consolidated enrichment report: the
angles you investigated and what you found for each, organized with clear sections and inline
source URLs. This report is meant to help write a more comprehensive version of the article.
"""


HINDI_WRITER_SYSTEM_PROMPT = """You are a Hindi writer for a tech journalism outlet. You will be
given the original article's title and body, plus an enrichment research report containing
additional facts, context, and angles a researcher found that the original article was missing.

Your job: write ONE comprehensive Hindi-language article that weaves the original article's own
content together with the new research findings into a single coherent piece - not two sections
stapled together, not a "here's what's new" appendix. A reader should not be able to tell it was
built from two sources.

Language rules (follow exactly):
- Every sentence must be in Hindi - verb, conjunction, adjective all Hindi. Do not mix in English
  verbs or conjunctions.
  CORRECT: "स्वायत्त एजेंट (Autonomous Agent) एक सरल निर्णय-चक्र पर काम करते हैं।"
  WRONG: "Individual agents बहुत simple हैं और एक loop follow करते हैं।"
- Technical terms: Devanagari first, original English in parentheses on first use - मेमोरी स्टोर
  (Memory Store). Company/product/person names stay in Latin script (OpenAI, Claude, GPT-5.5).
- Hedge predictions and unconfirmed claims: हो सकता है, संभावना है - never state speculation as fact.
- Do not invent facts - use only what's in the original article or the enrichment report. If a
  sentence in the enrichment report is marked "[UNVERIFIED: ...]", either omit that claim or state
  it explicitly as unconfirmed - never present it as settled fact.

Write a full, substantial article - multiple paragraphs, not a summary. Cover the original story's
core facts plus the genuinely new context/angles from the enrichment report.

Output ONLY the final Hindi article as plain text: first line is the headline, then a blank line,
then the article body. No JSON, no markdown formatting, no preamble or meta-commentary.
"""


def write_hindi_article(title: str, body: str, enrichment_report: str, llm_client: LLMClient | None = None) -> str:
    llm = llm_client or LLMClient()
    task = (
        f"Original article title: {title}\n\nOriginal article body:\n{body}\n\n"
        f"--- Enrichment research report ---\n{enrichment_report}"
    )
    response = llm.call(system=HINDI_WRITER_SYSTEM_PROMPT, messages=[{"role": "user", "content": task}], tools=[])
    return response.choices[0].message.content or ""


def research_article(
    title: str, body: str, max_iterations: int = 12, on_step=None, write_hindi: bool = True
) -> dict:
    """One continuous research session over the whole article, not a dossier of
    independently-researched questions. Followed by a separate, non-agentic writing pass
    (no tools, no search budget) that turns the English enrichment report into a final Hindi
    article - kept as its own LLM call rather than folded into the research loop, since writing
    prose is a different job from deciding what to research next."""
    agent = ResearchAgent(
        llm_client=LLMClient(),
        max_iterations=max_iterations,
        system_prompt=ENRICHMENT_SYSTEM_PROMPT,
    )
    task = f"Title: {title}\n\nArticle text:\n{body}"
    result = agent.run(task, on_step=on_step)
    hindi_article = write_hindi_article(title, body, result["report"]) if write_hindi else None
    return {
        "title": title,
        "report": result["report"],
        "hindi_article": hindi_article,
        "flagged_claims": result["flagged_claims"],
        "iterations_used": result["iterations_used"],
        "trace": result["trace"],
    }
