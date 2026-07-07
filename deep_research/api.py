"""HTTP API for calling this agent from outside a browser - e.g. a GitHub Actions
workflow or another model/service, neither of which can speak Chainlit's websocket chat
protocol. Deliberately not its own server: Hugging Face Spaces exposes exactly one port,
and Chainlit already owns it (`chainlit.server.app`, a real FastAPI instance) - so this
module adds one route onto that same app instead of standing up a second process that
would need its own port HF Spaces has no way to expose.

Two modes are wired up: concise_mode (a short, cited answer) and plain ask mode (the full
research report, e.g. for a caller that wants the whole write-up rather than a distilled
conclusion). Article mode isn't exposed here - it takes a full article body as input and
produces a Hindi article for a person to read, which fits the chat UI, not a scripted caller.

Protected by a shared-secret header rather than left open, because a public Space with an
unauthenticated POST route is an unauthenticated way for anyone on the internet to spend
this project's DeepSeek API quota.
"""
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .agent import ResearchAgent
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
    )


class ResearchRequest(BaseModel):
    question: str


class ResearchResponse(BaseModel):
    report: str


@router.post("/api/research", response_model=ResearchResponse)
def research_endpoint(body: ResearchRequest, x_api_key: str | None = Header(default=None)) -> ResearchResponse:
    """Plain ask mode (the full report), for a caller that wants the whole write-up rather
    than concise_mode's distilled conclusion. Only the report text is returned - not
    flagged_claims, trace, or iteration count - since a scripted caller asked for "just the
    final report", not the internals a person debugging the agent in the chat UI would want."""
    _check_api_key(x_api_key)
    result = ResearchAgent().run(body.question)
    return ResearchResponse(report=result["report"])
