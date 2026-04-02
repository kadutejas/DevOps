"""Slack notification sender.

Supports two delivery modes:
  1. Incoming Webhook URL — simplest setup, no bot token needed.
  2. Bot Token + channel — more flexible, supports threading.

WHY both: Webhooks are zero-config for teams that just want alerts.
Bot tokens are needed for interactive features or posting to multiple channels.
"""

from __future__ import annotations

import logging

import httpx

from k8s_advisor.config import Config
from k8s_advisor.models import DiagnosticContext, LLMAdvice
from k8s_advisor.notifier.formatter import format_slack_message

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Delivers formatted advisory messages to Slack."""

    def __init__(self, cfg: Config) -> None:
        self._webhook_url = cfg.slack_webhook_url
        self._bot_token = cfg.slack_bot_token
        self._channel = cfg.slack_channel
        self._timeout = 15

        if not self._webhook_url and not self._bot_token:
            logger.warning(
                "Neither SLACK_WEBHOOK_URL nor SLACK_BOT_TOKEN configured — "
                "Slack notifications will be logged only"
            )

    @property
    def enabled(self) -> bool:
        return bool(self._webhook_url or self._bot_token)

    def notify(self, context: DiagnosticContext, advice: LLMAdvice) -> bool:
        """Send a Slack notification.  Returns True on success."""
        payload = format_slack_message(context, advice)

        if not self.enabled:
            logger.info(
                "Slack not configured — would notify: %s/%s %s",
                context.issue.namespace,
                context.issue.pod_name,
                context.issue.issue_type.value,
            )
            return False

        try:
            if self._webhook_url:
                return self._send_via_webhook(payload)
            else:
                return self._send_via_bot(payload)
        except Exception:
            logger.exception("Failed to send Slack notification")
            return False

    # ── delivery methods ───────────────────────────────────────

    def _send_via_webhook(self, payload: dict) -> bool:
        resp = httpx.post(self._webhook_url, json=payload, timeout=self._timeout)
        if resp.status_code == 200:
            logger.info("Slack webhook notification sent")
            return True
        logger.error("Slack webhook returned %s: %s", resp.status_code, resp.text)
        return False

    def _send_via_bot(self, payload: dict) -> bool:
        payload["channel"] = self._channel
        resp = httpx.post(
            "https://slack.com/api/chat.postMessage",
            json=payload,
            headers={"Authorization": f"Bearer {self._bot_token}"},
            timeout=self._timeout,
        )
        data = resp.json()
        if data.get("ok"):
            logger.info("Slack bot notification sent to %s", self._channel)
            return True
        logger.error("Slack bot API error: %s", data.get("error", "unknown"))
        return False
