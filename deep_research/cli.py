"""Command-line entry point for testing both modes.

Usage:
    python -m deep_research.cli ask "How does GLM-5.2 compare to GPT-5.5?"
    python -m deep_research.cli article "Title here" path/to/body.txt
"""
import argparse
import json

from .agent import ResearchAgent
from .article_mode import research_article


def main():
    parser = argparse.ArgumentParser(description="Deep research agent")
    sub = parser.add_subparsers(dest="mode", required=True)

    ask = sub.add_parser("ask", help="Mode 1: ask a direct research question")
    ask.add_argument("question")

    article = sub.add_parser("article", help="Mode 2: research gaps in an article")
    article.add_argument("title")
    article.add_argument("body_file", help="Path to a text file containing the article body")

    args = parser.parse_args()

    if args.mode == "ask":
        result = ResearchAgent().run(args.question)
    else:
        with open(args.body_file, encoding="utf-8") as f:
            body = f.read()
        result = research_article(args.title, body)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
