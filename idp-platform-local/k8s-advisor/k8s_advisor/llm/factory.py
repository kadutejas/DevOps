"""LLM provider factory.

WHY a factory: Decouples the main loop from concrete provider classes.
Adding a new provider requires only a new module and a single entry here.
"""

from __future__ import annotations

import logging

from k8s_advisor.config import Config
from k8s_advisor.llm.base import LLMProvider
from k8s_advisor.llm.ollama import OllamaProvider
from k8s_advisor.llm.openai_provider import OpenAIProvider
from k8s_advisor.llm.claude import ClaudeProvider

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
}


def create_provider(cfg: Config) -> LLMProvider:
    """Instantiate the configured LLM provider."""
    provider_name = cfg.llm_provider
    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        supported = ", ".join(sorted(_PROVIDERS))
        raise ValueError(
            f"Unknown LLM provider '{provider_name}'. Supported: {supported}"
        )
    logger.info("Using LLM provider: %s", provider_name)
    return cls(cfg)
