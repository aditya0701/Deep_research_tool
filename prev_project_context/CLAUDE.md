# TechDrishti (टेकदृष्टि) — Project Context Document

## What is this?
An automated Hindi-language tech journalism pipeline. Every day at 8 AM IST, GitHub Actions runs `pipeline.py`, which:
1. Collects articles from RSS feeds + GitHub trending
2. Clusters related articles by topic
3. For each cluster, runs a 3-stage Sarvam AI writing pipeline to produce an original Hindi article
4. Saves all articles to `output/articles_hindi.json`, which the static frontend reads

**GitHub repo**: `aditya0701/Local_news_aggregator`
**GitHub Pages**: serves from `main` branch root `/`; `index.html` at root redirects to `frontend/index.html`

---

## Critical API Constraints

### Sarvam AI
- **URL**: `https://api.sarvam.ai/v1/chat/completions`
- **Auth header**: `api-subscription-key` (NOT `Authorization: Bearer`)
- **Stage 1 model** (fast): `sarvam-30b`
- **Stage 2+3 model** (quality): `sarvam-105b`
- **Token cap**: `max_tokens: 4096` HARD LIMIT on starter plan — confirmed via the API's own error message: `"max_tokens (6000) exceeds the maximum allowed for sarvam-105b for your subscription tier (starter): 4096. Please reduce max_tokens or upgrade your plan."` Only a plan upgrade raises it.
- **This cap is on COMPLETION tokens only — prompt/input size is a separate budget.** Confirmed
  empirically: sent a deliberately oversized ~4,827-token prompt with `max_tokens: 4096` and
  still got back a full 4,096-token completion (`total_tokens: 8923`, well over the cap) —
  `finish_reason: "length"` because the model was told to ramble past the limit, not because
  the prompt ate into it. Practical implication: source facts, entity context, and paragraph
  plans can be as long as needed without shrinking how much article Stage 3 can write.
- **Reasoning shares the SAME token budget as output** — there is no separate reasoning allowance. Confirmed empirically: a trivial prompt with reasoning left at its default burned 2500-3400+ tokens on `reasoning_content` before writing anything to `content`. If reasoning doesn't leave enough room, `content` comes back empty/`None`.
- **`reasoning_effort` param**: `"low"`, unset (higher/inconsistent — not actually "medium" in practice, confirmed by direct comparison), or `None` (reasoning fully off, guarantees the full budget goes to output). **Important**: putting `/no_think` in the system prompt text does **nothing** — it's plain text the model may or may not follow, not an API control. Only the `reasoning_effort` parameter actually disables reasoning. Confirmed by testing the exact same prompt with and without the param.
- **`_call_sarvam()` signature**: takes an optional `reasoning_effort` kwarg using a sentinel (`_UNSET`) so callers that don't pass it get default behavior (used for Stage 1 Call A and Stage 2, which benefit from real reasoning); Stage 1 Call B and Stage 3 explicitly pass `reasoning_effort=None`.
- **SECURITY**: API key goes in `.env` ONLY. Sarvam's scanner auto-revokes keys exposed in chat/commits.

### Other APIs
- **Tavily and Exa removed entirely this session** (`tavily-python`/`exa-py` dropped from
  `requirements.txt`, `TAVILY_API_KEY` dropped from `daily.yml`). Exa never had a key configured
  in `.env` in the first place; Tavily's key was present but revoked, so in practice every
  search query had already been silently falling through to DDG for a while — the search
  policy is now built to be free-only on purpose rather than papering over dead paid tiers.
  See `writer/search.py`'s tiered replacement below.
- **DuckDuckGo** (`ddgs` package — **not** `duckduckgo_search`, which is deprecated and was found to return empty results or badly mismatched matches for nearly every real query when tested live, e.g. matching "Zeus GPU" to Greek mythology). Switching to `ddgs` fixed this completely (6/6 test queries relevant vs. 5/6 failures before). Now the sole identity-query tier and the last-resort tier for context queries.
- **Wikipedia was tried this session and explicitly removed by the user's direction** — not
  treated as a source in any form, on the grounds that it's too unreliable/biased. This wasn't
  just deleting the dedicated `_wikipedia_search` API tier: DDG's own organic results are
  non-deterministic and can surface a Wikipedia page as a top hit regardless (confirmed live —
  a "Rocket Lab" query returned scraped text with Wikipedia-style citation markers like `[15]`),
  so `_ddg_search` also filters out `wikipedia.org`/`wikimedia.org`/`wiktionary.org`/
  `wikidata.org` domains from its results before using them. **Do not reintroduce Wikipedia as a
  search source in this project without the user's explicit sign-off.**
- **Google News RSS** (no key, parsed with the already-used `feedparser`): context/recency query tier.
- **GitHub token** (`GH_PAT`): for GitHub trending collector. Empty = 60 req/hour unauthenticated vs 5000 authenticated

---

## File Structure (key files only)

```
pipeline.py                          # Orchestrator — entry point
writer/
  synthesize.py                      # THE core file — all 3 Sarvam stages
  entity_cache.py                    # Persistent entity knowledge store
  cluster.py                         # Groups feed items by topic similarity — see "Deduplication
                                      # / Clustering" section below for the full matching rule
  search.py                          # Free-only tiered search, typed by query kind:
                                      # IDENTITY_TIERS = ddgs; CONTEXT_TIERS = google_news_rss ->
                                      # ddgs. No paid tiers (Tavily/Exa removed). Wikipedia is
                                      # explicitly excluded as a source, including from DDG's
                                      # own organic results — see "Other APIs" above.
  web_context.py                     # Scrapes source article body text
  github_gate.py                     # Batched editorial LLM gate for GitHub items (sarvam-105b) —
                                      # runs once per pipeline run on whatever survives the
                                      # deterministic filters below; keeps only genuine new AI/ML
                                      # developments, rejects guides/off-topic/low-substance repos
collectors/
  rss_collector.py
  github_collector.py                # Deterministic pre-filters, all free/no API call:
                                      # DENYLIST (abuse terms), JOB_LISTING_TERMS, listicle-pattern
                                      # regex (year + marketing word e.g. "Ultimate...Guide 2026"),
                                      # MIN_STARS = 20 floor for week-old repos
translator/
  translate.py                       # Google Translate fallback (Stage 1/2/3 processing failures
                                      # ONLY, i.e. returns None — never runs on SKIP). Uses
                                      # source="auto", not "en" — the hardcoded "en" let non-English
                                      # text (e.g. bilingual Chinese/English repo descriptions) pass
                                      # through completely untranslated.
frontend/
  index.html                         # Homepage: card grid with category/tag filtering
  article.html                       # Article detail page
output/
  articles_hindi.json                # Produced by pipeline, read by frontend
  pipeline_trace.json                # Saved after each run — one entry per article
data/
  entity_cache.json                  # Committed back to repo by Actions
config/
  feeds.yaml                         # RSS feed list — 8 feeds (see "RSS Feeds" section below)
  github.yaml                        # GitHub trending search queries
.github/workflows/daily.yml         # Runs `python pipeline.py` at 02:30 UTC (8 AM IST)
```

