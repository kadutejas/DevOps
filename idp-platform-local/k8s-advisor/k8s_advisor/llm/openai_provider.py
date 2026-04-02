"""OpenAI LLM provider.

Supports any OpenAI-compatible API (including Azure OpenAI) via the
standard chat completions endpoint.
"""

from __future__ import annotations

import logging

import httpx

from k8s_advisor.config import Config
from k8s_advisor.models import DiagnosticContext, LLMAdvice
from k8s_advisor.llm.base import LLMProvider, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(LLMProvider):
    """Calls the OpenAI Chat Completions API."""

    def __init__(self, cfg: Config) -> None:
        if not cfg.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for the openai provider")
        self._api_key = cfg.openai_api_key
        self._model = cfg.openai_model
        self._timeout = cfg.llm_timeout_seconds

    def analyse(self, context: DiagnosticContext) -> LLMAdvice:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._build_user_prompt(context)},
            ],
        }

        try:
            resp = httpx.post(
                _OPENAI_CHAT_URL,
                json=payload,
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
            return self._parse_response(raw)
        except httpx.HTTPStatusError as exc:
            logger.error("OpenAI HTTP error %s: %s", exc.response.status_code, exc)
            return LLMAdvice(
                root_cause=f"OpenAI returned HTTP {exc.response.status_code}",
                remediation_steps=["Check API key validity", "Review rate limits"],
                confidence="low",
            )
        except (httpx.RequestError, KeyError, IndexError) as exc:
            logger.error("OpenAI request failed: %s", exc)
            return LLMAdvice(
                root_cause=f"OpenAI request failed: {exc}",
                remediation_steps=["Check network connectivity to api.openai.com"],
                confidence="low",
            )
