"""Centralised configuration loaded from environment variables.

WHY env vars: The twelve-factor app pattern lets us inject config through
Kubernetes ConfigMaps/Secrets without rebuilding the image.  Every setting
has a safe default so the service starts in a reasonable state on a fresh
cluster with Ollama available.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    # ── Kubernetes ──────────────────────────────────────────────
    target_namespace: str = field(
        default_factory=lambda: os.getenv("TARGET_NAMESPACE", "default")
    )
    poll_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
    )

    # ── LLM Provider ───────────────────────────────────────────
    llm_provider: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "ollama").lower()
    )

    # Ollama (default — runs in-cluster, no data leaves the network)
    ollama_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_URL", "http://ollama.ollama:11434")
    )
    ollama_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3")
    )

    # OpenAI
    openai_api_key: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", "")
    )
    openai_model: str = field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4")
    )

    # Anthropic / Claude
    anthropic_api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "")
    )
    anthropic_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    )

    # ── Slack ──────────────────────────────────────────────────
    slack_webhook_url: str = field(
        default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL", "")
    )
    slack_bot_token: str = field(
        default_factory=lambda: os.getenv("SLACK_BOT_TOKEN", "")
    )
    slack_channel: str = field(
        default_factory=lambda: os.getenv("SLACK_CHANNEL", "#k8s-alerts")
    )

    # ── Alert behaviour ────────────────────────────────────────
    alert_cooldown_minutes: int = field(
        default_factory=lambda: int(os.getenv("ALERT_COOLDOWN_MINUTES", "30"))
    )

    # ── Logging ────────────────────────────────────────────────
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper()
    )

    # ── LLM request budget ─────────────────────────────────────
    llm_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    )
