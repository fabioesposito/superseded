from __future__ import annotations

import pytest

from superseded.providers.anthropic import (
    ANTHROPIC_API_KEY_ENV,
    ANTHROPIC_DEFAULT_MODEL,
    ANTHROPIC_MAX_TOKENS,
    AnthropicProvider,
)
from superseded.providers.base import Provider, ProviderConfigError, ProviderResponse
from superseded.providers.deepseek import (
    DEEPSEEK_API_KEY_ENV,
    DEEPSEEK_DEFAULT_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    DeepSeekProvider,
)
from superseded.providers.openai import (
    OPENAI_API_KEY_ENV,
    OPENAI_DEFAULT_BASE_URL,
    OPENAI_DEFAULT_MODEL,
    OpenAIProvider,
)
from superseded.providers.openai_compat import EFFORT_MAP, OpenAICompatProvider
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
    # Truncated/garbled array — no parseable array present, return [].
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


def test_parse_findings_json_returns_first_of_two_arrays():
    """`[A] garbage [B]` must yield A, not [] and not B."""
    raw = (
        '[{"severity": "critical", "file": "a.py", "line": 1}] '
        "garbage between "
        '[{"severity": "nit", "file": "b.py", "line": 2}]'
    )
    items = parse_findings_json(raw, "security")
    assert len(items) == 1
    assert items[0]["file"] == "a.py"


def test_parse_findings_json_recovers_valid_array_after_garbage():
    """`[garbage [valid]` — the greedy regex spans the garbage; the balanced scan recovers."""
    raw = '[not valid json [{"severity": "critical", "file": "a.py", "line": 1}] trailing'
    items = parse_findings_json(raw, "correctness")
    assert len(items) == 1
    assert items[0]["file"] == "a.py"


def test_parse_findings_json_closing_brace_in_string_does_not_truncate():
    r"""A `}]` substring inside a string value must not truncate the array."""
    raw = (
        "preamble\n"
        '[{"severity": "critical", "file": "a.py", "line": 1, '
        '"title": "beware }] injection", "suggestion": "s"}]\n'
        "trailing commentary\n"
    )
    items = parse_findings_json(raw, "security")
    assert len(items) == 1
    assert items[0]["title"] == "beware }] injection"
    assert items[0]["file"] == "a.py"


def test_parse_findings_json_no_catastrophic_backtracking():
    """Greedy .* under DOTALL must not cause catastrophic backtracking on big inputs."""
    import time

    large = "[{" + "x" * 50_000 + "}] not json"
    start = time.time()
    items = parse_findings_json(large, "security")
    elapsed = time.time() - start
    assert items == []
    assert elapsed < 1.0, f"parse_findings_json took {elapsed:.2f}s on 50KB input"


def test_parse_findings_json_caps_at_max_per_pass():
    from superseded.providers.parsing import MAX_FINDINGS_PER_PASS

    raw = (
        "["
        + ",".join(
            f'{{"severity": "nit", "file": "f.py", "line": {i}}}'
            for i in range(MAX_FINDINGS_PER_PASS + 50)
        )
        + "]"
    )
    items = parse_findings_json(raw, "style")
    assert len(items) == MAX_FINDINGS_PER_PASS


