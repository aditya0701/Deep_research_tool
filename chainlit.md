# Deep Research Agent

This isn't a single-shot search-and-summarize pipeline. The agent runs its own
research loop: it decides how many searches to run, what to search next based
on what it's already found, and when it has enough grounded evidence to stop.

Every claim it makes is checked in code against the pages it actually
retrieved — no comparison or fact makes it into the final answer unless it
was found in the evidence, not just asserted by the model. Unverified claims
are flagged inline rather than silently kept or dropped.

**Four modes, in the sidebar:**
- **Ask a question** — pose any research question directly and get the full
  research report.
- **Quick grounded answer** — a short, cited answer instead of the whole
  report. Simple questions get a fast verified answer, ambiguous ones get
  every plausible meaning plus which one fits your context, and genuinely
  complex questions are still fully researched — you just get the distilled,
  grounded conclusion back, not the raw write-up.
- **Research an article** — paste an article; the agent finds what's
  genuinely missing from it and researches those gaps, then hands back the
  raw findings.
- **Write an article** — same gap research, but the findings are woven
  together with your original article into one complete Hindi-language
  article.

For article modes, paste your text as **first line = title**, blank line,
then the article body.

**Live reasoning panel** — watch each search, fetch, and thinking step stream
in as it happens, not just the final report.

**LLM backend** — switch between providers (DeepSeek / Sarvam) from the
settings panel (⚙️) at any time; the response tells you which backend and
model actually answered.

**Grounding check** — after the report is generated, comparison claims are
verified against the retrieved sources in code. Anything that can't be
confirmed is flagged separately as unverified rather than silently included.