---

## Pipeline Architecture (writer/synthesize.py)

### SKIP Sentinel
```python
SKIP = object()  # module-level singleton
```
When `synthesize_article()` returns `SKIP`, `pipeline.py` does `continue` — **no Google Translate fallback**.
When it returns `None` (processing failure), the translate fallback runs.

### Stage 0 — Scrape
- `web_context.scrape_source(url)` fetches article body via BeautifulSoup
- If `len(source_text) + len(summary) < 250` → return `SKIP`

### Language Normalization (added this session) — between Stage 0 and Stage 1
Previously the ONLY place Google Translate ran was the fallback path (`translate_item`, used
when Sarvam synthesis fails entirely) — a Chinese- or other-non-English-sourced article that
Sarvam *successfully* synthesized from went straight into Stage 1-3 prompts in its original
language, completely untested (Known Issue #5, now fixed).

- `_detect_language(source_text or summary)` — `langdetect` (local, no API call) on up to a
  500-char sample. Returns `None` on empty input or on `LangDetectException` (raised on
  symbols-only/undetectable text) — treated the same as "assume English, proceed unchanged."
- If detected language isn't `"en"`: `title`, `summary`, and `source_text` are all translated
  to English via `_translate_to_english()` (`deep_translator.GoogleTranslator(source="auto",
  target="en")`) before Stage 1 ever sees them. Falls back to the original text on any
  translation failure — same graceful-degradation pattern as everywhere else in this pipeline.
- Recorded in the trace: `trace["stage0"]["detected_language"]`, `["translated_to_english"]`.
- **Verified live, full end-to-end**, not just unit-tested in isolation: a realistic Chinese
  article (a fictional Zhipu AI "GLM-6" model release, written entirely in Chinese) went
  through the complete pipeline — detected as `zh-cn`, translated to English, Stage 1 correctly
  extracted the real entities (`Zhipu AI`, `GLM-5`, `GLM-6`, and the person `张鹏` correctly
  translated to `Zhang Peng`), and Stage 2/3 produced a complete, well-formed Hindi article.
  Proper nouns stayed in Latin script in the final Hindi output (`Zhipu AI`, `Zhang Peng`) —
  consistent with how every other non-Hindi-origin proper noun (`OpenAI`, `Claude`, etc.) is
  already handled throughout this pipeline, not a new inconsistency.

### Stage 1 — Relevance + Entity Extraction (sarvam-30b), THREE STEPS

A single combined call was found to intermittently return nothing at all — reasoning would
sometimes eat the whole 4096-token budget before writing the actual JSON, causing
`content=None`. A 2-call plan-then-execute split fixed that failure mode, but testing then
found the model's *judgment* itself (verdict vs. its own stated reason disagreeing) needed a
further fix — full journey, all rejected alternatives, and the 4-case validation table are in
`prompt-design-thought-process.md`. Final production design, all in `_stage1_extract_queries()`:

- **Step 1 — skip gate** (`_STAGE1_SKIP_PROMPT`, `reasoning_effort=None`): asks for `REASON:`
  before `SKIP: yes/no`, deliberately in that order — with reasoning off, the model writes
  strictly left-to-right, so asking for the verdict first meant it committed to an answer
  before any reasoning existed to base it on (tested: 0/5 correct one way, 10/10 correct after
  reversing the order). `"skip": true` → return `SKIP` immediately, skipping steps 2-3 entirely.
- **Step 2 — entity + gap analysis** (`_STAGE1_ANALYSIS_PROMPT`, `reasoning_effort=None`,
  `max_tokens=600`): only runs if Step 1 kept the article. Structured as
  TYPE → CHECKLIST → COVERAGE CHECK → GAP1/GAP2/GAP3 (fixed numbered slots, not an open list —
  an earlier open-ended `QUERIES:` list caused a runaway repetition loop, one run producing
  300+ near-identical lines). GAP slots are an explicit maximum, not a target: "if the article
  already covers everything, leave all slots as `none`." Still not fully eliminated by prompt
  wording alone — the repetition bug recurred once in the 4-case validation, once in the first
  live production run (15 queries generated instead of ≤3), and again during the entity-
  truncation-fix verification (9 queries in one run). **Hard code-level cap added this
  session** (`_cap_gap_lines()`, not just prompt wording): truncates the raw analysis text the
  instant a `GAP` line beyond slot 3 appears, so Step 3 never even sees a runaway tail —
  verified against a synthetic 300-line worst-case reproduction (mirroring the real one seen
  live) and confirmed it truncates cleanly at `GAP3` every time. This sits alongside, not
  instead of, the existing `search_queries[:3]` truncation in `_synthesize_sarvam` — that one
  bounds query *usage*, this one bounds the analysis *text* itself, earlier in the pipeline.
- **Step 3 — JSON extraction** (`_STAGE1_EXTRACTION_PROMPT`, `reasoning_effort=None`):
  transcribes Step 2's plain-text write-up into strict JSON
  (`{"search_queries": [...], "entities": [...]}`), explicitly told not to re-analyze or invent
  anything not already in the write-up.

**Source text truncation bug, found and fixed**: Steps 1 and 2 only saw
`source_text[:800]` — on a real article (an eye-perfusion device story), the actual named
entities (the device's name `ECaBox`, the researcher `Shannon Tessier`, the institution
`Barcelona Institute of Science and Technology`) all sat past character 800; the first 800
chars were purely generic scene-setting text. Stage 1 had no choice but to extract generic
nouns (`"device"`, `"perfusion"`, `"researchers"`) instead of the real names, which then
poisoned the identity searches built from those names (see Known Issues) and got cached under
those generic keys. Confirmed as the real cause, not a prompt-wording issue: Stage 2, which
uses `source_text[:2000]`, reliably found all the real names on the same article. Fixed by
raising Steps 1-2's truncation to `source_text[:2000]` to match. Verified: 3 repeat runs on the
same article extracted the real named entities in all 3 (vs. generic nouns in every run
before). Residual, separate issue confirmed in the same test: query-count variance (0 to 9
queries across identical input) and entity-type mistakes (an institution typed as `person`,
everything over-flagged `"ambiguous": true`) are unrelated judgment-inconsistency issues this
fix does not touch.

Known residual limitation: the **judgment itself** (is this a job posting? is this genuine
news?) is still run-to-run inconsistent on borderline/ambiguous cases — the call split
fixed the *token-starvation* failure mode, not the model's own judgment variance. Confirmed
by testing the same ambiguous article 3x and getting different skip verdicts.

- Do NOT skip: articles ABOUT job market trends/hiring booms/crises
- Entity types: `company|startup|ai_model|product|person|researcher|technology|protocol|regulation|event|organization|material`
- Ambiguous entities: `"ambiguous": true, "resolved_sense": "which meaning applies here"`
- If `_stage1_extract_queries()` returns `None` (any step failed), `_synthesize_sarvam()`
  does `stage1 = ... or {}` — this does **not** trigger the translate fallback, it silently
  proceeds with no entities/no skip flag. Only Stage 2/3 returning `None` triggers fallback.

**GAP query phrasing — interpretive questions replaced with fact-lookups, then a hallucination
bug found and fixed with a code-level filter.** Tested this session against 4 real live articles
(GLM-5.2, an India-Japan AI alliance, the Persistent-Nagarro acquisition, Mistral's Leanstral
1.5) run through the actual production Stage 1 + `writer/search.py`, not synthetic examples.

- **Problem found**: Step 2's checklist gives analytical categories ("how it compares to
  competitors," "real-world impact") but no guidance on how to phrase the resulting query, so
  the model wrote its own analytical question out in full as the "query" — e.g. *"What is the
  strategic significance of Z.ai's open-source release in the context of the US ban on
  Anthropic, and what are the likely long-term implications for the global AI model market?"*
  Search engines can only return things already published (a number, a spec, a quote), never a
  judgment nobody has written down, so these queries under-performed even though — surprisingly
  — Google News RSS/DDG's keyword-bag matching still returned *some* relevant material on 3 of 4
  articles (the 4th, India-Japan, generated zero queries since Stage 1 judged the source already
  complete).
- **Fix**: reworded Step 2's query-generation instructions to require a fact-retrievability
  self-check ("could a search realistically return one specific fact that answers this?") before
  writing any GAP query, with WRONG/CORRECT contrastive examples (the same pattern already
  proven for the Stage 3 bleed-through fix). Verified live: GLM-5.2's queries went from one
  47-word interpretive question to `"GLM-5.2 pricing"` / `"GLM-5.2 technical architecture
  details"`; Persistent-Nagarro's queries surfaced the actual real deal terms (€81/share, €1.27
  billion, 94% premium) that the old compound query never returned.
