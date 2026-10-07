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

An **autonomous research agent**, not a fixed search-and-summarize pipeline. It decides how many searches to run, what to look for next based on what it has already found, and when it has enough evidence to stop. Claims in the final report are then checked in code against the pages the agent actually retrieved.

Use it in the chat UI on this Space, call it over HTTP, or fork it and run it yourself.

---

## What it can do

| Capability | Description |
|---|---|
| **Autonomous research loop** | Plans its own searches, follows up on findings and stops when it has enough evidence. |
| **Four chat profiles** | Full research report, quick grounded answer, article gap research, and Hindi article writing (see below). |
| **Live reasoning stream** | Thinking and every tool call (search, fetch, calculate) appear in the UI as they happen. |
| **Code-enforced grounding** | Comparison claims in the report are verified against retrieved pages. Anything the agent can't back up is flagged as unverified. |
| **Source tracking** | Every URL actually retrieved is returned alongside the answer. |
| **Hard iteration budget** | A code-enforced cap (8 turns by default) stops runaway search loops. |
| **Multiple sources** | Web search (DuckDuckGo), news search (Google News RSS), and full-page and PDF fetching. Wikipedia is excluded on purpose. |
| **Reliable arithmetic** | Percentages, growth rates and CAGR go through a safe `calculate` tool instead of being computed by the model. |
| **Switchable LLM backend** | DeepSeek (default), Sarvam, or Groq's free tier, selectable per chat. |

---

## Using it

### In the chat UI

Pick a profile when you start a chat:

| Profile | Input | Output |
|---|---|---|
| **Ask a question** | Any research question. | A full research report. |
| **Quick grounded answer** | A question or term. | A short, cited answer. The agent classifies the question first. Simple lookups get a quick verified answer. Ambiguous terms get every plausible meaning plus the one that fits your context. Complex questions are researched in full, then distilled into a grounded conclusion. |
| **Research an article** | An article: first line is the title, then a blank line, then the body. | The agent finds what's missing from the article, researches those gaps and returns the findings. |
| **Write an article** | Same as above. | The same research, then the original article and the new findings woven into one Hindi-language article. |

The **LLM backend** dropdown in the chat settings switches between DeepSeek, Sarvam and Groq. Each answer ends with a note saying which backend and model produced it.

### Reading the output

- **Thinking and tool steps** show what the agent searched and fetched. Open a step to see its input and result.
- **Grounding check** appears when the report names something that doesn't show up in any retrieved page. Treat flagged claims as unverified.
- **Sources** are the URLs the agent actually read, not just ones it mentions in the text.

### Over HTTP

The Space also exposes JSON endpoints for scripts and other services. They need a shared secret in the `X-API-Key` header. If you want access to the hosted Space's key, ask the owner. If you self-host, you set your own (see below).

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/concise` | `{"question": "...", "provider": "groq"}` | Short answer, category, flagged claims, sources, iterations used |
| `POST /api/research` | `{"question": "...", "provider": "groq"}` | Full report and sources |
| `POST /api/article` | `{"title": "...", "body": "...", "write_hindi": true}` | Research report, optional Hindi article, flagged claims, sources |

`provider` is optional (`deepseek`, `sarvam` or `groq`) and defaults to the server's setting.

```bash
curl -X POST https://<your-space-url>/api/concise \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $CONCISE_API_KEY" \
  -d '{"question": "What does CAGR stand for?"}'
```

Requests are synchronous and can take a while, since the agent is running a real research session. Set a generous client timeout.

### Limits to know about

- Research depth is capped by the iteration budget, so very broad questions may come back partly answered.
- The Groq free tier is rate-limited (about 30 requests per minute and daily token caps), so it suits comparison runs better than heavy use.
- The grounding check verifies **names in comparison claims**. It is a safeguard, not a full fact-checker.

---

## Self-hosting

Forks are welcome. You need Python 3.12+ and an API key for at least one provider.

### Run locally

```bash
git clone <your-fork-url>
cd Deep_research_tool
pip install -r requirements.txt

cp .env.example .env
# edit .env and set DEEPSEEK_API_KEY (or switch provider, see below)

chainlit run app.py -w
```

Open `http://localhost:8000`.

CLI, useful for testing:

```bash
python -m deep_research.cli ask "How does DeepSeek V4 compare to GPT-5?"
python -m deep_research.cli concise "What is retrieval-augmented generation?"
python -m deep_research.cli article "Article Title" path/to/article_body.txt
```

### Configuration

