"""
slack_notifier.py
Formats and posts Kubernetes pod alerts / recoveries to a Slack channel
using the Slack Block Kit layout for clean, readable messages.
"""

import logging
from datetime import datetime, timezone

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

log = logging.getLogger("kube-bot.slack")


class SlackNotifier:
    """Sends formatted alert and recovery messages to Slack."""

    def __init__(self, token: str, default_channel: str) -> None:
        self._client = WebClient(token=token)
        self._default_channel = default_channel
        log.info("Slack notifier ready | default_channel=%s", default_channel)

    # ── Public methods ──────────────────────────────────────────────────────

    def send_alert(
        self,
        namespace: str,
        pod_name: str,
        issues: list,
        suggestions: dict,
        pod=None,
        channel: str = "",
        restart_count: int = 0,
    ) -> None:
        """Post an alert message for a pod that has one or more issues."""
        blocks = self._build_alert_blocks(
            namespace, pod_name, issues, suggestions, pod, restart_count
        )
        self._post(
            channel=channel or self._default_channel,
            blocks=blocks,
            fallback_text=f"🚨 Pod alert: {namespace}/{pod_name} | {', '.join(issues)}",
        )

    def send_recovery(self, namespace: str, pod_name: str, channel: str = "") -> None:
        """Post a recovery message when a pod returns to a healthy state."""
        blocks = self._build_recovery_blocks(namespace, pod_name)
        self._post(
            channel=channel or self._default_channel,
            blocks=blocks,
            fallback_text=f"✅ Pod recovered: {namespace}/{pod_name}",
        )

    def send_escalation_warning(
        self,
        namespace: str,
        pod_name: str,
        restart_count: int,
        channel: str = "",
    ) -> None:
        """
        Post an early-warning prediction alert when a pod's restart count is
        growing rapidly – before it officially enters CrashLoopBackOff.
        """
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "⚠️  Escalating Restarts – Predicted Failure"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Namespace*\n`{namespace}`"},
                    {"type": "mrkdwn", "text": f"*Pod*\n`{pod_name}`"},
                    {"type": "mrkdwn", "text": f"*Total Restarts*\n`{restart_count}`"},
                    {"type": "mrkdwn", "text": "*Prediction*\nLikely to enter CrashLoopBackOff soon"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*💡 Suggested actions:*\n"
                        f"• Check current logs: `kubectl logs {pod_name} -n {namespace}`\n"
                        f"• Watch live: `kubectl logs -f {pod_name} -n {namespace}`\n"
                        f"• Describe pod: `kubectl describe pod {pod_name} -n {namespace}`\n"
                        "• Act now to prevent a full outage"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"⏱ Predicted at {_utc_now()}"}],
            },
        ]
        self._post(
            channel=channel or self._default_channel,
            blocks=blocks,
            fallback_text=f"⚠️ Escalating restarts predicted for {namespace}/{pod_name} (restarts={restart_count})",
        )

    def send_spread_warning(
        self,
        namespace: str,
        issue: str,
        affected_pods: list,
        channel: str = "",
    ) -> None:
        """
        Post a namespace-wide warning when the same issue hits multiple pods.
        This usually means a shared config, secret, or dependency is broken.
        """
        pod_list = "\n".join(f"• `{p}`" for p in affected_pods[:10])
        suffix = f"\n• … and {len(affected_pods) - 10} more" if len(affected_pods) > 10 else ""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🔥  Namespace-Wide Issue Detected"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Namespace*\n`{namespace}`"},
                    {"type": "mrkdwn", "text": f"*Issue*\n`{issue}`"},
                    {"type": "mrkdwn", "text": f"*Pods Affected*\n`{len(affected_pods)}`"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Affected pods:*\n{pod_list}{suffix}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*💡 This looks like a shared dependency failure:*\n"
                        f"• Check shared ConfigMaps/Secrets in `{namespace}`\n"
                        f"• Check namespace-level network policies: `kubectl get networkpolicies -n {namespace}`\n"
                        f"• Check recent deployments: `kubectl get events -n {namespace} --sort-by=.lastTimestamp`\n"
                        "• Check if an upstream service (DB, API gateway) is down"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"🕐 Detected at {_utc_now()}"}],
            },
        ]
        self._post(
            channel=channel or self._default_channel,
            blocks=blocks,
            fallback_text=f"🔥 Namespace-wide {issue} in {namespace}: {len(affected_pods)} pods affected",
        )

    # ── Block builders ──────────────────────────────────────────────────────

    def _build_alert_blocks(
        self, namespace: str, pod_name: str, issues: list, suggestions: dict, pod, restart_count: int = 0
    ) -> list:
        now = _utc_now()
        issue_str = "  ".join(f"`{i}`" for i in issues)
        owner = _get_owner(pod) if pod else None

        blocks = [
            # ── Header ─────────────────────────────────────────────────────
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🚨  Pod Issue Detected"},
            },
            # ── Pod details ─────────────────────────────────────────────────
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Namespace*\n`{namespace}`"},
                    {"type": "mrkdwn", "text": f"*Pod*\n`{pod_name}`"},
                    {"type": "mrkdwn", "text": f"*Issue(s)*\n{issue_str}"},
                    *(
                        [{"type": "mrkdwn", "text": f"*Owner*\n`{owner}`"}]
                        if owner
                        else []
                    ),
                    *(
                        [{"type": "mrkdwn", "text": f"*Restarts*\n`{restart_count}`"}]
                        if restart_count > 0
                        else []
                    ),
                ],
            },
            {"type": "divider"},
        ]

        # ── One suggestion block per issue ──────────────────────────────────
        for issue, tips in suggestions.items():
            if tips:
                bullet_list = "\n".join(f"• {tip}" for tip in tips)
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*💡 Fixes for `{issue}`:*\n{bullet_list}",
                        },
                    }
                )

        # ── Footer ──────────────────────────────────────────────────────────
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"🕐 Detected at {now}"},
                ],
            }
        )
        return blocks

    def _build_recovery_blocks(self, namespace: str, pod_name: str) -> list:
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "✅  Pod Recovered"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Pod* `{pod_name}` in namespace `{namespace}` "
                        "is now *running healthy* 🎉"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"🕐 Recovered at {_utc_now()}"},
                ],
            },
        ]

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _post(self, blocks: list, fallback_text: str, channel: str = "") -> None:
        target = channel or self._default_channel
        try:
            self._client.chat_postMessage(
                channel=target,
                blocks=blocks,
                text=fallback_text,   # shown in push notifications / unfurls
            )
        except SlackApiError as exc:
            log.error("Slack API error: %s", exc.response.get("error", exc))
        except Exception as exc:
            log.error("Failed to post Slack message: %s", exc)


# ── Module-level helpers ────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _get_owner(pod) -> str | None:
    """Return 'Kind/name' for the first owner reference (e.g. ReplicaSet/my-app-abc)."""
    refs = getattr(pod.metadata, "owner_references", None)
    if not refs:
        return None
    o = refs[0]
    return f"{o.kind}/{o.name}"
