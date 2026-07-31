"""Sanitize and demarcate untrusted tool output before it reaches the model.

Tool results (shell stdout, file contents, MCP responses, web content) are
untrusted: they can carry prompt-injection payloads that try to steer the model
into calling dangerous tools. We can't prevent a model from reading the content,
but we can (a) strip control/ANSI escapes that hide instructions or spoof output,
(b) cap size so a hostile server can't flood the context, and (c) wrap the content
in explicit delimiters the system prompt tells the model never to treat as
instructions.
"""

from __future__ import annotations

import re

# C0/C1 control chars except tab/newline/carriage-return, plus the ESC that starts
# ANSI/OSC sequences. Stripped so escape codes can't hide or spoof content.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# ANSI CSI / OSC escape sequences.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# Zero-width and bidirectional-control characters. These are invisible but are
# real characters to a tokenizer, so they let injected text smuggle a delimiter
# past a literal string comparison (``<​/untrusted_tool_output>``) or
# reorder how a line renders to a human reviewing it.
_INVISIBLE_RE = re.compile(
    "[­​-‏‪-‮⁠-⁤⁦-⁩﻿]"
)

_OPEN = "<untrusted_tool_output>"
_CLOSE = "</untrusted_tool_output>"
# Any spelling of the fence, in either direction. A literal str.replace only
# caught the exact lowercase form, so `</UNTRUSTED_TOOL_OUTPUT>` and
# `</untrusted_tool_output >` survived and could convince the model the
# untrusted region had ended.
_FENCE_RE = re.compile(r"<\s*/?\s*untrusted_tool_output\s*>", re.IGNORECASE)
_FENCE_PLACEHOLDER = "[fence-token-removed]"

# Escaped forms for characters that must stay visible but must not be able to
# restructure a terminal line or a prompt the operator reads.
_LINE_BREAKERS = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def strip_control_sequences(text: str) -> str:
    """Remove ANSI escapes, control characters, and invisible/bidi characters."""
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    return _INVISIBLE_RE.sub("", text)


def escape_line_breaks(text: str) -> str:
    """Render newlines/CR/tab visibly so a value cannot forge terminal lines.

    Used for anything interpolated into an operator-facing prompt: a model can
    choose these bytes, and a bare ``\\r`` rewrites the line the human is
    reading.
    """
    for raw, escaped in _LINE_BREAKERS.items():
        text = text.replace(raw, escaped)
    return text


def neutralize_fence_tokens(text: str) -> str:
    """Replace anything that could read as an untrusted-output delimiter."""
    return _FENCE_RE.sub(_FENCE_PLACEHOLDER, text)


def sanitize_tool_output(text: str, *, max_bytes: int = 100_000) -> str:
    """Strip control sequences and cap size (by encoded bytes)."""
    text = strip_control_sequences(text)
    raw = text.encode("utf-8", errors="replace")
    if len(raw) > max_bytes:
        text = raw[: max_bytes - 32].decode("utf-8", errors="replace") + "\n…[truncated]"
    return text


def wrap_untrusted(text: str, *, max_bytes: int = 100_000) -> str:
    """Sanitize and wrap tool output in untrusted-content delimiters.

    The model is instructed (see the session system prompt) to treat anything
    between these markers as data, never as instructions to follow. Anything in
    the content that could read as a delimiter is neutralized first, so injected
    text cannot forge a closing marker and "escape" the wrapper.

    Wrapping is always the last step: truncating afterwards would cut off the
    closing delimiter. Use :func:`truncate_preserving_fence` to shorten an
    already-wrapped payload.
    """
    cleaned = sanitize_tool_output(text, max_bytes=max_bytes)
    cleaned = neutralize_fence_tokens(cleaned)
    return f"{_OPEN}\n{cleaned}\n{_CLOSE}"


def is_wrapped(text: str) -> bool:
    return text.startswith(_OPEN) and text.rstrip().endswith(_CLOSE)


def truncate_preserving_fence(text: str, max_chars: int) -> str:
    """Shorten text without ever losing the closing delimiter.

    Callers that cap an already-wrapped payload (the chat provider path) would
    otherwise amputate ``</untrusted_tool_output>`` for exactly the largest —
    and therefore most suspicious — outputs.
    """
    if len(text) <= max_chars:
        return text
    if not is_wrapped(text):
        return text[:max_chars]
    marker = "\n…[truncated]\n"
    overhead = len(_OPEN) + len(_CLOSE) + len(marker)
    body = text[len(_OPEN) : text.rstrip().rfind(_CLOSE)]
    keep = max(0, max_chars - overhead)
    return f"{_OPEN}{body[:keep]}{marker}{_CLOSE}"
