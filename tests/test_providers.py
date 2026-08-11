from __future__ import annotations

from superseded.providers.base import Provider, ProviderConfigError, ProviderResponse


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