- **Query decomposition (e.g. splitting "compare X and Y" into two atomic queries) was
  considered and explicitly NOT built**, on cost grounds: `_synthesize_search_results` already
  batches every query's raw text into one call, so decomposition's token cost is negligible, but
  it roughly doubles/triples the actual HTTP fetches (Google News RSS + DDG scraping) per
  comparison-type gap, and unlike identity queries, context-query results aren't cached in
  `entity_cache.json` — so that cost is paid fresh on every article with a comparison-shaped
  gap, for a case confirmed real in only 1 of 4 test articles. Decided to keep this lean: accept
  whatever single query the reworded prompt produces, rely on `_synthesize_search_results`'s
  existing honest fallback (`"material did not address this"`) rather than engineer multi-query
  fan-out. Proper multi-hop comparison research (plan → search → reflect → refine) was judged a
  better fit for a dedicated future agentic-research project than bolting onto this pipeline's
  fixed one-call-per-query design.
- **New bug surfaced by the reframing fix itself, found and fixed**: pushed toward "ask for a
  fact," the model started **inventing specific comparison targets** never mentioned in the
  source. Confirmed live, 3 repeat runs on the Leanstral 1.5 article, same wrong query verbatim
  every time: `"Leanstral 1.5 vs. GPT-4 Turbo performance on PutnamBench"` — GPT-4 Turbo is never
  named anywhere in that article; the real comparison point the source actually gives is
  `Opus 4.6`. Two rounds of prompt-only fixes were tried and both failed empirically:
  - Attempt 1 (an explicit "never invent a competitor name" rule with a literal WRONG-example
    query) — failed 3/3 identical runs, reproducing that exact same wrong query verbatim. Root
    cause suspected: the literal example string in the prompt got echoed back instead of just
    illustrating a category to avoid.
  - Attempt 2 (removed the copyable literal example, reworded as an abstract self-check against
    the model's own ENTITIES line) — still failed, 2/3 runs, plus introduced a second symptom
    (one run generated 6 queries instead of the 3-slot maximum). The model has a strong prior
    toward "GPT-4 Turbo"/"Claude 3 Opus" as default comparison targets for any coding/reasoning
    model, and prompt wording alone couldn't override it — the same "wording isn't enough"
    lesson `_cap_gap_lines()` already exists for elsewhere in this file.
  - **Fix that actually worked**: a hard code-level filter, `_query_names_unlisted_competitor()`
    + `_drop_hallucinated_comparisons()`. Doesn't try to guess which names are hallucinated (not
    generally knowable) — it only checks whether a comparison-shaped query (matched via
    `vs`/`versus`/`compared to`/`comparison`) names something that doesn't already appear
    anywhere in the article's own extracted `entities` or source text, and drops the whole query
    if so. Mirrors the same "do not invent anything not already in the write-up" principle
    already used in the Stage 1 extraction prompt, just enforced in code instead of trusted to
    the model's compliance.
  - Two false positives found and fixed while building this filter, both from the same root
    cause (capitalized-word-ish text ≠ real proper noun): (1) the article's own entity in
    possessive form (`"Leanstral's"`) didn't literally match `"Leanstral 1.5"` in the known-name
    substring check — fixed by stripping a trailing `'s`/`’s` before comparing; (2) a sentence-
    initial generic word (`"Comparison of Leanstral's performance..."`) was mistaken for a
    proper noun purely from being capitalized as the first word — fixed with a small denylist of
    generic analysis nouns (`comparison`, `details`, `impact`, `significance`, etc.).
  - **Verified clean after the code-level fix**: 3 fresh live runs on the same Leanstral article,
    zero hallucinated competitor names in any final query list. One run's Step 2 analysis did
    still attempt a hallucinating query (`"...vs. existing code agent models like OpenAI's Code
    Interpreter or Anthropic's Claude Code..."`) — confirming the *underlying* model behavior
    that started this whole investigation is still there — but the code-level filter caught and
    dropped it before it ever reached search, exactly as designed. A genuinely different generic
    comparison query with no invented name (`"Comparison of Leanstral's performance on
    PutnamBench versus other state-of-the-art models"`) correctly passed through unflagged in
    the other two runs, confirming the false-positive fixes hold.
  - Also covered by 16 new pytest cases in `tests/test_synthesize.py`
    (`TestQueryNamesUnlistedCompetitor`, `TestDropHallucinatedComparisons`), built directly from
    these real observed false positives/negatives, not hypothetical ones.

### Cache Check
- `data/entity_cache.json` stores entity knowledge with 45-day TTL
- **Unambiguous schema**: `{canonical_name, entity_type, summary, last_updated}`
- **Ambiguous schema**: `{senses: [{sense_label, summary, last_updated}]}`
- Cache HITs → entity context; cache MISSes → go to web search

### Web Search (writer/search.py) — rebuilt this session, free-only, typed by query kind
- Identity queries: `'"EntityName" entity_type overview'` for each cache miss — routed through
  `IDENTITY_TIERS = [ddgs]`.
- Context queries: up to 3 from Stage 1's `search_queries` (comparison/why-now only, not "what is
  X") — routed through `CONTEXT_TIERS = [google_news_rss, ddgs]`, since these need recent news
  coverage a generic web search wouldn't reliably surface.
- `search_web(queries, tiers)` tries each tier in order per query, moving to the next only if
  the current one returns empty text.
