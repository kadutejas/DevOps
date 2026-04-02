"""Abstract LLM provider interface.

WHY an abstraction: The advisory service must work with a local Ollama
instance by default (privacy-first) but optionally delegate to cloud LLMs
for higher-quality analysis.  The interface is intentionally thin — a single
`analyse` method — so providers are trivial to implement and swap.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod

from k8s_advisor.models import DiagnosticContext, LLMAdvice

logger = logging.getLogger(__name__)

# ── System prompt shared by all providers ──────────────────────

SYSTEM_PROMPT = """\
You are an expert Kubernetes operations engineer embedded in a monitoring pipeline.
You receive structured diagnostic context about a pod issue and must respond with:

1. **Root Cause**: A clear, concise explanation of why the issue is happening.
2. **Remediation Steps**: An ordered list of concrete actions the operator should take.
3. **Confidence**: "high", "medium", or "low" — how certain you are about the diagnosis.

Respond ONLY in the following JSON format (no markdown fences, no extra text):
{
  "root_cause": "...",
  "remediation_steps": ["step 1", "step 2", ...],
  "confidence": "high|medium|low"
}

Rules:
- Be specific and actionable.  Reference exact resource names from the context.
- If the evidence is insufficient, say so and suggest what to investigate.
- Never suggest applying changes automatically — this is an advisory service.
- Keep each remediation step to one sentence.
"""


class LLMProvider(ABC):
    """Interface for LLM providers."""

    @abstractmethod
    def analyse(self, context: DiagnosticContext) -> LLMAdvice:
        """Send diagnostic context to the LLM and parse the response."""

    def _build_user_prompt(self, context: DiagnosticContext) -> str:
        return (
            "Analyse the following Kubernetes pod issue and provide your diagnosis.\n\n"
            + context.to_prompt_text()
        )

    def _parse_response(self, raw: str) -> LLMAdvice:
        """Best-effort JSON parse of the LLM response.

        WHY best-effort: LLMs sometimes wrap output in markdown fences or
        add commentary.  We strip fences and attempt JSON parse; if that
        fails we return the raw text as the root cause so the operator
        still gets *something* useful.
        """
        cleaned = raw.strip()
        # Strip markdown code fences if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            data = json.loads(cleaned)
            return LLMAdvice(
                root_cause=data.get("root_cause", raw),
                remediation_steps=data.get("remediation_steps", []),
                confidence=data.get("confidence", "medium"),
                raw_response=raw,
            )
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse LLM JSON response; using raw text")
            return LLMAdvice(
                root_cause=raw,
                remediation_steps=["Unable to parse structured response — review raw output"],
                confidence="low",
                raw_response=raw,
            )