| Variable | Default | Description |
|---|---|---|
| `DEEPSEEK_API_KEY` | none | Required when using DeepSeek (the default provider). |
| `LLM_PROVIDER` | `deepseek` | Default backend: `deepseek`, `sarvam` or `groq`. Users can still switch in the UI. |
| `RESEARCH_AGENT_MODEL` | `deepseek-v4-flash` | DeepSeek model (`deepseek-v4-flash` or `deepseek-v4-pro`). |
| `DEEPSEEK_THINKING` | `true` | Enable the model's reasoning mode. |
| `RESEARCH_AGENT_MAX_TOKENS` | provider default | Max tokens per LLM response. |
| `GROQ_API_KEY` | none | Required to use Groq. Free keys at [console.groq.com](https://console.groq.com/keys). |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model. `openai/gpt-oss-20b` is faster and cheaper. |
| `SARVAM_API_KEY` | none | Required to use Sarvam. Can live in a `.sarvanenv` file instead of `.env`. |
| `SARVAM_MODEL` | `sarvam-105b` | Sarvam model. |
| `CONCISE_API_KEY` | none | Shared secret for the `/api/*` endpoints. If unset, every API request is refused. |

Provider keys only matter for the backends you actually use.

### Deploy on Hugging Face Spaces

1. Create a new Space and choose **Docker** as the SDK. The frontmatter at the top of this README already sets `sdk: docker` and `app_port: 7860`.
2. Push this repository to the Space.
3. In **Settings → Variables and secrets**, add your keys as **secrets**: `DEEPSEEK_API_KEY`, plus `CONCISE_API_KEY` if you want the HTTP API, plus any other provider keys.
4. If you added secrets after the first build, restart the Space.

The CPU basic tier is enough. All model work happens through provider APIs, so no GPU is needed. Never commit API keys to the repository.

The container runs as UID 1000 (what Spaces expects) and starts Chainlit on port 7860.

---

## How it works

```
Chainlit UI (app.py)  /  HTTP API (api.py)  /  CLI (cli.py)
                          │
                          ▼
              ResearchAgent loop (agent.py)
        think ─► call tool ─► read result ─► think ...
        (web_search, news_search, fetch_page, calculate, get_current_date)
        until it answers or the iteration budget runs out
                          │
                          ▼
            Grounding check (grounding.py)
```

1. **Think.** The LLM gets the question, the research rules and the history so far.
2. **Act.** It either calls a tool or writes the final report. Tool results are appended to the history.
3. **Enforce the budget.** At `MAX_ITERATIONS` (8 by default) the search tools are removed. Only utility tools like `calculate` stay available, so the model must write its report from what it has.
4. **Check grounding.** Comparison sentences (`vs.`, `versus`, `compared to`, `comparison with`) are scanned. Proper nouns in them must appear in the retrieved page text, and any that don't get an inline `[UNVERIFIED: ...]` flag.

### Design decisions

| Decision | Why |
|---|---|
| Agent-driven search planning | Adaptive depth: easy questions use few searches, hard ones use more. |
| Code-enforced budget and grounding | "Don't loop forever" and "don't hallucinate" failed as prompt-only instructions in earlier projects. |
| Structured tool errors | A failed `fetch_page` returns `{"error": ...}`, so the model can't mistake an error for page content. |
| Wikipedia excluded | Treated as unreliable for claims this agent must ground. |
| AST-based `calculate` | Safe arithmetic without `eval()`. |
| Evidence-only grounding | Only `web_search`, `news_search` and `fetch_page` count as evidence, so `calculate` output can't be used to "verify" a claim. |
| Article mode shares one session | Gap finding happens in the model's first turn, so every angle shares the article context and one evidence pool. |

### The six research rules

Defined in `CORE_RULES` in `agent.py`: decompose interpretive questions into concrete fact questions, check that compared things are the same kind of thing, trace every claim to a retrieved source, prioritize the most valuable searches, retry fetches with other URLs before giving up, and use the calculator for all arithmetic.

### Customizing

- **Budget:** change `MAX_ITERATIONS` in `deep_research/agent.py` (default 8). Sarvam is given 12 in the app.
- **System prompt:** `ResearchAgent(system_prompt="...")`.
- **Add a provider:** add an entry to `_PROVIDERS` in `deep_research/llm_client.py`. Any OpenAI-compatible API works.

---

## Project structure

```
├── app.py                      # Chainlit UI; also mounts the HTTP API
├── chainlit.md                 # Chainlit welcome screen
├── Dockerfile                  # Hugging Face Spaces image
├── requirements.txt
├── .env.example                # Template for local configuration
├── public/                     # Theme and logos
├── deep_research/
│   ├── agent.py                # Research loop, rules, budget
│   ├── api.py                  # /api/concise, /api/research, /api/article
│   ├── article_mode.py         # Article gap research and Hindi writing
│   ├── concise_mode.py         # Short grounded answers
│   ├── grounding.py            # Code-level claim verification
│   ├── llm_client.py           # Provider wrapper (DeepSeek, Sarvam, Groq)
│   ├── tools.py                # Search, fetch, calculate
│   └── cli.py                  # Command-line interface
├── tests/                      # Eval cases, eval runner, concise-mode tests
├── scripts/                    # Brand asset generation
└── prev_project_context/       # Design notes and lessons from the earlier project
```

### Tests and evaluation

```bash
python tests/run_eval.py          # evaluation cases (calls live LLMs)
pytest tests/test_concise_mode.py # live tests skip if DEEPSEEK_API_KEY is unset
```

---

## Contributing

Please make sure that:

1. Claims in generated reports stay grounded in retrieved sources.
2. `grounding.py` keeps catching unverified comparison targets.
3. The tests in `tests/` still pass.
4. Any new tool that retrieves external data is added to `_EVIDENCE_TOOLS` in `agent.py`.

## License

MIT