def _fake_completion(
    *, content="[]", prompt_tokens=10, completion_tokens=5, model="deepseek-v4-flash"
):
    """Build an object that quacks like openai's ChatCompletion."""
    message = type("Msg", (), {"content": content, "reasoning_content": None})()
    choice = type("Choice", (), {"message": message})()
    usage = type(
        "Usage", (), {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    )()
    return type(
        "Resp",
        (),
        {"choices": [choice], "usage": usage, "model": model},
    )()


def test_deepseek_constants():
    assert DEEPSEEK_API_KEY_ENV == "SUPERSEDED_DEEPSEEK_API_KEY"
    assert DEEPSEEK_DEFAULT_BASE_URL == "https://api.deepseek.com"
    assert DEEPSEEK_DEFAULT_MODEL == "deepseek-v4-flash"


def test_deepseek_provider_name():
    p = DeepSeekProvider(api_key="sk-test")
    assert p.name == "deepseek"


def test_deepseek_provider_uses_env_when_no_arg(monkeypatch):
    monkeypatch.setenv(DEEPSEEK_API_KEY_ENV, "sk-from-env")
    p = DeepSeekProvider()
    assert p.name == "deepseek"  # construction succeeded


def test_deepseek_provider_raises_when_no_key(monkeypatch):
    monkeypatch.delenv(DEEPSEEK_API_KEY_ENV, raising=False)
    with pytest.raises(ProviderConfigError, match="No deepseek API key"):
        DeepSeekProvider()


def test_deepseek_complete_returns_content(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            captured["init_kwargs"] = kw

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            captured["create_kwargs"] = kw
            return _fake_completion(
                content='[{"severity": "critical"}]', prompt_tokens=42, completion_tokens=7
            )

    monkeypatch.setattr("superseded.providers.openai_compat.OpenAI", FakeClient)
    p = DeepSeekProvider(api_key="sk-test")
    resp = p.complete(
        "the prompt", model="deepseek-v4-flash", timeout=120.0, reasoning_effort="max"
    )
    assert resp.content == '[{"severity": "critical"}]'
    assert resp.prompt_tokens == 42
    assert resp.completion_tokens == 7
    assert resp.model == "deepseek-v4-flash"
    # The prompt was forwarded as the user message.
    assert captured["create_kwargs"]["messages"] == [{"role": "user", "content": "the prompt"}]
    assert captured["create_kwargs"]["timeout"] == 120.0
    assert captured["create_kwargs"]["model"] == "deepseek-v4-flash"
    assert captured["create_kwargs"]["reasoning_effort"] == "max"


def test_deepseek_complete_omits_reasoning_effort_when_none(monkeypatch):
    """When reasoning_effort is None, the kwarg is not sent to the API."""
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            captured.update(kw)
            return _fake_completion()

    monkeypatch.setattr("superseded.providers.openai_compat.OpenAI", FakeClient)
    p = DeepSeekProvider(api_key="sk-test")
    p.complete("p", reasoning_effort=None)
    assert "reasoning_effort" not in captured


def test_deepseek_complete_uses_default_model_when_none(monkeypatch):
    class FakeClient:
        def __init__(self, **kw):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            assert kw["model"] == DEEPSEEK_DEFAULT_MODEL
            return _fake_completion()

    monkeypatch.setattr("superseded.providers.openai_compat.OpenAI", FakeClient)
    p = DeepSeekProvider(api_key="sk-test")
    p.complete("p")


def test_deepseek_complete_ignores_reasoning_content(monkeypatch):
    """Reasoner models populate both .reasoning_content and .content; we use .content only."""

    class FakeClient:
        def __init__(self, **kw):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            message = type(
                "Msg",
                (),
                {"content": "[]", "reasoning_content": "let me think..."},
            )()
            choice = type("Choice", (), {"message": message})()
            usage = type("Usage", (), {"prompt_tokens": 1, "completion_tokens": 1})()
            return type("Resp", (), {"choices": [choice], "usage": usage, "model": "x"})

    monkeypatch.setattr("superseded.providers.openai_compat.OpenAI", FakeClient)
    p = DeepSeekProvider(api_key="sk-test")
    resp = p.complete("p")
    assert resp.content == "[]"


def test_deepseek_complete_handles_null_content(monkeypatch):
    """A refusal may return content=None; provider should normalise to empty string."""

    class FakeClient:
        def __init__(self, **kw):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            message = type("Msg", (), {"content": None, "reasoning_content": None})()
            choice = type("Choice", (), {"message": message})()
            usage = type("Usage", (), {"prompt_tokens": 0, "completion_tokens": 0})()
            return type("Resp", (), {"choices": [choice], "usage": usage, "model": "x"})

    monkeypatch.setattr("superseded.providers.openai_compat.OpenAI", FakeClient)
    p = DeepSeekProvider(api_key="sk-test")
    resp = p.complete("p")
    assert resp.content == ""


def test_deepseek_init_forwards_base_url_and_retries(monkeypatch):
    """The OpenAI client must be configured with max_retries and the DeepSeek base_url."""
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("superseded.providers.openai_compat.OpenAI", FakeClient)
    DeepSeekProvider(api_key="sk-test")
    assert captured["base_url"] == DEEPSEEK_DEFAULT_BASE_URL
    assert captured["max_retries"] == 2
    assert captured["api_key"] == "sk-test"


def test_provider_map_exports():
    from superseded.providers import (  # noqa: F401
        PROVIDER_MAP,
        DeepSeekProvider,
        Provider,
        ProviderConfigError,
        ProviderResponse,
    )

    assert "deepseek" in PROVIDER_MAP
    assert PROVIDER_MAP["deepseek"] is DeepSeekProvider


def test_effort_map_deepseek():
    assert EFFORT_MAP["deepseek"] == {"low": "low", "medium": "high", "high": "high", "max": "max"}


def test_effort_map_openai():
    assert EFFORT_MAP["openai"] == {"low": "low", "medium": "medium", "high": "high", "max": "max"}


def test_effort_map_anthropic():
    assert EFFORT_MAP["anthropic"] == {
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "xhigh",
    }


def test_openai_compat_provider_requires_configured_subclass():
    """The base must refuse instantiation without subclass class-attributes."""
    with pytest.raises((TypeError, ProviderConfigError)):
        OpenAICompatProvider()


def _fake_responses(*, text="[]", input_tokens=11, output_tokens=6, model="gpt-5.6-terra"):
    """Quacks like an openai Responses API response."""
    usage = type("Usage", (), {"input_tokens": input_tokens, "output_tokens": output_tokens})()
    return type("Resp", (), {"output_text": text, "usage": usage, "model": model})()


def test_openai_constants():
    assert OPENAI_API_KEY_ENV == "SUPERSEDED_OPENAI_API_KEY"
    assert OPENAI_DEFAULT_BASE_URL == "https://api.openai.com/v1"
    assert OPENAI_DEFAULT_MODEL == "gpt-5.6-terra"


def test_openai_provider_name():
    p = OpenAIProvider(api_key="sk-test")
    assert p.name == "openai"


def test_openai_provider_raises_when_no_key(monkeypatch):
    monkeypatch.delenv(OPENAI_API_KEY_ENV, raising=False)
    with pytest.raises(ProviderConfigError, match="No openai API key"):
        OpenAIProvider()


def test_openai_complete_uses_responses_api(monkeypatch):
    """OpenAIProvider must call responses.create (not chat.completions)."""
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            captured["init_kwargs"] = kw

        @property
        def responses(self):
            return self

        def create(self, **kw):
            captured["create_kwargs"] = kw
            return _fake_responses(
                text='[{"severity": "critical"}]', input_tokens=42, output_tokens=7
            )

    monkeypatch.setattr("superseded.providers.openai_compat.OpenAI", FakeClient)
    p = OpenAIProvider(api_key="sk-test")
    resp = p.complete("the prompt", reasoning_effort="max")
    assert resp.content == '[{"severity": "critical"}]'
    assert resp.prompt_tokens == 42
    assert resp.completion_tokens == 7
    # Responses API: single user message translated to `input` (a string).
    assert captured["create_kwargs"]["input"] == "the prompt"
    assert "messages" not in captured["create_kwargs"]
    assert captured["create_kwargs"]["reasoning_effort"] == "max"


def test_openai_complete_maps_effort(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            pass

        @property
        def responses(self):
            return self

        def create(self, **kw):
            captured.update(kw)
            return _fake_responses()

    monkeypatch.setattr("superseded.providers.openai_compat.OpenAI", FakeClient)
    p = OpenAIProvider(api_key="sk-test")
    p.complete("p", reasoning_effort="medium")
    assert captured["reasoning_effort"] == "medium"
    p.complete("p", reasoning_effort="max")
    assert captured["reasoning_effort"] == "max"


def _fake_messages(*, blocks=("[]",), input_tokens=13, output_tokens=8, model="claude-sonnet-5"):
    """Quacks like an anthropic Messages API response."""
    content = []
    for b in blocks:
        content.append(type("Block", (), {"type": "text", "text": b})())
    usage = type("Usage", (), {"input_tokens": input_tokens, "output_tokens": output_tokens})()
    return type("Resp", (), {"content": content, "usage": usage, "model": model})()


def test_anthropic_constants():
    assert ANTHROPIC_API_KEY_ENV == "SUPERSEDED_ANTHROPIC_API_KEY"
    assert ANTHROPIC_DEFAULT_MODEL == "claude-sonnet-5"
    assert ANTHROPIC_MAX_TOKENS == 128_000


def test_anthropic_provider_name():
    p = AnthropicProvider(api_key="sk-test")
    assert p.name == "anthropic"


def test_anthropic_provider_raises_when_no_key(monkeypatch):
    monkeypatch.delenv(ANTHROPIC_API_KEY_ENV, raising=False)
    with pytest.raises(ProviderConfigError, match="No anthropic API key"):
        AnthropicProvider()


def test_anthropic_complete_uses_messages_api(monkeypatch):
    captured = {}

    class FakeMessages:
        def __init__(self, **kw):
            captured["init_kwargs"] = kw

        @property
        def messages(self):
            return self

        def create(self, **kw):
            captured["create_kwargs"] = kw
            return _fake_messages(blocks=("first ", "second"), input_tokens=5, output_tokens=3)

    monkeypatch.setattr("superseded.providers.anthropic.Anthropic", FakeMessages)
    p = AnthropicProvider(api_key="sk-test")
    resp = p.complete("the prompt", reasoning_effort="max")
    # text blocks joined
    assert resp.content == "first second"
    assert resp.prompt_tokens == 5
    assert resp.completion_tokens == 3
    assert captured["create_kwargs"]["messages"] == [{"role": "user", "content": "the prompt"}]
    assert captured["create_kwargs"]["max_tokens"] == 128_000
    # effort mapped: max -> xhigh (Anthropic vocabulary), via extra_body
    # (this SDK version's create() has no `effort` kwarg; extra_body is
    # the documented forward-compat escape hatch for API params).
    assert captured["create_kwargs"]["extra_body"] == {"effort": "xhigh"}


def test_anthropic_complete_maps_effort(monkeypatch):
    captured = {}

    class FakeMessages:
        def __init__(self, **kw):
            pass

        @property
        def messages(self):
            return self

        def create(self, **kw):
            captured.update(kw)
            return _fake_messages()

    monkeypatch.setattr("superseded.providers.anthropic.Anthropic", FakeMessages)
    p = AnthropicProvider(api_key="sk-test")
    p.complete("p", reasoning_effort="low")
    assert captured["extra_body"]["effort"] == "low"
    p.complete("p", reasoning_effort="high")
    assert captured["extra_body"]["effort"] == "high"
    p.complete("p", reasoning_effort="max")
    assert captured["extra_body"]["effort"] == "xhigh"


def test_provider_map_has_three_providers():
    from superseded.providers import PROVIDER_MAP

    assert set(PROVIDER_MAP) == {"deepseek", "openai", "anthropic"}
    assert PROVIDER_MAP["openai"] is OpenAIProvider
    assert PROVIDER_MAP["anthropic"] is AnthropicProvider
