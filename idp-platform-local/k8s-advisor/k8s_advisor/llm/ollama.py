"""Ollama LLM provider — default, privacy-preserving option.

WHY Ollama as default: It runs in-cluster (or on the same node), meaning
diagnostic data never leaves the network boundary.  This is critical for
regulated environments and aligns with the "no data exfiltration" principle.
"""

from __future__ import annotations

import logging

import httpx

from k8s_advisor.config import Config
from k8s_advisor.models import DiagnosticContext, LLMAdvice
from k8s_advisor.llm.base import LLMProvider, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    """Calls the Ollama HTTP API (/api/chat)."""

    def __init__(self, cfg: Config) -> None:
        self._url = cfg.ollama_url.rstrip("/")
        self._model = cfg.ollama_model
        self._timeout = cfg.llm_timeout_seconds

    def analyse(self, context: DiagnosticContext) -> LLMAdvice:
        endpoint = f"{self._url}/api/chat"
        payload = {
            "model": self._model,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._build_user_prompt(context)},
            ],
        }

        try:
            resp = httpx.post(endpoint, json=payload, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("message", {}).get("content", "")
            return self._parse_response(raw)
        except httpx.HTTPStatusError as exc:
            logger.error("Ollama HTTP error %s: %s", exc.response.status_code, exc)
            return _error_advice(f"Ollama returned HTTP {exc.response.status_code}")
        except httpx.RequestError as exc:
            logger.error("Ollama request failed: %s", exc)
            return _error_advice(f"Could not reach Ollama at {self._url}: {exc}")


def _error_advice(msg: str) -> LLMAdvice:
    return LLMAdvice(
        root_cause=f"LLM analysis unavailable: {msg}",
        remediation_steps=["Check Ollama deployment health", "Review k8s-advisor logs"],
        confidence="low",
    )
