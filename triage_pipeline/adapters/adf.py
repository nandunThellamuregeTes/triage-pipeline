"""Atlassian Document Format (ADF) -> lightweight Markdown conversion.

Used by JiraRestAdapter. Converting to a single space-joined line would
destroy repro steps and stack traces, so this preserves paragraphs, lists,
fenced code blocks, and inline code marks as real Markdown structure.
"""
from __future__ import annotations


def adf_to_markdown(node: dict | None) -> str:
    if not node:
        return ""
    if isinstance(node, str):
        return node
    return _render_node(node, list_depth=0).strip("\n")


def _render_children(node: dict, list_depth: int) -> str:
    return "".join(_render_node(child, list_depth) for child in node.get("content", []) or [])


def _render_text(node: dict) -> str:
    text = node.get("text", "")
    for mark in node.get("marks", []) or []:
        mark_type = mark.get("type")
        if mark_type == "code":
            text = f"`{text}`"
        elif mark_type == "strong":
            text = f"**{text}**"
        elif mark_type == "em":
            text = f"*{text}*"
        elif mark_type == "link":
            href = mark.get("attrs", {}).get("href", "")
            text = f"[{text}]({href})"
    return text


def _render_node(node: dict, list_depth: int) -> str:
    node_type = node.get("type")

    if node_type == "text":
        return _render_text(node)
    if node_type == "paragraph":
        return _render_children(node, list_depth) + "\n\n"
    if node_type == "heading":
        level = node.get("attrs", {}).get("level", 1)
        return "#" * level + " " + _render_children(node, list_depth) + "\n\n"
    if node_type == "hardBreak":
        return "\n"
    if node_type == "bulletList":
        items = []
        for item in node.get("content", []) or []:
            inner = _render_children(item, list_depth + 1).strip("\n")
            items.append("  " * list_depth + f"- {inner}")
        return "\n".join(items) + "\n\n"
    if node_type == "orderedList":
        items = []
        for i, item in enumerate(node.get("content", []) or [], start=1):
            inner = _render_children(item, list_depth + 1).strip("\n")
            items.append("  " * list_depth + f"{i}. {inner}")
        return "\n".join(items) + "\n\n"
    if node_type == "listItem":
        return _render_children(node, list_depth)
    if node_type == "codeBlock":
        language = node.get("attrs", {}).get("language", "")
        code = "".join(c.get("text", "") for c in node.get("content", []) or [])
        return f"```{language}\n{code}\n```\n\n"
    if node_type == "blockquote":
        inner = _render_children(node, list_depth).strip("\n")
        quoted = "\n".join(f"> {line}" for line in inner.splitlines())
        return quoted + "\n\n"
    if node_type == "rule":
        return "---\n\n"
    if node_type == "mention":
        return f"@{node.get('attrs', {}).get('text', node.get('attrs', {}).get('id', ''))}"
    if node_type == "panel":
        inner = _render_children(node, list_depth).strip("\n")
        return f"> **Note:** {inner}\n\n"
    # Unknown node type: descend into children if present, else drop silently.
    return _render_children(node, list_depth)