- **Wikipedia was tried as the first identity tier this session, then explicitly removed by the
  user's direction** ("too unreliable and biased... won't be considered a source in any way") —
  not just deprioritized, ruled out entirely. Two real pitfalls surfaced during the brief time it
  was in use, kept here so no one reintroduces it blind to why it was pulled:
  - The full query string (`'"Rocket Lab" company overview'`) skewed Wikipedia's own ranking
    away from the entity's page — resolved to "List of launch service providers" instead of
    Rocket Lab's own article.
  - Wikipedia's search has **no relevance floor** — confirmed live: `"Bolt Graphics" company
    overview` (a real GPU startup with no Wikipedia page) matched "Rock n' Bolt", an unrelated
    1984 video game, and returned it as if it were a good match.
  - Because DDG's own organic ranking is non-deterministic and can surface a Wikipedia page as a
    top hit regardless of the dedicated tier being gone (confirmed live via Wikipedia-style
    citation markers like `[15]` in scraped DDG result text), `_ddg_search` also filters out
    `wikipedia.org`/`wikimedia.org`/`wiktionary.org`/`wikidata.org` domains from its own results.
    **Do not reintroduce Wikipedia as a search source without explicit user sign-off.**
- **Google News RSS pitfall found and fixed** (`_google_news_rss`): entry `link` fields are
  Google redirect-wrapper URLs resolved via client-side JS, not a normal HTTP redirect —
  scraping them with `requests` returned Google's own cookie-consent interstitial page every
  time, not the publisher's article (confirmed live — every "scraped" body was actually "Select
  'More options' to see additional information..." junk). Fixed by not scraping the link at all
  and using the entry titles directly instead, which tested as substantive on their own (e.g.
  "Rocket Lab Stock Surges on Iridium Deal: Is This the Anti-SpaceX Trade?").
- **DDG-shared scraper pitfall found and fixed** (`_scrape_page`, used by the DDG tier for both
  query types): sites like Yahoo Finance / Oracle Blogs return their cookie-consent banner as
  the first paragraphs when the `article p, main p` selector finds nothing and it falls back to
  every `<p>` on the page. Fixed by filtering out paragraphs containing boilerplate markers
  (`cookie`, `accept all`, `reject all`, etc.) before joining.
- Results are synthesized (see below), then stored in the entity cache and assembled into
  `entity_context`.

### Search Result Synthesis (`_synthesize_search_results`) — added this session
Previously `entity_context` was raw, truncated snippet soup — Stage 2 had to guess which part
of the noise actually answered its own query, with nothing guaranteeing it picked right
(confirmed: for a "why now" query, one of two snippets answered it, the other was filler).
Now one batched `sarvam-30b` call (`reasoning_effort=None` — same fix as Stage 1 Call B/Stage 3;
this is a straightforward extraction task, not real reasoning, so turning it off guarantees the
full token budget goes to writing every item's answer) distills each query's raw material into
a direct answer:
- Identity items get a general-purpose overview (explicitly told not to shape it around today's
  story, since the cache reuses it for unrelated future articles).
- Context items get a direct answer to the specific question, or an honest
  `"material did not address this"` if the raw material genuinely doesn't cover it — confirmed
  live the model does this instead of hallucinating when search material is thin.
- Batched into a single call rather than one call per query, since a run can have several
  identity queries (one per cache-miss entity) plus up to 3 context queries.
- **Matched positionally, not by the model's echoed `"query"` field** — confirmed live the model
  normalizes the query text when echoing it back (dropped the quotes from
  `'"Rocket Lab" company overview'`), which silently broke an exact-string dict lookup and would
  have made every identity answer fall back to its raw, unsynthesized snippet instead of the
  synthesized one. Fixed by zipping the original `(query, raw_text)` list against the model's
  answer list by position instead of trusting the round-tripped query string.
- Falls back to the raw truncated snippet per-query if the call fails outright or a query's
  answer is missing from the parsed response — same graceful-degradation pattern as the rest of
  this pipeline, not an all-or-nothing dependency.
- The entity cache now stores the **synthesized** identity answer, not a raw mid-sentence-cut
  snippet — a quality improvement to `entity_cache.json` itself, not just to `entity_context`.

### Stage 2 — Editorial Strategy (sarvam-105b)
Output JSON:
```json
{
  "core_narrative": "the real story and tension",
  "key_facts_and_quotes": "facts/figures that must appear",
  "disambiguation_targets": "terms needing inline Hindi explanation",
  "category": "acquisition|model_release|ban_regulation|repo_analysis|general",
  "paragraph_plan": [
    "Para 1: <specific instruction — what to say, which facts, tone>",
    "Para 2: <specific instruction>",
    "Para 3: <specific instruction>",
    "Para 4: <specific instruction>",
    "Para 5: <optional>",
    "Para 6: <optional>"
  ]
}
```
**paragraph_plan** is the key to the plan-execute split. Stage 2 does all planning; Stage 3 only executes.

**Comprehensive-length change**: originally capped at "3-4 paragraphs, keep each instruction to
1-2 sentences" — this directly caused short articles regardless of the 4096-token completion
budget (see Stage 3 below: real usage was only ~11% of the cap). Raised to "4-6 paragraphs (a
maximum driven by real content, not a target — don't pad a thin story to reach 6), each
instruction 2-4 sentences, explicitly listing out specific facts/figures/quotes to include, not
just a topic label." More paragraphs only if the story genuinely has that much substance.

### Stage 3 — Write Article (sarvam-105b)
- Receives the `paragraph_plan` from Stage 2 formatted as numbered list
- System prompt: `/no_think Output JSON only. No preamble.`
- **`reasoning_effort=None`** is what actually enforces "don't re-think" — the `/no_think`
  text alone does nothing (confirmed: the exact same prompt/system-message combo without
  the `reasoning_effort` param still burned 3000+ tokens on reasoning before Stage 3 ever
  wrote a word, in direct testing). The param is the only real control.

**Full current `_STAGE3_PROMPT`** (kept here verbatim so the actual prompt text is visible
without digging through `writer/synthesize.py`; `{title}`/`{paragraph_plan}`/`{category_framing}`/
`{source_text_block}`/`{entity_context}` are `.format()`-substituted):
````
You are a Hindi writer for टेकदृष्टि. The editorial team has done all the planning — your ONLY job is to execute the writing plan below exactly as instructed.

DO NOT re-plan, re-think, or restructure. Follow each paragraph instruction below, but NEVER copy the
instruction's own wording into the article — the plan tells you WHAT to cover, not what to literally
write. Turn each instruction into actual flowing Hindi prose that DOES what it says, using the source
facts and entity definitions provided.

Write a COMPREHENSIVE, substantial article, not a short summary — you have a generous token budget for
this, use it. Every body paragraph (introduction_lede, deep_dive_and_context, strategic_analysis,
conclusion_and_significance) should be full, multi-sentence Hindi prose that genuinely explains
mechanism, context, background, and implications from the plan and source facts — not a one-line
gist of the paragraph's topic. deep_dive_and_context in particular is the main body of the article:
it should be the longest section, covering every middle paragraph from the plan in real depth.

CRITICAL — instruction vs. content, do not confuse the two:
WRONG (copies the instruction itself, explains nothing): "उपकरण के पीछे की तकनीक की व्याख्या करें। वर्णन करें कि यह कैसे काम करता है।"
CORRECT (actually executes it): "यह उपकरण परफ्यूजन तकनीक का उपयोग करता है, जो आंख की धमनी के माध्यम से ऑक्सीजन युक्त तरल पहुँचाता है।"
If a sentence you're about to write contains a verb like "करें"/"दें" telling the reader what to do (व्याख्या करें, वर्णन करें, उल्लेख करें, शामिल करें), you are copying the instruction, not writing the article — rewrite it as a direct statement of fact instead.

--- WRITING PLAN (describes what each paragraph must cover — an instruction to you, not text to reproduce) ---
Title idea: {title}
Paragraph plan:
{paragraph_plan}

Category framing for STRATEGIC_ANALYSIS paragraph: {category_framing}

--- SOURCE FACTS (use these, do not invent) ---
{source_text_block}

--- ENTITY DEFINITIONS ---
{entity_context}

Mapping the plan's paragraphs (there may be 4-6) onto the JSON fields below:
- The FIRST paragraph in the plan -> introduction_lede
- EVERY paragraph BETWEEN the first and the last (Para 2 through the second-to-last, however
  many that is) -> deep_dive_and_context, combined into one thorough, multi-paragraph-worth section
- The LAST paragraph in the plan -> strategic_analysis, using the category framing

Output ONLY valid JSON with exactly these keys (no markdown, no preamble, no code fences):
{
  "title": "<one sharp Hindi headline based on the title idea>",
  "concept_box": "<2-3 sentences — explain the ONE hardest concept for a newcomer, in simple Hindi>",
  "introduction_lede": "<actual prose fulfilling Para 1's instruction, not the instruction itself — 3-5 substantial sentences>",
  "deep_dive_and_context": "<actual prose combining every middle paragraph's instruction in full depth, not the instructions themselves — this is the main body, cover every fact/comparison/quote from those instructions, several sentences per paragraph covered>",
  "strategic_analysis": "<actual prose fulfilling the final paragraph's instruction using the category framing — 3-5 substantial sentences>",
  "conclusion_and_significance": "<one strong closing paragraph — 3-4 sentences on what this means for the reader>"
}

Language rules (CRITICAL): [see "Language Rules" section below — omitted here to avoid duplication]
````

**Plan-instruction bleed-through bug, found and fixed** (distinct from the label bleed-through
bug described just below — that one was content leaking between adjacent *sections*; this one
is the plan's own *instruction wording* leaking into a section's content): the old prompt said "write each paragraph
*exactly* as the plan specifies" — ambiguous enough that, combined with `reasoning_effort=None`
(no hidden reasoning step to catch the misread) and Stage 2's own imperative-instruction
phrasing ("explain X," "describe Y"), the model would sometimes copy the plan's *instruction
wording* into the article field instead of executing it. Confirmed live on a real article:
`deep_dive_and_context` came back as literally *"उपकरण के पीछे की तकनीक की व्याख्या करें। वर्णन
करें कि..."* ("Explain the technology behind the device. Describe how...") instead of an actual
explanation. Reproduced deliberately with a fixed test fixture (same real Stage 1+2 output,
reused across every trial): the old prompt bled through on 1 of 3 runs. Fixed by adding the
explicit instruction-vs-content distinction and WRONG/CORRECT example shown in the prompt
above. Verified on the same fixed fixture: 8 of 8 runs clean after the fix (small sample, not
claimed as fully eliminated — same honesty standard as every other fix in this doc).

**Comprehensive-length change, verified**: real Stage 3 completion usage before the length
changes was only ~468 tokens (~11% of the 4096 cap) for a full article — confirming article
shortness was a prompt-instruction problem, not the token cap. After the Stage 2 + Stage 3
prompt changes above, the same real article went from 930 total chars across all 6 fields to
2,075-2,269 chars (more than 2x), using ~590 completion tokens (~14% of the cap) — still
comfortable headroom. Also recovered content that was previously getting lost entirely
(Shannon Tessier's expert quote, dropped in the shorter version, now included).

Output format: **JSON**, not labeled plain text (switched this session — see below for why).

**Why JSON instead of labeled text**: the original format asked for exact English labels
(`TITLE:`, `LEDE:`, etc.), but on real articles the model routinely drifted into Hindi labels
(`शीर्षक:`, `लीड:`) and/or wrapped them in markdown bold (`**शीर्षक:**`) — inconsistently,
different phrasing across runs on the *same* article. The old strict-prefix parser missed
these, and worse: sometimes a section's content bled into the *previous* field when its own
label wasn't recognized, silently leaving `strategic_analysis` empty while its actual content
sat inside `deep_dive_and_context`. Switching to JSON reuses the already-robust
`_parse_json_response()` (same one Stage 1/2 use, tolerant of markdown code fences and
trailing junk) and eliminates the label-drift failure mode entirely — confirmed via a direct
head-to-head test on the same real article: JSON came back with all 6 fields populated every
time; labeled text produced an empty `strategic_analysis` field due to the bleed-through bug.
The legacy labeled-text parser (`_parse_labeled_text`, now with Hindi-label-synonym matching
and markdown-stripping added defensively) is kept only as an automatic fallback if a response
ever fails to parse as JSON.

### Language Rules (enforced in Stage 3 prompt)
- Every sentence in Hindi — verb, conjunction, adjective all Hindi
- CORRECT: `"स्वायत्त एजेंट (Autonomous Agent) एक सरल निर्णय-चक्र पर काम करते हैं।"`
- WRONG: `"Individual agents बहुत simple हैं और एक loop follow करते हैं।"`
- Technical terms: Devanagari first, English in parentheses — `मेमोरी स्टोर (Memory Store)`
- Predictions hedge: `हो सकता है, संभावना है`

### Category Framing (_CATEGORY_FRAMING dict)
- `acquisition`: always hedge with हो सकता है / संभावना है
- `model_release`: translate benchmark numbers into practical meaning
- `ban_regulation`: separate immediate vs speculative implications
- `repo_analysis`: explain real-world impact with a simple analogy

### Post-processing
- `_is_meta_line()`: strips model self-commentary (English-only lines starting with "Let me/Here's/I'll/Note:" etc.)
- `_trim_to_last_sentence()`: if text ends mid-sentence (no `।!?` or a *real* `.`), trims to
  last complete sentence. **Decimal-point-aware** (fixed this session) — it used to treat any
  `.` as a sentence end, which truncated titles containing version numbers or percentages
  mid-number (`"GLM-5.2"` → `"GLM-5."`, `"32.8%"` → `"32."`). Now a `.` between two digits is
  never treated as a sentence boundary.
- `_parse_stage3_output()`: tries `_parse_json_response()` first (primary path); falls back to
  `_parse_labeled_text()` only if that fails.
- `_match_label()` / `_LABEL_SYNONYMS`: defensive fallback-parser hardening — recognizes Hindi
  label variants (`शीर्षक`, `लीड`/`लेड`, `रणनीतिक विश्लेषण`, etc.) and strips markdown emphasis
  (`**`) before matching, in case the labeled-text fallback path is ever exercised.
- Both fix real token-exhaustion / model-drift artifacts observed on live articles, not
  hypothetical ones.

---

## GitHub Item Filtering (two layers, before anything reaches Stage 1)

Added this session after finding zero pre-filtering existed beyond an abuse-term denylist —
a job-listing repo (an internship tracker) sailed straight through to Stage 1 with no gate.

### Layer 1 — deterministic, free (`collectors/github_collector.py`)
- `DENYLIST`: abuse/piracy terms (`exploit`, `cheat`, `jailbreak`, `crack`, `nsfw`)
- `JOB_LISTING_TERMS`: catches internship/hiring/job-board repos by keyword, before any LLM call
- **Listicle-pattern regex** (`_looks_like_listicle`): flags repos where the title/description
  has both a year stamp (`20XX`) *and* a marketing word (`top`, `ultimate`, `proven`, `guide`) —
  e.g. `"Ultimate Claude Fable 5 Guide 2026"`, `"Top Agent Swarm Simulation Tools...2026"`.
  Confirmed against live trending data before wiring in: every repo matching both signals was
  independently flagged as low-substance/SEO-spam by a quality check.
- `MIN_STARS = 20`: a repo created in the last 7 days with under 20 stars hasn't shown real
  traction — filters noise, not popularity per se.
- Repos are deduped by URL (`pipeline.py: collect()`) before the next layer, since the same
  repo often matches multiple topic queries in `config/github.yaml`.

### Layer 2 — editorial judgment gate (`writer/github_gate.py`)
- Batched call (15 repos/call) to **sarvam-105b**, framed as an editor for a premium AI/ML
  publication: approve only genuine new AI/ML developments (new model, architecture,
  framework, infra component); reject guides/tutorials/roundups/"awesome-lists", off-topic
  repos, generic toy projects, and anything reading like SEO/clickbait.
- **Known limitation, accepted, not chased further**: verdicts are batch-context-dependent —
  the same repo can be approved or rejected depending on what else is in its batch of 15.
  Confirmed directly: two repos majority-rejected in an isolated 3-run test were approved
  in a different full-batch run. This is the same run-to-run inconsistency documented for
  Stage 1/2 judgment calls throughout this doc — batching doesn't eliminate it.
- `config/github.yaml`'s topic queries were narrowed this session — removed the generic
  `developer-tools` tag (too broad, pulls in non-AI content) and added `rag`
  (retrieval-augmented-generation), to bias collection toward AI-infra specifically.

---

## RSS Feeds (config/feeds.yaml)

Expanded this session from 2 feeds to 8, after noticing GitHub trending structurally dominated
daily output (8 GitHub topic queries × up to 10 repos each ≈ 80 raw candidates/day, refreshed by
a rolling 7-day "created" window, vs. just 2 RSS feeds × `limit=10` ≈ 20 raw candidates/day at
most — and fewer in practice, since consecutive days' top-10 lists overlap and get filtered by
the `seen_ids` dedup). Verified live: usable RSS candidates went from ~15/run (Hacker News + MIT
Tech Review only) to **61/run** across all 8 feeds.

| Feed | URL | Why added |
|---|---|---|
| Hacker News | `https://news.ycombinator.com/rss` | original |
| MIT Technology Review | `https://www.technologyreview.com/feed/` | original |
| TechCrunch | `https://techcrunch.com/feed/` | general tech journalism volume |
| The Verge | `https://www.theverge.com/rss/index.xml` | general tech journalism volume |
| Ars Technica | `https://feeds.arstechnica.com/arstechnica/index` | general tech journalism volume |
| VentureBeat AI | `https://venturebeat.com/category/ai/feed/` | AI-specific trade press |
| OpenAI News | `https://openai.com/news/rss.xml` | official model-announcement source |
| Google DeepMind Blog | `https://deepmind.google/blog/rss.xml` | official model-announcement source |

