"""ADF -> Markdown conversion tests (§12)."""
from triage_pipeline.adapters.adf import adf_to_markdown


def test_paragraph_and_plain_text():
    adf = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}]}
    assert adf_to_markdown(adf).strip() == "Hello world"


def test_bullet_list_preserves_structure():
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "bulletList",
                "content": [
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "first"}]}]},
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "second"}]}]},
                ],
            }
        ],
    }
    md = adf_to_markdown(adf)
    assert "- first" in md
    assert "- second" in md
    # Not flattened to one space-joined line.
    assert md.count("\n") >= 1


def test_code_block_preserved_as_fence():
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "codeBlock",
                "attrs": {"language": "python"},
                "content": [{"type": "text", "text": "x = 1\ny = 2"}],
            }
        ],
    }
    md = adf_to_markdown(adf)
    assert "```python" in md
    assert "x = 1\ny = 2" in md


def test_inline_code_mark():
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "calculate_discount", "marks": [{"type": "code"}]}],
            }
        ],
    }
    assert "`calculate_discount`" in adf_to_markdown(adf)


def test_none_input_returns_empty_string():
    assert adf_to_markdown(None) == ""
