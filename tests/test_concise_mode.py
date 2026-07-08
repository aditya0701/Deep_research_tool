"""Tests for concise_mode.py's classify-then-answer contract.

Split in two tiers, for the same reason eval_cases.py/run_eval.py stayed separate from a
pass/fail suite: judging whether a research report's *prose* is good requires a human, but
the category tag (SIMPLE_ANSWER/AMBIGUOUS_ANSWER/COMPLEX_REPORT) is a real, code-enforced
contract - answer_concisely() either parses it correctly and branches correctly, or it
doesn't, and that part genuinely is assertable without a person reading the output.

- TestTagParsing: mocks ResearchAgent.run and _write_conclusion, so these run instantly,
  need no API key, and pin down the mechanical contract - tag parsing, category mapping,
  the fallback when a tag is missing, and that the complex path's second pass actually runs
  while simple/ambiguous don't.
- TestLiveQueryTypes: calls the real answer_concisely against the real LLM, one case per
  query type (simple, ambiguous, complex/comparison) - skipped automatically unless
  DEEPSEEK_API_KEY is set, same guard style as run_eval.py. These check the classification
  and a few structural invariants (sources present for complex, no dangling bracket
  citations), not prose quality - that part still wants a human reading the output, same as
  eval_cases.py.
"""
import os
import re
from unittest.mock import patch

import pytest

from deep_research.concise_mode import AMBIGUOUS_TAG, COMPLEX_TAG, SIMPLE_TAG, answer_concisely

_BRACKET_CITATION_RE = re.compile(r"\[\d+\]")


def _fake_agent_result(report_text: str, sources=None, trace=None):
    return {
        "question": "irrelevant",
        "report": report_text,
        "flagged_claims": [],
        "iterations_used": 1,
        "trace": trace or [],
        "sources": sources or [],
    }


class TestTagParsing:
    """Mocked - no network, no API key needed."""

    @patch("deep_research.concise_mode.ResearchAgent")
    def test_simple_tag_maps_to_simple_category(self, mock_agent_cls):
        mock_agent_cls.return_value.run.return_value = _fake_agent_result(
            f"{SIMPLE_TAG}\n\nParis is the capital of France."
        )
        result = answer_concisely("what is the capital of France")
        assert result["category"] == "simple"
        assert result["answer"] == "Paris is the capital of France."
        assert "research_report" not in result

    @patch("deep_research.concise_mode.ResearchAgent")
    def test_ambiguous_tag_maps_to_ambiguous_category(self, mock_agent_cls):
        body = (
            "Lean could refer to a functional programming language, a codeine-based drug, "
            "or a body type. Given the coding context here, it refers to the programming "
            "language."
        )
        mock_agent_cls.return_value.run.return_value = _fake_agent_result(f"{AMBIGUOUS_TAG}\n\n{body}")
        result = answer_concisely("what is lean, in the context of a coding tutorial")
        assert result["category"] == "ambiguous"
        assert result["answer"] == body

    @patch("deep_research.concise_mode._write_conclusion")
    @patch("deep_research.concise_mode.ResearchAgent")
    def test_complex_tag_triggers_conclusion_pass_and_keeps_report(self, mock_agent_cls, mock_write_conclusion):
        report = "Model A costs $10/month according to https://a.example/pricing. Model B costs $15/month according to https://b.example/pricing."
        mock_agent_cls.return_value.run.return_value = _fake_agent_result(
            f"{COMPLEX_TAG}\n\n{report}", sources=["https://a.example/pricing", "https://b.example/pricing"]
        )
        mock_write_conclusion.return_value = (
            "Model A is cheaper than Model B (A costs $10/month vs B's $15/month, per https://a.example/pricing "
            "and https://b.example/pricing)."
        )
        result = answer_concisely("how does Model A's pricing compare to Model B's")
        assert result["category"] == "complex"
        assert result["research_report"] == report
        assert "cheaper" in result["answer"]
        assert result["sources"] == ["https://a.example/pricing", "https://b.example/pricing"]
        mock_write_conclusion.assert_called_once()

    @patch("deep_research.concise_mode.ResearchAgent")
    def test_missing_tag_falls_back_to_simple_instead_of_crashing(self, mock_agent_cls):
        mock_agent_cls.return_value.run.return_value = _fake_agent_result(
            "The model forgot to tag this, but here's an answer anyway."
        )
        result = answer_concisely("some question")
        assert result["category"] == "simple"
        assert "forgot to tag" in result["answer"]

    @patch("deep_research.concise_mode.ResearchAgent")
    def test_sources_and_flagged_claims_always_present(self, mock_agent_cls):
        mock_agent_cls.return_value.run.return_value = _fake_agent_result(f"{SIMPLE_TAG}\n\nAnswer text.")
        result = answer_concisely("q")
        assert isinstance(result["sources"], list)
        assert isinstance(result["flagged_claims"], list)


_REQUIRES_LIVE_KEY = pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set - skipping live LLM calls"
)


@_REQUIRES_LIVE_KEY
class TestLiveQueryTypes:
    """Real calls against the real agent, one per query type this mode is designed to
    handle differently. Assertions are structural (category, presence of fields, no
    dangling citation markers) - not a judgment on prose quality, which still needs a
    human reading the actual answer the way eval_cases.py's cases do."""

    def test_simple_factual_question(self):
        result = answer_concisely("What year was the Eiffel Tower completed?")
        assert result["category"] == "simple"
        assert result["answer"]

    def test_ambiguous_word_with_context(self):
        result = answer_concisely(
            "What is lean? Context: I'm reading a tutorial about writing formally verified "
            "mathematical proofs using tactics and the mathlib library."
        )
        assert result["category"] == "ambiguous"
        answer_lower = result["answer"].lower()
        # Should surface at least the unrelated meanings, not just silently answer as if
        # only one meaning existed.
        assert any(word in answer_lower for word in ("proof", "theorem", "language"))

    def test_complex_comparison_question(self):
        result = answer_concisely(
            "How does DeepSeek's pricing compare to OpenAI's GPT-5.5 pricing, per token?"
        )
        assert result["category"] == "complex"
        assert result.get("research_report")
        assert result["sources"], "a genuinely complex/comparison query should retrieve real sources"
        assert not _BRACKET_CITATION_RE.search(result["answer"]), (
            "answer should cite with real URLs, not dangling [1]/[2] markers with nothing to resolve them against"
        )