**Anthropic was deliberately left out** — every plausible RSS URL pattern
(`/news/rss.xml`, `/rss/news.xml`, `/feed.xml`, `/rss`, `/news.rss`) returned 404 when tested
live; they don't appear to expose a public RSS feed. Don't add a guessed URL here without
verifying it actually parses first.

---

## Deduplication / Clustering (writer/cluster.py)

`group_by_topic()` groups collected items whose titles describe the same underlying story
(title-only, no full-article-text fetch, to stay consistent with the rest of the pipeline's
snippet-first approach) so `pipeline.py` synthesizes ONE article per cluster (with all matched
items listed in `sources[]`) instead of one duplicate article per outlet covering the same
event. This is separate from the exact-URL dedup in `pipeline.py: run()` (`seen_ids`), which
stops the same URL being reprocessed across daily runs — clustering handles the same STORY
appearing under different URLs from different outlets on the same day.

**Three design iterations this session**, each driven by real false positives/negatives found
against live multi-feed batches, not hypothetically:

1. **Word-overlap counting** (first rebuild): required 2+ overlapping significant words, where
   at least one had to be "distinctive" (mentioned in <3 titles in the batch via a hand-rolled
   `doc_freq` counter). This fixed the original bug (a single shared generic word like
   "disaster" wrongly merging an Xbox story with a Supergirl review), but the *hard* frequency
   cutoff was noisy on small batches — a coincidental word ("startup") hitting exactly the
   threshold count once broke a genuine duplicate pair.
