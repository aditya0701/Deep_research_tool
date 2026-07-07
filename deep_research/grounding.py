"""Code-level grounding check for comparison claims.

Mirrors TechDrishti's `_query_names_unlisted_competitor` /
`_drop_hallucinated_comparisons` pattern: "don't hallucinate" as a prompt
instruction alone was tried twice there and failed reproducibly, so any named
comparison target in the final report is checked here against text the agent
actually retrieved during research - not trusted to the model's self-report.

This flags rather than silently deletes: comparison sentences whose target
isn't found in retrieved material get an inline `[UNVERIFIED: ...]` marker so
the issue is visible in the output, not hidden.
"""
import re

_COMPARISON_MARKERS = re.compile(r"\b(vs\.?|versus|compared to|comparison with)\b", re.IGNORECASE)
_PROPER_NOUN = re.compile(r"\b[A-Z][A-Za-z0-9\-]*(?:\s[A-Z][A-Za-z0-9\-]*){0,2}\b")
_GENERIC_WORDS = {"Comparison", "Details", "Impact", "Significance", "The", "This", "It", "Overview"}
# Markdown/numbered headings ("### 2.1 Before vs. After:", "2. How This Positions...") are
# title-cased labels, not factual claims - every capitalized word in one looks like a proper
# noun to the regex above, so headings are skipped entirely rather than checked as prose.
_HEADING_LINE = re.compile(r"^\s*(#{1,6}\s|\d+(\.\d+)*\.?\s)")
# Ordinary sentence-boundary split, EXCEPT right after "vs." specifically - the one
# comparison marker this whole module exists to catch contains a period, so the naive
# "split after . ! ?" rule severs "X vs." from "Y" and hides the comparison target in the
# next fragment, where it's never checked at all (no marker there for _COMPARISON_MARKERS
# to find). Confirmed live: this silently let a fabricated "X vs. Y" comparison straight
# through uncaught.
_SENTENCE_BOUNDARY = re.compile(r"(?<!vs\.)(?<=[.!?])\s+", re.IGNORECASE)


def _strip_possessive(name: str) -> str:
    return re.sub(r"['’]s$", "", name)


def find_unlisted_names(text: str, corpus: str) -> list[str]:
    """Extracts proper-noun-like names from `text` and returns whichever ones don't appear
    in `corpus` (already lowercased). Pulled out as its own function so it can be applied
    directly to clean structured data (e.g. make_table's cell values) - not just prose -
    without needing any of the sentence-splitting logic that data doesn't have or need."""
    names = [_strip_possessive(m.group()) for m in _PROPER_NOUN.finditer(text) if m.group() not in _GENERIC_WORDS]
    return [n for n in names if n.lower() not in corpus]


def _looks_like_claim(sentence: str) -> bool:
    """A heading/label fragment ("vs. Accenture:", a bare title line like
    "X: What the Numbers Show vs. the Headline") isn't a factual assertion - and
    it reliably doesn't end the way a real sentence does, because it isn't one.
    Enumerating every possible heading prefix (numbered, "#", ALL CAPS, ...) is
    whack-a-mole; this checks the one thing all of them share instead: no
    terminal punctuation, because nothing was ever asserted to be checked.

    One sharp edge case this needs to handle explicitly: a fragment that ends
    exactly at "vs."/"vs" itself (e.g. "Challenge A: Hierarchy vs.") - a heading
    cut off right at the comparison marker, with no target ever stated. "vs."
    ends in a period too, so the terminal-punctuation check alone would wrongly
    accept it as a complete claim. Confirmed live: this fired on a real report,
    flagging a fragment with nothing to actually check against."""
    stripped = sentence.strip()
    if not stripped:
        return False
    if re.search(r"\bvs\.?$", stripped, re.IGNORECASE):
        return False
    return stripped[-1] in ".!?"


def check_grounding(report_text: str, retrieved_texts: list[str]) -> tuple[str, list[str]]:
    """Returns (annotated_report_text, flagged_sentences).

    Splits on newlines before splitting into sentences - a markdown heading
    (e.g. "### 2. How This Positions X Against Y") has no terminal
    punctuation, so a plain sentence-boundary regex glues it onto the next
    line. If that combined blob happens to contain "vs.", every capitalized
    word in the *heading's own title* gets treated as a comparison target and
    checked - producing nonsense flags like "How This Positions" or "Global
    Competitors" that have nothing to do with an actual claim. Found via a
    real report where this fired twice, both false positives.
    """
    corpus = " ".join(retrieved_texts).lower()
    flagged = []
    checked_lines = []
    for line in report_text.split("\n"):
        if _HEADING_LINE.match(line):
            checked_lines.append(line)
            continue
        sentences = _SENTENCE_BOUNDARY.split(line)
        checked = []
        for sentence in sentences:
            if _COMPARISON_MARKERS.search(sentence) and _looks_like_claim(sentence):
                unlisted = find_unlisted_names(sentence, corpus)
                if unlisted:
                    flagged.append(sentence.strip())
                    sentence = (
                        sentence.rstrip() + f" [UNVERIFIED: {', '.join(unlisted)} not found in retrieved sources]"
                    )
            checked.append(sentence)
        checked_lines.append(" ".join(checked))
    return "\n".join(checked_lines), flagged
