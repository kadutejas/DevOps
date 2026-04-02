"""Anthropic Claude LLM provider."""

from __future__ import annotations

import logging

import httpx

from k8s_advisor.config import Config
from k8s_advisor.models import DiagnosticContext, LLMAdvice
from k8s_advisor.llm.base import LLMProvider, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


class ClaudeProvider(LLMProvider):
    """Calls the Anthropic Messages API."""

    def __init__(self, cfg: Config) -> None:
        if not cfg.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for the claude provider")
        self._api_key = cfg.anthropic_api_key
        self._model = cfg.anthropic_model
        self._timeout = cfg.llm_timeout_seconds

    def analyse(self, context: DiagnosticContext) -> LLMAdvice:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "max_tokens": 1024,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": self._build_user_prompt(context)},
            ],
        }

        try:
            resp = httpx.post(
                _ANTHROPIC_MESSAGES_URL,
                json=payload,
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            # Anthropic returns content as a list of blocks
            raw = data["content"][0]["text"]
            return self._parse_response(raw)
        except httpx.HTTPStatusError as exc:
            logger.error("Anthropic HTTP error %s: %s", exc.response.status_code, exc)
            return LLMAdvice(
                root_cause=f"Anthropic returned HTTP {exc.response.status_code}",
                remediation_steps=["Check API key validity", "Review usage limits"],
                confidence="low",
            )
        except (httpx.RequestError, KeyError, IndexError) as exc:
            logger.error("Anthropic request failed: %s", exc)
            return LLMAdvice(
                root_cause=f"Anthropic request failed: {exc}",
                remediation_steps=["Check network connectivity to api.anthropic.com"],
                confidence="low",
            )