2. **TF-IDF cosine similarity** (superseded, see below): `TfidfVectorizer` + `cosine_similarity`
   (scikit-learn) over titles. Fixed the word-overlap problems above, tuned to
   `_SIMILARITY_THRESHOLD = 0.3` against a live batch (confirmed false positives Claude
   Science/Cowork 0.244, Xbox/Supergirl 0.223 scored below it; a confirmed genuine duplicate
   scored 0.551 above it) — but being pure bag-of-words, it could not catch two headlines
   describing the same event in genuinely different wording. Confirmed directly: a real pair
   about the same story ("The Download: a startup has a solution for AI's groupthink problem" /
   "LLMs are stuck in a groupthink groove...") scored only 0.236 — indistinguishable from the
   confirmed false positives at that score, meaning no threshold could separate it correctly.
3. **Sentence embeddings, `all-mpnet-base-v2`, cosine similarity (current)**: replaces TF-IDF
   entirely. `SentenceTransformer("all-mpnet-base-v2")` (free, local, ~420MB, runs on CPU, no API
   key) turns each title into a 768-dim vector representing meaning rather than word overlap, so
   paraphrased duplicates score highly even with almost no shared vocabulary.

**Why embeddings were adopted over TF-IDF despite a real, confirmed trade-off** — this decision
was made deliberately, with the actual numbers seen before deciding, not by assuming embeddings
are strictly better:
- Tested `all-MiniLM-L6-v2` (small, 90MB) first: raised the groupthink pair's score from TF-IDF's
  0.236 to 0.368, but the Claude Science/Cowork false positive rose even more, to 0.460 — putting
  the false positive *above* the true positive. No threshold works with this model.
