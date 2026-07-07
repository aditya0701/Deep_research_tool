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


def research_article(title: str, body: str, max_iterations: int = 12, on_step=None) -> dict:
    """One continuous research session over the whole article, not a dossier of
    independently-researched questions."""
    agent = ResearchAgent(
        llm_client=LLMClient(),
        max_iterations=max_iterations,
        system_prompt=ENRICHMENT_SYSTEM_PROMPT,
    )
    task = f"Title: {title}\n\nArticle text:\n{body}"
    result = agent.run(task, on_step=on_step)
    return {
        "title": title,
        "report": result["report"],
        "flagged_claims": result["flagged_claims"],
        "iterations_used": result["iterations_used"],
        "trace": result["trace"],
    }
