"""Real eval cases drawn directly from the TechDrishti handoff's failing-case
catalog (prev_project_context/deep_research_agent_handoff.md). These are a
quality eval, not a pass/fail assert suite - judging whether a research report
correctly decomposed a question or avoided a hallucinated comparison requires
reading the output, the same way TechDrishti's own fixes were verified against
real live articles rather than synthetic fixtures.
"""

EVAL_CASES = [
    {
        "id": "interpretive_judgment",
        "question": (
            "What is the strategic significance of Z.ai's open-source model release given "
            "the US restrictions on Anthropic models?"
        ),
        "expect": (
            "Should decompose into fact sub-questions (what did Z.ai release, what are the "
            "US restrictions) and synthesize the judgment itself, not search for the "
            "judgment directly."
        ),
    },
    {
        "id": "genuine_comparison",
        "question": "How does GLM-5.2 compare to GPT-5.5 in architecture and pricing?",
        "expect": (
            "Should fetch facts about each model independently, then compare - not invent "
            "unstated specs for either side."
        ),
    },
    {
        "id": "no_real_comparison_target",
        "question": "How does the Leanstral 1.5 model compare to existing coding models?",
        "expect": (
            "Should search for real named competitors rather than inventing one (must not "
            "invent e.g. 'GPT-4 Turbo' without retrieved evidence it's the relevant "
            "comparator)."
        ),
    },
    {
        "id": "cross_domain_risk",
        "question": (
            "Compare a new large language model's benchmark scores to a vision-language "
            "model's benchmark scores in the same report."
        ),
        "expect": (
            "Should recognize these are different categories and either decline the direct "
            "comparison or explicitly caveat the category mismatch."
        ),
    },
    {
        "id": "thin_coverage",
        "question": (
            "What are the technical specifications of the XR-9000 quantum chip announced "
            "yesterday?"
        ),
        "expect": (
            "A deliberately fictional product - should report insufficient information "
            "found rather than hallucinating specs."
        ),
    },
]
