from __future__ import annotations

from superseded.providers.base import Provider, ProviderConfigError, ProviderResponse
from superseded.providers.deepseek import DeepSeekProvider

PROVIDER_MAP: dict[str, type[Provider]] = {
    "deepseek": DeepSeekProvider,
}

__all__ = [
    "PROVIDER_MAP",
    "DeepSeekProvider",
    "Provider",
    "ProviderConfigError",
    "ProviderResponse",
]
