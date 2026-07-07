# Deep Research Agent — Context Handoff from TechDrishti

This document exists to seed a new project: an autonomous deep-research agent, born directly
out of a real, measured limitation in TechDrishti (टेकदृष्टि), a Hindi tech-journalism pipeline.
Everything below is grounded in actual test runs against real news articles, not hypothetical
design — where a number appears, it was measured.

## Why this project exists

TechDrishti is a **fixed-sequence agentic workflow**, not an autonomous agent: for every article,
it always runs the same ~6 LLM calls in the same order (skip-gate → entity analysis → JSON
extraction → editorial strategy → writing → post-processing). The number of steps is decided by
code, not by the model. That's the right design for a pipeline that has to run unattended every
day and stay cheap — but it means TechDrishti structurally cannot do real research: it fires one
search per query and takes whatever comes back, with no ability to notice "this didn't actually
answer the question, let me search again differently" or "I found something better, let me follow
that thread."

The new project's whole point is to be the opposite: a system where **the number and sequence of
research steps is genuinely undecidable in advance** — it decides how many searches to run, what
to search next based on what it already found, when it's confident enough to stop, and how to
reconcile conflicting or thin information. That's a real agent loop, not a workflow.

## The concrete gap that motivated this (with real examples)

TechDrishti's Stage 1 (`writer/synthesize.py`) identifies "gaps" in a news article that an editor
would want filled — things like "how does this compare to competitors" or "what's the pricing."
It turns each gap into a single search query, fires it once at DDG/Google News, and hands
whatever comes back to the writer. Tested this against 4 real live articles
(GLM-5.2 model release, an India-Japan AI alliance announcement, the Persistent-Nagarro
acquisition, Mistral's Leanstral 1.5 model) run through the actual production code, not
synthetic test fixtures. Here's what a single-shot search architecture cannot do that a real
research agent should:

### Failing case 1 — compound interpretive questions have no single answer
Stage 1 generated queries like:

> "What is the strategic significance of Z.ai's open-source release in the context of the US ban
> on Anthropic, and what are the likely long-term implications for the global AI model market?"

No search result ever "answers" this — it's not a fact anyone published, it's an editorial
judgment. A real research agent needs to recognize when a question is fundamentally a synthesis
task (requiring the agent itself to read multiple sources and draw a conclusion) versus a
fact-lookup task (a single query away). This is probably the single most important distinction
to design around: **decompose "what does this mean" into "what are the facts," retrieve those,
then reason over them yourself** — don't ask the search engine to do your thinking.

### Failing case 2 — genuine comparisons need multi-hop retrieval
A real comparison ("how does GLM-5.2's architecture compare to Claude Opus 4.8 and GPT-5.5")
needs the facts about *each* side fetched independently, then compared by the reasoning model —
not one compound query. Confirmed live: reframing a compound comparison query into atomic
single-entity fact queries (e.g. `"GLM-5.2 pricing"`, `"GLM-5.2 technical architecture details"`
instead of one long "how do they compare" question) produced dramatically better real search
results — actual deal terms (€81/share, €1.27 billion, 94% premium) surfaced for the
Persistent-Nagarro acquisition once queries were atomic, versus nothing useful from the compound
version.

**Important nuance discovered**: this decomposition genuinely helps, but it's expensive if
naively multiplied — TechDrishti deliberately did NOT build general query-splitting into its
fixed pipeline because it roughly doubles/triples the number of network searches per comparison,
for a case that was only clearly needed in 1 of 4 real test articles. That tradeoff calculus
doesn't apply the same way to an agent whose entire purpose is deep research — there, the cost of
more searches is the point, not a tax to minimize. But it's still worth being deliberate about a
search budget (see "Open design questions" below).

### Failing case 3 — comparison targets must be grounded, or the model hallucinates them
This is the most important failure mode to design against from day one. When Stage 1 was
reworded to "ask for a comparison fact instead of an opinion," the model started **inventing**
specific wrong comparison targets that were never mentioned in the source article. Confirmed
live, reproduced identically across repeated runs:

> "Leanstral 1.5 vs. GPT-4 Turbo performance on PutnamBench"

Leanstral 1.5 is a 2026 model; GPT-4 Turbo is old and was never mentioned anywhere near it in the
source. The real comparison point the source actually gave was "Opus 4.6." The model has a
strong prior toward well-known model names as default comparison targets regardless of whether
they're actually current or relevant — and **this was not fixable by prompt wording alone**.
Two rounds of increasingly explicit "don't invent unlisted competitors" instructions both failed
empirically (reproduced the identical wrong query in 3/3 and 2/3 runs respectively). What
actually worked was a hard, code-level grounding check: before using any comparison the model
proposes, verify every named entity in it actually appears in material the agent itself already
retrieved or was given — and if not, drop it rather than trust the model's self-report.

**Design implication for the new project**: any time your agent proposes to compare X to Y, Y
needs to be validated against real retrieved evidence that Y is (a) real, (b) current, and (c)
actually relevant — not accepted on the model's say-so. Build this as a code-level check, not a
prompt instruction. This is probably the single highest-value lesson from this whole project for
building the new one.

### Failing case 4 — domain-matching for comparisons
The user's own insight, which is exactly right and worth building in from the start: a
comparison is only useful if the two things being compared are in the same category — an LLM
should be compared to other LLMs, a VLM to other VLMs, not across categories. A real research
agent asked to "find comparable products with a competitive edge" needs to first classify what
kind of thing it's even looking at, then constrain its search/comparison candidates to that same
category. This is a genuine research capability (classify → constrain search space → retrieve →
compare) that a fixed pipeline can't do gracefully, but an agent loop is naturally suited to:
first pass identifies category, second pass searches within it, with the ability to backtrack if
the category guess was wrong.

## Failing case catalog — use these as your eval set

If you build this new project, these are real, reproducible test cases worth carrying over
directly as an evaluation suite (the same way TechDrishti's own test suite was built from real
observed failures, not synthetic ones):

| Query shape | Example | What a good agent should do |
|---|---|---|
| Interpretive/judgment question | "What is the strategic significance of X?" | Decompose into fact sub-queries, synthesize the judgment itself, don't search for it directly |
| Genuine two-entity comparison | "How does GLM-5.2 compare to GPT-5.5?" | Fetch each entity's facts independently, compare in the reasoning step |
| Comparison with no real target named | "How does Leanstral 1.5 compare to existing tools?" | Search for what the source itself names as a comparator; if none exists, either search broadly for real category-mates or say so — never invent a specific name |
| Cross-domain comparison risk | Comparing an LLM's benchmark to a VLM's benchmark | Classify domain/category first; only compare within it |
| Thin/no coverage | A genuinely novel claim nobody has covered yet | Recognize and report "insufficient information found," not hallucinate to fill the gap |

## Design principles learned the hard way (transferable to any research agent)

1. **Search engines return facts, not judgments.** If your planned query sounds like an essay
   prompt ("what does this mean," "what are the implications," "how significant is this"), it's
   not a search query — it's the thing your agent should be producing as output, not sending as
   input.
2. **Never trust "don't hallucinate" as a prompt instruction alone.** It was tried twice here,
   worded carefully both times, and failed both times, reproducibly. Ground every generated
   claim (especially named entities/comparisons) against retrieved evidence with actual code, not
   politeness.
3. **Test with real, current news, not synthetic examples.** Every fix in this handoff was
   validated by fetching genuinely real, dated articles (using WebSearch/WebFetch) and running
   them through the real pipeline — synthetic test fixtures didn't surface the hallucination bug
   at all; only real recent articles about models the base LLM wouldn't have strong opinions on
   did.
4. **Decomposition has a real cost, budget for it deliberately.** Multi-hop research means
   multiple searches; that's fine for a project whose whole point is doing that, but design an
   explicit stopping condition (confidence threshold, max searches, diminishing-returns check) so
   the agent doesn't spiral into unbounded searching the way TechDrishti's own Stage 1 has a
   documented history of runaway generation loops (a separate, analogous bug: the model
   repeatedly ignored a "max 3 items" instruction and produced 300+ near-identical lines in one
   run — only a hard code-level cap actually stopped it, not the prompt asking nicely).
5. **Free/keyless search is viable but has sharp edges.** `ddgs` (not the deprecated
   `duckduckgo_search`) works well; Google News RSS is good for recency but its `link` fields are
   JS-redirect wrappers that can't be scraped directly (use entry titles instead); Wikipedia was
   deliberately excluded project-wide as unreliable/biased, and DDG's own organic results need
   filtering to keep Wikipedia out too, since its ranking can still surface it. All of this code
   already exists and works in `writer/search.py` if you want a starting point rather than
   building a search layer from scratch.
6. **Batch synthesis calls are cheap; network calls are the real cost.** An LLM call that
   synthesizes 5 retrieved snippets into 5 answers costs barely more than one that synthesizes 1
   — the token cost of "more context" is trivial compared to the latency/reliability cost of
   "more HTTP requests." Design your agent's economics around search-call count, not token count.

## Reusable technical assets from TechDrishti

- `writer/search.py` — free-tier search functions (`_ddg_search`, `_google_news_rss`), already
  handles boilerplate-stripping, Wikipedia exclusion, and tiered fallback. Usable as-is or as a
  reference implementation.
- The grounding-check pattern in `writer/synthesize.py`
  (`_query_names_unlisted_competitor`/`_drop_hallucinated_comparisons`) — a working example of
  validating LLM-proposed comparisons against real retrieved/known material in code.
- The general "cheap deterministic filter first, expensive LLM call only on what survives"
  principle, used throughout this codebase (GitHub item pre-filtering, GAP-slot capping) — the
  same idea applies to a research agent: cheap heuristics to prune obviously-bad search results
  before spending a reasoning call on them.

## Suggested MVP scope for the new project

A lean version that still genuinely demonstrates a dynamic loop (not a fixed 3-step pipeline
wearing a trenchcoat):

1. Given a research question, the agent decides whether it's a fact-lookup or a
   judgment/synthesis question (failing case 1).
2. If it names or implies a comparison, the agent identifies what's actually being compared,
   checks domain/category match (failing case 4), and independently retrieves facts for each
   side (failing case 2) — deciding for itself how many searches that requires, not a hardcoded
   count.
3. Every claim in the final output is checked against what was actually retrieved before being
   surfaced — the grounding-check principle (failing case 3), built as code, not prompted.
4. The agent has an explicit stopping condition (confidence check or search budget) rather than
   running to a fixed step count — this is the part that makes it a genuine agent loop rather
   than a slightly-longer fixed pipeline.

## Open design questions for the new project

These weren't resolved here and are genuinely yours to decide:

- What's the actual stopping condition? Confidence self-assessment, a fixed search budget, a
  diminishing-returns heuristic on new information, or some combination?
- How do you evaluate research *quality*, not just "did it produce an answer"? Worth building a
  small real eval set the way this handoff's failing-case table suggests, with known-good and
  known-bad examples from real, dated articles.
- Do you want this to be domain-general (any research question) or scoped like TechDrishti was
  (tech/AI news specifically), at least for a first version? A narrower domain makes both search
  and evaluation easier to get right first.
- Reuse `ddgs`/Google News as the search backend, or use a different/paid search API now that
  cost isn't constrained by a daily-cron budget the way TechDrishti's was?

## The portfolio narrative

This pairs directly with TechDrishti as a "here's when I used a fixed pipeline and why, here's
when I built a real agent loop" contrast for interviews: TechDrishti is deliberately a
cost-controlled, deterministic *workflow* because it runs unattended daily and needs predictable
behavior; this new project is deliberately an autonomous *agent* because research depth is
inherently unbounded in advance. The failing cases above aren't just bug reports — they're the
concrete argument for why the second project needs to exist at all: a fixed pipeline provably
could not handle case 3 (hallucination) without a code-level grounding check, and provably could
not handle case 1 (interpretive questions) without an agent capable of deciding for itself how
many searches to run and when to stop searching and start reasoning.
