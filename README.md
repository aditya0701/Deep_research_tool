---
title: Deep Research Agent
emoji: 🔎
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# Deep Research Agent

An autonomous research agent, not a fixed search-and-summarize pipeline: it
decides how many searches to run, what to search next based on what it's
already found, and when it has enough grounded evidence to stop.

Every claim is checked in code against the pages actually retrieved during
the run — comparisons and facts have to show up in the evidence, not just be
asserted by the model.

**Two modes:**
- **Ask a question** — direct research questions.
- **Research an article** — paste an article; the agent finds what's
  genuinely missing and researches those gaps.

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in DEEPSEEK_API_KEY
chainlit run app.py -w
```

## Deploying

Ships as a Docker image (see `Dockerfile`) — works on Hugging Face Spaces
(this README's frontmatter is already set up for that: `sdk: docker`,
`app_port: 7860`), Render, Fly.io, or any host that runs containers.

Required secret: `DEEPSEEK_API_KEY` (set it as a Space secret / host env var,
never commit it — see `.env.example` for the other optional variables).
