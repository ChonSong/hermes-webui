"""Session-to-Markdown renderer for wiki capture.

Renders a hermes-webui session transcript as a Markdown document suitable for
storage in a knowledge/wiki base. System messages are dropped, tool messages
are folded into collapsible ``<details>`` blocks, and user/assistant messages
are rendered as plain Markdown paragraphs.

Public entry point: render_session_markdown(session: dict) -> str
"""
from __future__ import annotations

import html
import json
from typing import Any


def _content_to_text(content: Any) -> str:
    """Flatten message content (str or multimodal list) into plain text.

    Mirrors the logic in ``session_export_html._content_to_text`` but strips
    image and unknown content types to plain text placeholders, since the
    Markdown output has no inline-image affordance worth preserving verbatim.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text" or "text" in c:
                    parts.append(str(c.get("text", "")))
                elif c.get("type") in ("image_url", "image"):
                    # Drop images — not representable in a text-only wiki page.
                    continue
                else:
                    parts.append(f"[{c.get('type', 'content')}]")
            else:
                parts.append(str(c))
        return "\n\n".join(p for p in parts if p)
    return str(content or "")


def _format_tool_call(tool_call: dict) -> str:
    """Render a single tool_call dict (id, function: {name, arguments})."""
    call_id = html.escape(str(tool_call.get("id", "unknown")))
    func = tool_call.get("function", {})
    name = html.escape(str(func.get("name", call_id)))
    arguments_raw = func.get("arguments", "")

    # Pretty-print arguments if they're valid JSON; otherwise plain text.
    if isinstance(arguments_raw, str) and arguments_raw.strip():
        try:
            parsed = json.loads(arguments_raw)
            arguments_formatted = json.dumps(parsed, indent=2)
            body = f"```json\n{html.escape(arguments_formatted)}\n```"
        except (json.JSONDecodeError, ValueError):
            body = html.escape(arguments_raw)
    else:
        body = html.escape(str(arguments_raw))

    return (
        f"<details>\n"
        f"<summary>Tool: {name}</summary>\n\n"
        f"{body}\n\n"
        f"</details>"
    )


def render_session_markdown(session: dict) -> str:
    """Render a session dict as a Markdown document.

    Input is a session dict as returned by ``redact_session_data(s.__dict__)``,
    with keys: session_id, title, model, model_provider, created_at, updated_at,
    messages (list of message dicts with role, content, timestamp, optional
    tool_calls).

    Returns a Markdown string with YAML frontmatter header.
    """
    sid = session.get("session_id", "")
    title = (session.get("title") or sid or "Untitled Session").strip()
    model = session.get("model", "")
    provider = session.get("model_provider", "")
    created_at = session.get("created_at", "")
    updated_at = session.get("updated_at", "")
    messages = session.get("messages") or []

    # YAML frontmatter
    frontmatter = (
        "---\n"
        f"session_id: {sid}\n"
        f"title: {title}\n"
        f"model: {model}\n"
        f"created_at: {created_at}\n"
        f"updated_at: {updated_at}\n"
        "source: hermes-webui\n"
        "---\n"
    )

    body_parts: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        if role == "system":
            continue

        content = m.get("content")
        tool_calls = m.get("tool_calls")

        if role == "tool":
            # Tool messages with tool_calls list.
            if isinstance(tool_calls, list) and tool_calls:
                for tc in tool_calls:
                    body_parts.append(_format_tool_call(tc))
            elif content:
                text = _content_to_text(content).strip()
                if text:
                    body_parts.append(
                        "<details>\n"
                        "<summary>Tool</summary>\n\n"
                        f"{text}\n\n"
                        "</details>"
                    )
        elif role in ("user", "assistant"):
            # Render multimodal content as plain text.
            text = _content_to_text(content).strip()
            if text:
                label = "User" if role == "user" else "Assistant"
                body_parts.append(f"**{label}:**\n\n{text}")
        else:
            # Unknown role — render if it has content.
            text = _content_to_text(content).strip()
            if text:
                body_parts.append(f"**{html.escape(role)}:**\n\n{text}")

    body = "\n---\n".join(body_parts)
    return f"{frontmatter}\n# {title}\n\n{body}" if body else f"{frontmatter}\n# {title}\n"
