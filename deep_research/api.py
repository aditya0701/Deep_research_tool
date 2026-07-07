"""HTTP API for calling this agent from outside a browser - e.g. a GitHub Actions
workflow or another model/service, neither of which can speak Chainlit's websocket chat
protocol. Deliberately not its own server: Hugging Face Spaces exposes exactly one port,
and Chainlit already owns it (`chainlit.server.app`, a real FastAPI instance) - so this
module adds one route onto that same app instead of standing up a second process that
would need its own port HF Spaces has no way to expose.

Three modes are wired up: concise_mode (a short, cited answer - the model self-classifies
each question as simple/ambiguous/complex and only does full research for genuinely complex
ones), plain ask mode (the full research report on any question, for a caller that wants the
whole write-up rather than a distilled conclusion), and article mode (given an article's
title/body, researches what's genuinely missing from it and optionally writes the Hindi
article - the same job the Chainlit "Research an article"/"Write an article" profiles do).

Protected by a shared-secret header rather than left open, because a public Space with an
unauthenticated POST route is an unauthenticated way for anyone on the internet to spend
this project's DeepSeek API quota.
"""
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .agent import ResearchAgent
from .article_mode import research_article
from .concise_mode import answer_concisely

router = APIRouter()


class ConciseRequest(BaseModel):
    question: str


class ConciseResponse(BaseModel):
    question: str
    category: str
    answer: str
    research_report: str | None = None
    flagged_claims: list[str]
    iterations_used: int
    # Every URL actually retrieved via a tool call during research (see
    # agent.extract_sources) - reconstructed from the trace independent of whatever
    # citation style the model's own answer text used, so a caller has something concrete
    # to resolve claims against even if the model's inline citations are inconsistent.
    sources: list[str]


def _check_api_key(x_api_key: str | None) -> None:
    expected = os.environ.get("CONCISE_API_KEY")
    if not expected:
        # Fail closed: an unset secret must never be treated as "auth disabled" on a
        # public Space - that would silently reopen the endpoint the moment someone
        # forgets to configure the secret, instead of loudly refusing all requests.
        raise HTTPException(status_code=503, detail="CONCISE_API_KEY is not configured on the server")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")


@router.post("/api/concise", response_model=ConciseResponse)
def concise_endpoint(body: ConciseRequest, x_api_key: str | None = Header(default=None)) -> ConciseResponse:
    """Synchronous `def`, not `async def` - FastAPI runs sync handlers in a worker
    thread pool automatically, so the blocking LLM/HTTP calls inside answer_concisely
    don't stall the event loop Chainlit's own websocket traffic shares this process with."""
    _check_api_key(x_api_key)
    result = answer_concisely(body.question)
    return ConciseResponse(
        question=result["question"],
        category=result["category"],
        answer=result["answer"],
        research_report=result.get("research_report"),
        flagged_claims=result["flagged_claims"],
        iterations_used=result["iterations_used"],
        sources=result["sources"],
    )


class ResearchRequest(BaseModel):
    question: str


class ResearchResponse(BaseModel):
    report: str
    sources: list[str]


@router.post("/api/research", response_model=ResearchResponse)
def research_endpoint(body: ResearchRequest, x_api_key: str | None = Header(default=None)) -> ResearchResponse:
    """Plain ask mode (the full report), for a caller that wants the whole write-up rather
    than concise_mode's distilled conclusion. Only the report text plus its source list are
    returned - not flagged_claims, trace, or iteration count - since a scripted caller asked
    for "just the final report", not the internals a person debugging the agent in the chat
    UI would want."""
    _check_api_key(x_api_key)
    result = ResearchAgent().run(body.question)
    return ResearchResponse(report=result["report"], sources=result["sources"])


class ArticleRequest(BaseModel):
    title: str
    body: str
    # Defaults to True to match article_mode.research_article's own default (mirrors the
    # Chainlit "Write an article" profile) - set false for just the English research
    # findings, matching the "Research an article" profile instead.
    write_hindi: bool = True


class ArticleResponse(BaseModel):
    title: str
    report: str
    hindi_article: str | None = None
    flagged_claims: list[str]
    iterations_used: int
    sources: list[str]


@router.post("/api/article", response_model=ArticleResponse)
def article_endpoint(body: ArticleRequest, x_api_key: str | None = Header(default=None)) -> ArticleResponse:
    """Article mode: given an article's title and full body text, finds what's genuinely
    missing from it, researches those gaps in one session, and (unless write_hindi=false)
    weaves the original article and the new findings into one Hindi-language article -
    exactly what the Chainlit "Research an article"/"Write an article" profiles do, just
    reachable over plain HTTP instead of the chat UI."""
    _check_api_key(x_api_key)
    result = research_article(body.title, body.body, write_hindi=body.write_hindi)
    return ArticleResponse(
        title=result["title"],
        report=result["report"],
        hindi_article=result.get("hindi_article"),
        flagged_claims=result["flagged_claims"],
        iterations_used=result["iterations_used"],
        sources=result["sources"],
    )
