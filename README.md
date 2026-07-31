# Deep Research Agent

An **autonomous research agent** powered by DeepSeek V4 Flash — not a fixed search-and-summarize pipeline. It decides how many searches to run, what to search next based on what it's already found, and when it has enough grounded evidence to stop. Every claim is checked in code against the pages actually retrieved during the run.

[![Hugging Face Spaces](https://img.shields.io/badge/🤗%20Hugging%20Face-Spaces-blue)](https://huggingface.co/spaces)
[![DeepSeek](https://img.shields.io/badge/DeepSeek-V4%20Flash-4F46E5)](https://deepseek.com)
[![Chainlit](https://img.shields.io/badge/UI-Chainlit-2D9CDB)](https://chainlit.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#)

---

## ✨ Features

| Capability | Description |
|---|---|
| **Autonomous research loop** | The agent plans its own searches, follows up on findings, and decides when it has enough evidence — no fixed pipeline. |
| **Two modes** | **Ask a question** for direct research, or **Research an article** to find gaps in existing content. |
| **Live reasoning stream** | Watch the agent think, search, and fetch in real time — every step streams to the UI as it happens. |
| **Code-enforced grounding** | Comparison claims are automatically verified against retrieved sources. Unverified claims are flagged with `[UNVERIFIED: ...]` markers. |
| **Hard iteration budget** | A code-enforced cap prevents runaway search loops — the agent must prioritize its best searches first. |
| **Multi-source retrieval** | Web search (DuckDuckGo), news search (Google News RSS), and full-page/PDF fetching. |
| **Safe arithmetic** | AST-based `calculate` tool for precise math — the agent never computes percentages or CAGR by hand. |
| **Dark/light theme** | Custom Chainlit theme with indigo/blue accents. |

---

## 🚀 Deploy on Hugging Face Spaces

### One-click deployment

1. Go to [Hugging Face Spaces](https://huggingface.co/new-space)
2. Enter a **Space name** (e.g., `deep-research-agent`)
3. Choose **Docker** as the SDK (the `sdk: docker` in this README's frontmatter auto-detects this)
4. Select a **Space hardware** tier (CPU basic is fine for light use; upgrade to CPU upgrade or GPU for faster responses)
5. Click **Create Space**
6. In your Space's **Settings → Repository secrets**, add:

| Secret | Value | Required |
|---|---|---|
| `DEEPSEEK_API_KEY` | Your DeepSeek API key | ✅ Yes |
| `RESEARCH_AGENT_MODEL` | Model name (default: `deepseek-v4-flash`) | ❌ Optional |
| `DEEPSEEK_THINKING` | Enable thinking mode (`true`/`false`, default: `true`) | ❌ Optional |
| `RESEARCH_AGENT_MAX_TOKENS` | Max tokens per response (default: `8000`) | ❌ Optional |

7. The Space will build automatically — grab a coffee ☕ while Docker builds.

> **Important:** Never commit your API key to the repository. Use **Space secrets** only.

### Space hardware recommendations

| Tier | Use case |
|---|---|
| **CPU basic** (free) | Light research, short queries, testing |
| **CPU upgrade** | Heavier research with more page fetches |
| **Nvidia T4 / A10G** | Not needed — the agent is API-driven, no local GPU required |

### How it works on Spaces

The `Dockerfile` builds a container that:
1. Installs Python dependencies from `requirements.txt`
2. Exposes port `7860` (configured via `app_port` in frontmatter)
3. Starts Chainlit with `chainlit run app.py --host 0.0.0.0 --port 7860 --headless`

The container runs as UID 1000 for compatibility with Hugging Face's runtime environment.

---

## 🧠 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Chainlit Web UI (app.py)                  │
│  ┌────────────────┐  ┌──────────────────────────────────┐   │
│  │ Ask a Question  │  │      Research an Article         │   │
│  │    (Mode 1)     │  │          (Mode 2)                │   │
│  └───────┬─────────┘  └──────────┬───────────────────────┘   │
│          │                       │                            │
│          └───────────┬───────────┘                            │
│                      ▼                                        │
│           ┌──────────────────┐                                │
│           │  ResearchAgent   │  ◄── on_step callback streams  │
│           │  (agent.py)      │       every step live to UI    │
│           └────────┬─────────┘                                │
└────────────────────┼──────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│                    ResearchAgent Loop                         │
│                                                               │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐  │
│  │  Think   │──►│  Search  │──►│  Fetch   │──►│ Evaluate │  │
│  │ (LLM)    │   │ (Tools)  │   │ (Pages)  │   │ (LLM)    │  │
│  └──────────┘   └──────────┘   └──────────┘   └─────┬────┘  │
│         ▲                                            │       │
│         └────────────────────────────────────────────┘       │
│         (loop until enough evidence or budget exhausted)      │
│                                                               │
│  Hard cap: MAX_ITERATIONS = 8 (configurable)                  │
│  When budget exhausted: utility tools only, then final report │
└──────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│                   Grounding Check (grounding.py)              │
│                                                               │
│  • Scans final report for comparison phrases                  │
│    (vs., versus, compared to, comparison with)                │
│  • Extracts proper nouns from comparison sentences            │
│  • Verifies each name appears in retrieved page text          │
│  • Flags unverified names inline with [UNVERIFIED: ...]       │
│  • Skips headings, handles edge cases (fragments, "vs.")      │
└──────────────────────────────────────────────────────────────┘
```

### Key design decisions

| Decision | Rationale |
|---|---|
| **Agent-driven search planning** | The model decides what to search for, not a hardcoded pipeline — enables adaptive research depth. |
| **Code-enforced iteration budget** | Prevents runaway search loops (learned from prior project failures). |
| **Structured error returns from tools** | `fetch_page` returns `{"error": ...}` on failure, not a plain string — the model can't mistake an error for real content. |
| **Wikipedia excluded** | Project-wide policy — Wikipedia is treated as unreliable for the grounded claims this agent produces. |
| **Cookie-banner filtering** | Boilerplate markers are stripped from fetched page text. |
| **Utility tools survive budget cutoff** | `calculate` is still available after the search budget is exhausted — prevents incomplete reports. |
| **AST-based `calculate`** | Uses Python's `ast` module instead of `eval()` — safe from code injection. |
| **Separate evidence tracking** | Only `web_search`, `news_search`, and `fetch_page` count as evidence for grounding — `calculate` results don't create a loophole. |

---

## 💻 Running Locally

### Prerequisites

- Python 3.12+
- A [DeepSeek API key](https://platform.deepseek.com/api_keys)

### Setup

```bash
# Clone the repository
git clone https://huggingface.co/spaces/YOUR_USERNAME/deep-research-agent
cd deep-research-agent

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and set DEEPSEEK_API_KEY=your_key_here

# Launch the app
chainlit run app.py -w
```

Open `http://localhost:8000` in your browser.

### CLI mode (for testing)

```bash
# Mode 1: Ask a direct question
python -m deep_research.cli ask "How does DeepSeek V4 compare to GPT-5?"

# Mode 2: Research gaps in an article
python -m deep_research.cli article "Article Title" path/to/article_body.txt
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | **Required.** Your DeepSeek API key |
| `RESEARCH_AGENT_MODEL` | `deepseek-v4-flash` | Model to use (`deepseek-v4-flash` or `deepseek-v4-pro`) |
| `DEEPSEEK_THINKING` | `true` | Enable reasoning/thinking mode |
| `RESEARCH_AGENT_MAX_TOKENS` | `8000` | Max tokens per LLM response |

---

## 🧪 Evaluation

The project includes an evaluation suite for systematic testing:

```bash
# Run evaluation cases
python tests/run_eval.py
```

See `tests/eval_cases.py` for the test cases and `tests/run_eval.py` for the evaluation harness.

---

## 📁 Project Structure

```
├── app.py                          # Chainlit web UI entry point
├── chainlit.md                     # Chainlit welcome message
├── Dockerfile                      # Container image (HF Spaces compatible)
├── requirements.txt                # Python dependencies
├── .env                            # Local environment variables (gitignored)
├── public/
│   └── theme.json                  # Chainlit UI theme (dark/light)
├── deep_research/
│   ├── __init__.py
│   ├── agent.py                    # Core research agent orchestration loop
│   ├── article_mode.py             # Mode 2: article gap research
│   ├── cli.py                      # Command-line interface
│   ├── grounding.py                # Code-level grounding verification
│   ├── llm_client.py               # DeepSeek API client wrapper
│   └── tools.py                    # Search, fetch, calculate tools
├── tests/
│   ├── eval_cases.py               # Evaluation test cases
│   └── run_eval.py                 # Evaluation runner
├── scripts/
│   └── generate_brand_assets.py    # Brand asset generation
└── prev_project_context/           # Design docs & lessons from prior project
```

---

## ⚙️ How the agent works (detailed)

### 1. Research loop (`agent.py`)

The agent runs an iterative loop (up to `MAX_ITERATIONS = 8`):

1. **Think** — The LLM receives the question + research rules + conversation history
2. **Decide** — The model chooses to either call a tool or produce the final report
3. **Act** — If a tool is called, the result is appended to the conversation
4. **Repeat** — Steps 1-3 until the model stops calling tools
5. **Enforce budget** — If `MAX_ITERATIONS` is reached, search tools are forcibly removed; the model gets one final turn with only `calculate` available
6. **Grounding check** — The final report is scanned for comparison claims; each is verified against retrieved page text

### 2. Six research rules (`CORE_RULES`)

The agent follows these hard-learned rules (documented in `prev_project_context/`):

1. **Decompose interpretive questions** into concrete fact sub-questions — search engines return facts, not judgments
2. **Verify comparison validity** before comparing — confirm two things are genuinely the same *kind* of thing
3. **Every claim must be traceable** to a retrieved source — no facts from training data
4. **Prioritize searches** — limited budget means highest-value searches first
5. **Retry on fetch failure** — try different URLs from the same search results before giving up
6. **Use the calculator** — never compute percentages, CAGR, or growth rates by hand

### 3. Grounding check (`grounding.py`)

The grounding system is a **code-level safeguard**, not a prompt instruction:

- Scans the final report for comparison markers: `vs.`, `versus`, `compared to`, `comparison with`
- Extracts proper nouns from those sentences
- Checks each name against the full text of all retrieved pages
- Flags unverified names inline: `[UNVERIFIED: CompetitorX not found in retrieved sources]`
- Handles edge cases: headings are skipped, fragments ending in "vs." are ignored, possessives are normalized

This pattern was adopted because "don't hallucinate" as a prompt instruction failed reproducibly in prior projects.

### 4. Article mode (`article_mode.py`)

Mode 2 feeds the article directly into the same research loop — gap identification happens as part of the model's own first-turn thinking. This means:

- Every angle the model investigates shares full article context
- All research shares the same retrieved-evidence pool
- No two-stage "dumbed-down" gap extraction needed

---

## 🔧 Customization

### Changing the model

Set the `RESEARCH_AGENT_MODEL` environment variable to switch between:
- `deepseek-v4-flash` (default) — faster, lower cost
- `deepseek-v4-pro` — higher quality, higher cost

### Adjusting the research budget

Modify `MAX_ITERATIONS` in `deep_research/agent.py` (default: 8):

```python
MAX_ITERATIONS = 12  # Allow more search iterations
```

### Custom system prompts

Pass a custom `system_prompt` to `ResearchAgent()`:

```python
from deep_research.agent import ResearchAgent
agent = ResearchAgent(system_prompt="Your custom system prompt here")
```

---

## 📚 Dependencies

| Package | Purpose |
|---|---|
| `openai` | DeepSeek API client (OpenAI-compatible) |
| `chainlit` | Web UI framework |
| `ddgs` | DuckDuckGo search API |
| `feedparser` | Google News RSS parsing |
| `beautifulsoup4` | HTML page scraping |
| `requests` | HTTP client |
| `pypdf` | PDF text extraction |
| `python-dotenv` | Environment variable loading |

---

## 🤝 Contributing

Contributions are welcome! Please ensure:

1. All claims in generated reports remain grounded in retrieved sources
2. The grounding check (`grounding.py`) continues to catch unverified comparison targets
3. The evaluation suite (`tests/`) passes
4. New tools are added to `_EVIDENCE_TOOLS` in `agent.py` if they retrieve external data

---

## 📄 License

MIT
