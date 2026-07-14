"""Realtime adapter helpers (no live network)."""

from __future__ import annotations

from aegis.voice.realtime import _usage_from_response


def test_usage_from_response_nested() -> None:
    msg = {
        "type": "response.done",
        "response": {
            "usage": {
                "input_token_details": {
                    "audio_tokens": 11,
                    "text_tokens": 3,
                    "cached_tokens": 2,
                },
                "output_token_details": {"audio_tokens": 22, "text_tokens": 4},
            }
        },
    }
    u = _usage_from_response(msg)
    assert u is not None
    assert u.input_audio_tokens == 11
    assert u.output_audio_tokens == 22
    assert u.cached_input_tokens == 2
