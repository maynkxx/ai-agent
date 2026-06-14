"""LLM helpers: JSON salvage from messy replies, and provider auto-resolution."""
from __future__ import annotations

from uniagent.config import LLMSettings
from uniagent.llm import parse_json_block


def test_parse_plain_json_object():
    assert parse_json_block('{"a": 1}') == {"a": 1}


def test_parse_fenced_json():
    reply = "```json\n{\"a\": 1, \"b\": null}\n```"
    assert parse_json_block(reply) == {"a": 1, "b": None}


def test_parse_json_embedded_in_prose():
    reply = 'Sure! Here is the data:\n{"name": "MIT"}\nHope that helps.'
    assert parse_json_block(reply) == {"name": "MIT"}


def test_parse_json_list():
    assert parse_json_block("[1, 2, 3]") == [1, 2, 3]


def test_parse_unsalvageable_returns_none():
    assert parse_json_block("no json here at all") is None
    assert parse_json_block("") is None


def test_available_true_with_key():
    from uniagent.llm import LLMClient
    client = LLMClient(LLMSettings(provider="groq", groq_api_key="x"))
    assert client.available() is True


def test_available_false_without_key():
    from uniagent.llm import LLMClient
    client = LLMClient(LLMSettings(provider="groq", groq_api_key=""))
    assert client.available() is False


def test_provider_none_not_available():
    from uniagent.llm import LLMClient
    client = LLMClient(LLMSettings(provider="none", groq_api_key="x"))
    assert client.available() is False
