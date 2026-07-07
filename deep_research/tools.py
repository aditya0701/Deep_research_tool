"""Search + fetch tools for the research agent.

Adapted from the search-layer lessons documented in TechDrishti's handoff:
`ddgs` (not the deprecated `duckduckgo_search`), Google News RSS using entry
titles directly (the `link` field is a JS-redirect wrapper that can't be
scraped with `requests`), Wikipedia excluded project-wide as unreliable, and
cookie-banner boilerplate filtered out of scraped page text.

`fetch_page` returns a dict, not a bare string, on any failure - a failed
fetch used to come back as a plain string like "Fetch failed: ...", which
looked exactly like real retrieved content to the model. There was no signal
distinguishing "this is genuine page text" from "this is an error message" -
the model had to infer failure from prose, the same problem the grounding
check exists to avoid elsewhere. A structured `{"error": ...}` dict is
unambiguous instead.
"""
import ast
import io
import operator
from datetime import datetime, timezone

import requests
import feedparser
from ddgs import DDGS
from bs4 import BeautifulSoup
from pypdf import PdfReader

_EXCLUDED_DOMAINS = ("wikipedia.org", "wikimedia.org", "wiktionary.org", "wikidata.org")
_BOILERPLATE_MARKERS = ("cookie", "accept all", "reject all", "manage preferences", "subscribe now")


def _is_excluded(url: str) -> bool:
    return any(domain in url for domain in _EXCLUDED_DOMAINS)


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """General web search. Use atomic, fact-seeking queries, not compound questions."""
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results * 2):
            url = r.get("href") or r.get("url", "")
            if _is_excluded(url):
                continue
            results.append({"title": r.get("title", ""), "url": url, "snippet": r.get("body", "")})
            if len(results) >= max_results:
                break
    return results


def news_search(query: str, max_results: int = 5) -> list[dict]:
    """Recent news coverage via Google News RSS. Uses entry titles directly instead of
    scraping `link` fields, which resolve via client-side JS and return a cookie-consent
    interstitial when fetched with `requests`."""
    feed_url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    parsed = feedparser.parse(feed_url)
    return [
        {"title": e.get("title", ""), "url": e.get("link", ""), "snippet": e.get("title", "")}
        for e in parsed.entries[:max_results]
    ]


def _extract_pdf_text(pdf_bytes: bytes, max_chars: int) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        parts.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return " ".join(parts).strip()[:max_chars]


def fetch_page(url: str, max_chars: int = 3000) -> str | dict:
    """Scrape a page's body text for detail beyond a search snippet, filtering
    cookie-banner/boilerplate paragraphs and excluded domains. Returns the text as
    a plain string on success; returns a dict with an "error" key on any failure,
    so a failed fetch is never mistaken for genuine retrieved content."""
    if _is_excluded(url):
        return {"error": "excluded domain", "url": url}
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"error": str(e), "url": url}

    content_type = resp.headers.get("Content-Type", "")
    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        try:
            text = _extract_pdf_text(resp.content, max_chars)
        except Exception as e:
            return {"error": f"PDF parsing failed: {e}", "url": url}
        if not text:
            return {"error": "PDF had no extractable text (likely scanned/image-based)", "url": url}
        return text

    soup = BeautifulSoup(resp.text, "html.parser")
    paragraphs = soup.select("article p, main p") or soup.find_all("p")
    parts = []
    for p in paragraphs:
        text = p.get_text(strip=True)
        if not text or any(marker in text.lower() for marker in _BOILERPLATE_MARKERS):
            continue
        parts.append(text)
    body = " ".join(parts)[:max_chars]
    if not body:
        return {"error": "no readable body text found (possibly JS-rendered or empty page)", "url": url}
    return body


def get_current_date() -> str:
    """Today's date (UTC). The model's own knowledge has a training cutoff and has no other
    way to know what "today" actually is - needed for judging recency ("how long ago was
    this," "is this still current") rather than guessing from training data."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d (%A)")


_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPERATORS:
        return _SAFE_OPERATORS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPERATORS:
        return _SAFE_OPERATORS[type(node.op)](_safe_eval(node.operand))
    raise ValueError(f"unsupported expression element: {ast.dump(node)}")


def calculate(expression: str) -> str | dict:
    """Safely evaluates a purely arithmetic expression (+ - * / ** % and parentheses only -
    no variables, no function calls, no attribute access) via Python's `ast` module rather
    than `eval()`, so it can't execute anything beyond arithmetic. Use this instead of doing
    percentage/CAGR/growth-rate math by hand in reasoning, which is error-prone on multi-step
    calculations."""
    try:
        tree = ast.parse(expression, mode="eval")
        return str(_safe_eval(tree.body))
    except Exception as e:
        return {"error": f"could not evaluate expression: {e}", "expression": expression}


def make_table(headers: list[str], rows: list[list[str]]) -> str:
    """Formats structured comparison data into a guaranteed well-formed markdown table -
    takes the burden of hand-writing correct pipe/alignment syntax off the model, which is
    error-prone inside a long free-text generation. This only formats data the model already
    has; it is not a source of new facts, so its own output is never treated as retrieved
    evidence (see agent.py's _EVIDENCE_TOOLS) - otherwise a table built from invented numbers
    could make itself look "grounded" simply by being present in the conversation."""

    def esc(cell) -> str:
        return str(cell).replace("|", "\\|").replace("\n", " ")

    header_line = "| " + " | ".join(esc(h) for h in headers) + " |"
    sep_line = "|" + "|".join(["---"] * len(headers)) + "|"
    row_lines = []
    for row in rows:
        padded = (list(row) + [""] * len(headers))[: len(headers)]
        row_lines.append("| " + " | ".join(esc(c) for c in padded) + " |")
    return "\n".join([header_line, sep_line, *row_lines])
