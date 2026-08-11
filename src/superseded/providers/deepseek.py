from __future__ import annotations

from superseded.providers.openai_compat import OpenAICompatProvider

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_API_KEY_ENV = "SUPERSEDED_DEEPSEEK_API_KEY"


class DeepSeekProvider(OpenAICompatProvider):
    """Direct DeepSeek Chat Completions client (OpenAI-compatible)."""

    name = "deepseek"
    api_key_env = DEEPSEEK_API_KEY_ENV
    base_url = DEEPSEEK_DEFAULT_BASE_URL
    default_model = DEEPSEEK_DEFAULT_MODEL
    effort_key = "deepseek"
