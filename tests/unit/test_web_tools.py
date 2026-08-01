"""Web-search tool tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aegis.config import build_config
from aegis.config.schema import ToolsConfig
from aegis.tools.builtin.web_tools import (
    _PageTextParser,
    handle_read_web_page,
    handle_search_web,
    handle_view_web_image,
)
from aegis.tools.factory import build_registry


@pytest.mark.asyncio
async def test_search_web_extracts_results() -> None:
    response = MagicMock()
    response.read.return_value = (
        b'<a class="result__a" href="//duckduckgo.com/l/?uddg='
        b'https%3A%2F%2Fexample.com">Example</a>'
        b'<a class="result__snippet">An example result.</a>'
    )
    response.__enter__.return_value = response
    with patch("aegis.tools.builtin.web_tools.urlopen", return_value=response):
        result = await handle_search_web({"query": "example"}, tools=ToolsConfig())

    assert not result.is_error
    assert "https://example.com" in result.output
    assert "Example" in result.output
    assert "An example result." in result.output


@pytest.mark.asyncio
async def test_search_web_rejects_invalid_arguments() -> None:
    result = await handle_search_web({"query": "", "max_results": 1}, tools=ToolsConfig())
    assert result.is_error
    assert "query_required" in result.output


@pytest.mark.asyncio
async def test_read_web_page_rejects_non_https_url() -> None:
    result = await handle_read_web_page({"url": "http://example.com"}, tools=ToolsConfig())
    assert result.is_error
    assert "https_url_required" in result.output


def test_page_text_parser_ignores_active_content() -> None:
    parser = _PageTextParser()
    parser.feed("<title>Example</title><p>Visible</p><script>ignore()</script><style>x</style>")
    assert parser.title == "Example"
    assert "Visible" in parser.parts
    assert not any("ignore" in part for part in parser.parts)


@pytest.mark.asyncio
async def test_view_web_image_attaches_only_fetched_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aegis.tools.builtin.web_tools._fetch_image",
        lambda url: "data:image/png;base64,aW1hZ2U=",
    )
    result = await handle_view_web_image(
        {"url": "https://example.com/image.png"}, tools=ToolsConfig()
    )
    assert not result.is_error
    assert result.meta["image_data_url"] == "data:image/png;base64,aW1hZ2U="


def test_web_tool_is_opt_in() -> None:
    cfg = build_config({"tools": {"enabled": ["fs", "web"]}})
    assert "search_web" in build_registry(cfg).names()


@pytest.mark.asyncio
async def test_web_auto_approval_bypasses_network_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aegis.tools.builtin.web_tools._fetch_search_results",
        lambda query, max_results: [],
    )
    cfg = build_config({"tools": {"enabled": ["web"], "web": {"auto_approve": True}}})
    result = await build_registry(cfg).dispatch("search_web", {"query": "example"})
    assert not result.is_error
    assert result.decision == "auto"
