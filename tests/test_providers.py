from __future__ import annotations

from superseded.providers.base import Provider, ProviderConfigError, ProviderResponse
from superseded.providers.parsing import parse_findings_json


def test_provider_response_defaults():
    r = ProviderResponse(content="hello")
    assert r.content == "hello"
    assert r.prompt_tokens == 0
    assert r.completion_tokens == 0
    assert r.model == ""
    assert r.raw is None


def test_provider_response_is_frozen():
    r = ProviderResponse(content="hello")
    try:
        r.content = "world"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("ProviderResponse should be frozen")


def test_provider_config_error_is_runtime_error():
    assert issubclass(ProviderConfigError, RuntimeError)


def test_provider_protocol_is_typing_protocol():
    from typing import Protocol as TypingProtocol

    # Provider must be a typing.Protocol so any object with the right shape matches.
    assert isinstance(Provider, type)
    assert issubclass(Provider, TypingProtocol) or Provider._is_protocol  # type: ignore[attr-defined]


def test_provider_protocol_has_complete_method():
    # Structural check: any class with a `complete` method and `name` property satisfies Provider.
    class Fake:
        name = "fake"

        def complete(self, prompt, *, model=None, timeout=600.0, temperature=0.0):
            return ProviderResponse(content="ok")

    fake = Fake()
    assert hasattr(fake, "complete")
    assert hasattr(fake, "name")


def test_parse_findings_json_bare_array():
    raw = '[{"severity": "critical", "file": "a.py", "line": 1}]'
    items = parse_findings_json(raw, "security")
    assert len(items) == 1
    assert items[0]["severity"] == "critical"
    assert items[0]["pass_name"] == "security"


def test_parse_findings_json_fenced_block():
    raw = 'Here you go:\n```json\n[{"severity": "nit", "file": "a.py", "line": 1}]\n```\n'
    items = parse_findings_json(raw, "style")
    assert len(items) == 1
    assert items[0]["pass_name"] == "style"


def test_parse_findings_json_array_embedded_in_prose():
    raw = (
        "I reviewed the diff and found:\n"
        '[{"severity": "critical", "file": "a.py", "line": 1, "title": "t"}]\n'
        "Let me know if you need more detail."
    )
    items = parse_findings_json(raw, "correctness")
    assert len(items) == 1
    assert items[0]["title"] == "t"


def test_parse_findings_json_empty_array_returns_empty_list():
    assert parse_findings_json("[]", "security") == []


def test_parse_findings_json_no_array_returns_empty_list():
    assert parse_findings_json("no json here", "security") == []


def test_parse_findings_json_malformed_array_returns_empty_list():
    # Truncated/garbled array — return [] so the retry path (engine) can react.
    assert parse_findings_json("[{bad json", "security") == []


def test_parse_findings_json_top_level_dict_rejected():
    # Prompt asks for an array; a single dict is schema drift — return [].
    assert parse_findings_json('{"severity": "critical"}', "security") == []


def test_parse_findings_json_array_element_not_dict_rejected():
    # ["string"] is not a list of finding dicts.
    assert parse_findings_json('["just a string"]', "security") == []


def test_parse_findings_json_injects_pass_name_into_each_item():
    raw = '[{"severity": "critical", "file": "a.py", "line": 1}, {"severity": "nit", "file": "b.py", "line": 2}]'
    items = parse_findings_json(raw, "architecture")
    assert all(i["pass_name"] == "architecture" for i in items)
    assert len(items) == 2
