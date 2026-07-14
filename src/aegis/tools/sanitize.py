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

_OPEN = "<untrusted_tool_output>"
_CLOSE = "</untrusted_tool_output>"


def strip_control_sequences(text: str) -> str:
    """Remove ANSI escapes and non-printable control characters."""
    text = _ANSI_RE.sub("", text)
    return _CONTROL_RE.sub("", text)


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
    between these markers as data, never as instructions to follow. We also strip
    any literal occurrences of the delimiters from the content so injected text
    can't forge a closing marker and "escape" the wrapper.
    """
    cleaned = sanitize_tool_output(text, max_bytes=max_bytes)
    cleaned = cleaned.replace(_OPEN, "<untrusted_tool_output_>").replace(
        _CLOSE, "</untrusted_tool_output_>"
    )
    return f"{_OPEN}\n{cleaned}\n{_CLOSE}"
