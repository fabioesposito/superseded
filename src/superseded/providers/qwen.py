from __future__ import annotations

from superseded.providers.openai_compat import OpenAICompatProvider

QWEN_DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
QWEN_DEFAULT_MODEL = "qwen3.8-max"
QWEN_API_KEY_ENV = "SUPERSEDED_QWEN_API_KEY"


class QwenProvider(OpenAICompatProvider):
    """Qwen via DashScope's OpenAI-compatible endpoint."""

    name = "qwen"
    api_key_env = QWEN_API_KEY_ENV
    base_url = QWEN_DEFAULT_BASE_URL
    default_model = QWEN_DEFAULT_MODEL
    effort_key = "qwen"
