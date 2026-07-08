# Deep Research Agent

This isn't a single-shot search-and-summarize pipeline. The agent runs its own
research loop: it decides how many searches to run, what to search next based
on what it's already found, and when it has enough grounded evidence to stop.

Every claim it makes is checked in code against the pages it actually
retrieved — no comparison or fact makes it into the final answer unless it
was found in the evidence, not just asserted by the model.

**Two modes, in the sidebar:**
- **Ask a question** — pose any research question directly.
- **Research an article** — paste an article; the agent finds what's
  genuinely missing from it and researches those gaps.

Watch the reasoning panel as it runs — each search, fetch, and thinking step
streams live, not just the final report.