- Tested `all-mpnet-base-v2` (larger, ~420MB, stronger model) next: groupthink rose to 0.460,
  this time landing above the isolated Cowork false positive (0.417). But testing the *full*
  pairwise matrix of the existing 4-item Claude/Anthropic test batch (not just the isolated
  Cowork pair) surfaced a worse false positive the isolated-pair test had never exposed:
  "Claude Science is Anthropic's newest flagship product" vs. "Anthropic raises new funding round
  for Claude infrastructure" — two genuinely unrelated announcements (a product launch vs. a
  funding round) — scored **0.645**, higher than the groupthink true positive (0.460). No single
  threshold can separate 0.417 (correctly-separate) from 0.645 (should-separate) while also
  keeping 0.460 (should-merge) below it — blending TF-IDF and embedding scores was also checked
  and doesn't rescue this, the two problem cases land almost on top of each other either way.
- **Root cause, not a tuning problem**: general-purpose sentence embeddings score *topical/
  semantic* closeness, not *"is this the same specific news event."* Two headlines sharing a
  company/product family (Anthropic, Claude) read as close to the model even describing unrelated
  events — the mirror-image failure mode of TF-IDF (which under-merges same-story-different-
  wording; embeddings over-merge same-entity-different-story).
- **The decision, made explicitly by the user after seeing this evidence**: adopt
  `all-mpnet-base-v2` anyway, accepting the Claude Science/funding-round-style false positive as
  the cost. Reasoning: for a newspaper, a reader seeing the same story published twice
  ("this outlet is repeating itself") is worse than one distinct story occasionally being folded
  into another's write-up and not getting its own separate article that day. TF-IDF's failure
  mode (duplicate publication) was judged unacceptable; the embedding failure mode (occasional
  wrong merge) was judged an acceptable, bounded cost. This is a deliberate reader-experience
  prioritization, not an oversight — a *different* project, or a stricter one about never losing
  a distinct story, should keep TF-IDF instead (see git history for that version).
- `_SIMILARITY_THRESHOLD = 0.44`, tuned against the full known false-positive/true-positive set:
  both true positives (carbon-manure digest 0.622, groupthink 0.460) score above it; the
  Xbox/Supergirl (0.267), filler-word (0.299), and Claude Science/Cowork (0.417) false positives
  score below it; the Claude Science/funding-round pair (0.645) is the one known, accepted
  exception described above.
- **What did NOT change**: the model compares titles only (no full-article-text fetch), same as
  before, and the greedy assignment algorithm (`assigned` array, forward-only pairwise scan) is
  unchanged — only the similarity source (embeddings instead of TF-IDF vectors) and the threshold
  were replaced.
