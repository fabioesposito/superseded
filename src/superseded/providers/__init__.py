from __future__ import annotations

from superseded.providers.anthropic import AnthropicProvider
from superseded.providers.base import Provider, ProviderConfigError, ProviderResponse
from superseded.providers.deepseek import DeepSeekProvider
from superseded.providers.openai import OpenAIProvider

PROVIDER_MAP: dict[str, type[Provider]] = {
    "deepseek": DeepSeekProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
}

__all__ = [
    "PROVIDER_MAP",
    "AnthropicProvider",
    "DeepSeekProvider",
    "OpenAIProvider",
    "Provider",
    "ProviderConfigError",
    "ProviderResponse",
]
