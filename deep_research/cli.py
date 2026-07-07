"""Command-line entry point for testing both modes.

Usage:
    python -m deep_research.cli ask "How does GLM-5.2 compare to GPT-5.5?"
    python -m deep_research.cli article "Title here" path/to/body.txt
"""
import argparse
import json
import sys

from .agent import ResearchAgent
from .article_mode import research_article
from .concise_mode import answer_concisely

# Windows consoles default stdout to the system codepage (cp1252), which can't encode
# Hindi/CJK/most non-Latin output - reconfigure explicitly rather than relying on
# PYTHONIOENCODING being set in the environment.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Deep research agent")
    sub = parser.add_subparsers(dest="mode", required=True)

    ask = sub.add_parser("ask", help="Mode 1: ask a direct research question")
    ask.add_argument("question")

    article = sub.add_parser("article", help="Mode 2: research gaps in an article")
    article.add_argument("title")
    article.add_argument("body_file", help="Path to a text file containing the article body")

    concise = sub.add_parser(
        "concise", help="Mode 3: short, fact-grounded answer (also callable by another model)"
    )
    concise.add_argument("question")

    args = parser.parse_args()

    if args.mode == "ask":
        result = ResearchAgent().run(args.question)
    elif args.mode == "concise":
        result = answer_concisely(args.question)
    else:
        with open(args.body_file, encoding="utf-8") as f:
            body = f.read()
        result = research_article(args.title, body)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