- **A genuinely different property vs. TF-IDF, worth knowing**: TF-IDF's word weighting was
  corpus-relative (a word's weight depended on how often it recurred across the whole batch), so
  the old test fixtures needed realistic surrounding "padding" items to be representative.
  Embedding similarity is computed independently per title pair — batch composition doesn't
  change any pairwise score — so that padding is no longer load-bearing, though the fixtures were
  left as-is since they still pass and reflect realistic batches.
- **GitHub Actions**: the model is cached via `actions/cache` in both `daily.yml` and `tests.yml`
  (key `hf-model-all-mpnet-base-v2-v1`, path `~/.cache/huggingface`) so it downloads from
  Hugging Face once, not on every run — comfortably within GitHub's 10GB-per-repo cache
  allowance at ~420MB, and a daily cron run keeps the cache's 7-day inactivity clock from ever
  expiring it. Both workflows also set `HF_HUB_DISABLE_XET=1`: confirmed directly during
  development that Hugging Face's newer "xet" transfer backend repeatedly hit connection
  timeouts and stalled the model download for 10+ minutes before ever giving up, while the
  classic HTTP downloader (this flag) fetched the same file in under a minute.

---

## Article Output Schema (articles_hindi.json)

Each article dict:
```json
{
  "id": "<sha1 hash of url(s)>",
  "first_seen": "<UTC ISO timestamp>",
  "url": "<primary source URL>",
  "title": "<Hindi title>",
  "summary": "<same as introduction_lede for Sarvam articles>",
  "concept_box": "<2-sentence newcomer explainer>",
  "introduction_lede": "<Para 1>",
  "deep_dive_and_context": "<Para 2+3>",
  "strategic_analysis": "<Para 4>",
  "conclusion_and_significance": "<closing>",
  "category": "acquisition|model_release|ban_regulation|repo_analysis|general",
  "tags": ["EntityName1", "EntityName2"],
  "sources": ["url1", "url2"],
  "source": "rss|github|synthesized",
  "feed_name": "<RSS feed's configured name, e.g. 'Hacker News', 'TechCrunch' — RSS items only>",
  "language": "hindi"
}
```
Legacy articles (translate fallback) only have `title`, `summary`, `url`, `source`, `language`
(plus `feed_name` too now, for RSS-sourced legacy articles — see below).

**`feed_name` — added this session.** Previously every RSS item was tagged with a flat
`"source": "rss"` regardless of which of the 8 configured feeds it came from, making it
impossible to tell from the stored data alone whether a given article came from Hacker News,
TechCrunch, OpenAI News, etc. Fixed in `pipeline.py: collect()` — each RSS item is tagged with
its feed's configured `name` (from `config/feeds.yaml`) right after fetching, before anything
else touches it. Not GitHub items (only asked for RSS; GitHub items don't have this ambiguity in
the same way since `writer/github_gate.py`'s judgment doesn't currently record which
`config/github.yaml` query found a repo either — same gap, just not fixed here).

Propagates automatically to the final output with no changes needed elsewhere: both
`_synthesize_sarvam()`'s result (`{**primary, ...}`) and `translate_item()`'s result
(`{**item, ...}`) spread the original item's dict first, so any extra key set during collection
survives into the published article. Verified directly (not just inferred from reading the
spread syntax): tagged a real item with `feed_name: "TechCrunch"`, ran it through
`translate_item()`, confirmed `feed_name` came out the other end unchanged.

---

## Frontend (frontend/)

### index.html
- Loads `../output/articles_hindi.json` via `fetch` (relative path works both locally and on GitHub Pages)
- First article shown as `featured` card (full-width); rest as 3-column `card-grid`
- Pagination: load 12 at a time, "और लेख देखें" button
- **Filter bar**: category buttons (`सभी/सामान्य/मॉडल रिलीज/अधिग्रहण/नियमन/रेपो`). Buttons auto-hide if category absent in data.
- **activeFilter** state → `filteredItems()` → `applyFilter()`
- Card shows: source pill, category pill, title (Rozha One font), summary text (bold, 1.05rem), tag chips, "लेख पढ़ें" link
- **CATEGORY_LABELS** mapping: `{general: 'सामान्य', model_release: 'मॉडल रिलीज', acquisition: 'अधिग्रहण', ban_regulation: 'नियमन', repo_analysis: 'रेपो'}`

### article.html
- Reads `?id=` param, finds matching article in JSON
- Sarvam articles: concept_box (indigo left-border box) → lede → flowing article body
  - Body = `deep_dive_and_context` + `रणनीतिक दृष्टिकोण` subhead + `strategic_analysis` + `निष्कर्ष` subhead + `conclusion_and_significance`
  - All in one `<div class="article-body">` — NOT separate panels
- Legacy articles: summary text only
- `संपादकीय अंश` panel: shows first sentence of lede as a pull quote
- `स्रोत विवरण` panel: source metadata + "मूल स्रोत खोलें" link(s)
- `isSarvamArticle(item)`: checks `item.introduction_lede && item.deep_dive_and_context`

---

## GitHub Actions (daily.yml)

```yaml
on:
  schedule:
    - cron: '30 2 * * *'   # 8:00 AM IST
  workflow_dispatch:        # manual trigger
# secrets needed: SARVAM_API_KEY, GH_PAT (TAVILY_API_KEY removed this session — search is free-only now)
```
After pipeline runs, bot commits `output/articles_hindi.json` + `data/entity_cache.json` back to `main`.
**Known issue**: If you `git push` locally after the bot commits, you get a rejected push. Fix: `git pull --rebase && git push`.

---

## Pipeline Trace (output/pipeline_trace.json)

Saved after each run. Array of per-article dicts:
```json
{
  "url": "...",
  "title": "...",
  "outcome": "published|skipped_no_content|skipped_not_tech_news|stage2_failed|stage3_failed",
  "stage0": {"scraped_chars": 2400, "source_preview": "..."},
  "stage1": {"skip": false, "search_queries": [], "entities": []},
  "cache": {"hits": [], "misses": []},
  "search": {"identity_queries": [], "context_queries": [], "results": {}},
  "synthesis": {"<query>": "<synthesized answer, 400 chars>"},
  "cache_updates": [{"name": "...", "type": "...", "chars_stored": 300}],
  "stage2": {"core_narrative": "...", "paragraph_plan": [], "category": "..."},
  "stage3": {"title": "...", "introduction_lede": "..."}
}
```

---

## Known Issues / Pending Work

1. **Article length — root cause found, largely fixed.** The 4096 token hard cap turned out
   NOT to be the actual constraint on article length: measured real completion usage was only
   ~468 tokens (~11% of the cap) before this session's length fix. The real cause was Stage 2's
   paragraph_plan rules ("3-4 paragraphs, 1-2 sentences each") and Stage 3 never being told to
   write comprehensively. Fixed by raising Stage 2 to 4-6 paragraphs with richer 2-4 sentence
   instructions, and adding an explicit "write a comprehensive article, use the token budget"
   instruction to Stage 3 (see Stage 3 section above for the full before/after). Verified: same
   real article went from 930 to 2,075-2,269 total chars, completion usage rising to only ~14%
   of the cap — so there's still comfortable headroom, the cap itself is not yet the bottleneck.
2. **GitHub token**: `GITHUB_TOKEN` may be empty in `.env` (local), causing rate-limiting. The `GH_PAT` secret IS set in GitHub Actions.
3. **Custom domain**: `techdrishti.in` (~₹800/year) discussed but not yet purchased.
4. **Stage 1/2 judgment inconsistency, not solved**: relevance/skip decisions, entity
   extraction, and the GitHub editorial gate are all run-to-run inconsistent on borderline
   cases — same input, different verdict across repeated runs. `reasoning_effort` tuning
   didn't fix this; it only fixed Stage 3's *token-starvation* failure mode. Accepted as a
   residual risk for now (explicitly: "we can let some garbage pass").
5. **Chinese/non-English source text through the main Sarvam path — fixed this session.** A
   language-normalization step (`_detect_language` + `_translate_to_english`, see "Language
   Normalization" above) now runs between Stage 0 and Stage 1, translating non-English
   title/summary/source_text to English before Sarvam ever sees them. Verified end-to-end with
   a realistic Chinese article, not just unit-tested in isolation — see the Language
   Normalization section for the full result.
6. **`entity_context` raw-snippet problem — fixed this session.** Search is now typed by query
   kind (Wikipedia/DDG for identity, Google News RSS/DDG for context — all free, see Web Search
   above) and every result goes through a synthesis pass (`_synthesize_search_results`) before
   reaching `entity_context`, instead of dumping raw snippet soup for Stage 2 to guess at.
7. **SearXNG was considered and deliberately not adopted** as a DDG replacement — it's a
   self-hosted metasearch aggregator with the same underlying scraping fragility as DDG,
   just spread across more engines, and needs a persistent host (GitHub Actions runners are
   ephemeral). The actual DDG problem turned out to be a deprecated package
   (`duckduckgo_search`, replaced with `ddgs`), fixed for free without needing SearXNG.
8. **Stage 3 plan-instruction bleed-through — investigated and fixed this session.** On a real
   article, `deep_dive_and_context` came back as the plan's own instruction wording ("Explain
   the technology behind the device. Describe how...") instead of an actual explanation — the
   model copied the instruction rather than executing it. Root cause and fix are detailed in
   the Stage 3 section above (ambiguous "write exactly as specified" phrasing + no hidden
   reasoning to catch the misread + Stage 2's own imperative plan phrasing). Verified via a
   fixed test fixture: old prompt bled through 1 of 3 runs, new prompt clean 8 of 8 — a real,
   measured improvement, not claimed as fully eliminated given the small sample.
9. **Stage 1 entity extraction producing generic nouns instead of named entities — found and
   fixed this session.** Root cause: Steps 1-2's `source_text[:800]` truncation frequently cut
   off before an article's actual named entities appeared, forcing extraction of generic terms
   like `"device"`/`"perfusion"`/`"researchers"` instead. This poisoned identity search queries
   built from those names (e.g. `'"device" technology overview'` returned a generic dictionary
   definition of the word "technology"; `'"perfusion" protocol overview'` returned an unrelated
   veterinary mouse-euthanasia protocol) and cached the garbage under those generic keys. Fixed
   by raising the truncation to `source_text[:2000]`, matching Stage 2 (which already reliably
   found the real names at that length). Verified: 3 repeat runs on the same article extracted
   real named entities in all 3, vs. generic nouns every time before. Separate, NOT fixed by
   this change: the same test showed query-count variance (0 to 9 across identical input) and
   entity-type mistakes (an institution typed as `person`, near-universal
   `"ambiguous": true` over-flagging) — pre-existing judgment-inconsistency issues, tracked
   under item 4 above, not caused by or solved by the truncation fix.
10. **Search result quality for generic/mistyped entities — downstream of item 9, not yet
    separately addressed.** Even with better-named entities, search relevance for whatever
    Stage 1 does extract hasn't been hardened further this session — worth revisiting once
    entity extraction quality is confirmed stable over more real articles.

---

## Design System

CSS variables (consistent across both HTML files):
```css
--cotton: #f3e8c9;     /* background base */
--red: #8b2626;        /* primary brand color */
--indigo: #1a3644;     /* concept box accent */
--mustard: #d49a36;    /* decorative accent */
--charcoal: #3a2e2a;   /* body text */
```
Fonts (Google Fonts):
- **Rozha One**: large headings / brand name
- **Anek Devanagari**: article titles, subheadings
- **Martel Sans / Mukta**: body text
- **Yatra One**: labels, uppercase metadata
