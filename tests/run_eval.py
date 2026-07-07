"""Runs the eval cases live against the real agent and prints each report for
manual inspection. Requires ANTHROPIC_API_KEY to be set (see .env.example).

    python tests/run_eval.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deep_research.agent import ResearchAgent  # noqa: E402
from eval_cases import EVAL_CASES  # noqa: E402


def main():
    agent = ResearchAgent()
    for case in EVAL_CASES:
        print(f"\n=== {case['id']} ===")
        print(f"Question: {case['question']}")
        print(f"Expected behavior: {case['expect']}")
        result = agent.run(case["question"])
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
