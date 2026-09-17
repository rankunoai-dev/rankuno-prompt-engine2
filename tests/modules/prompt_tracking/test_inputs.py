"""Tests for prompt-list parsing."""

from __future__ import annotations

from src.modules.prompt_tracking.inputs import parse_prompt_lines, read_prompts_file

TEXT = """
# analyst prompts
What is the best procurement software?
How does GEP compare to Coupa? | procurement software | Vendor Comparison
  what is the best procurement software?
Is GEP SMART worth it | | Pricing
ab
Bare | source to pay software
"""


def test_parses_fields_and_dedupes_case_insensitively():
    prompts = parse_prompt_lines(TEXT)
    assert [p.prompt_text for p in prompts] == [
        "What is the best procurement software?",
        "How does GEP compare to Coupa?",
        "Is GEP SMART worth it",
        "Bare",
    ]
    assert prompts[0].keyword is None and prompts[0].subtopic is None
    assert prompts[1].keyword == "procurement software"
    assert prompts[1].subtopic == "Vendor Comparison"
    assert prompts[2].keyword is None
    assert prompts[2].subtopic == "Pricing"
    assert prompts[3].keyword == "source to pay software"


def test_blank_and_comment_lines_and_too_short_prompts_are_ignored():
    assert parse_prompt_lines("\n# only a comment\n\n  \nab\n") == []


def test_read_prompts_file(tmp_path):
    path = tmp_path / "prompts.txt"
    path.write_text("One prompt here\nAnother prompt | kw\n", encoding="utf-8")
    prompts = read_prompts_file(path)
    assert len(prompts) == 2
    assert prompts[1].keyword == "kw"
