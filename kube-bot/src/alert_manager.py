"""
alert_manager.py
Tracks which pods are currently alerting and manages cooldown periods.
Prevents duplicate / spammy Slack notifications.
"""

import time
import logging

log = logging.getLogger("kube-bot.alerts")


class AlertManager:
    """
    Per-pod state machine:
      • should_alert()  → True when a new or changed issue needs a notification
      • is_alerting()   → True while a pod has at least one active issue
      • resolve()       → Called when a pod returns to a healthy state
      • clear()         → Called when a pod is deleted (frees memory)
    """

    def __init__(self, cooldown_seconds: int = 1800) -> None:
        self.cooldown = cooldown_seconds
        # "namespace/pod-name" → { last_alert, issues (frozenset), alerting (bool) }
        self._states: dict = {}

    def should_alert(self, namespace: str, pod_name: str, issues: list) -> bool:
        """
        Returns True (and records the alert) when:
          (a) New issue types appeared that were not in the previous alert, OR
          (b) The cooldown period expired and issues are still present.
        """
        key = _key(namespace, pod_name)
        now = time.monotonic()
        state = self._states.get(key, {})

        last_alert: float = state.get("last_alert", 0.0)
        last_issues: frozenset = frozenset(state.get("issues", []))
        current_issues: frozenset = frozenset(issues)

        new_issues = current_issues - last_issues
        cooldown_expired = (now - last_alert) >= self.cooldown

        fire = bool(new_issues) or (
            bool(current_issues) and cooldown_expired and bool(last_issues)
        )

        if fire:
            self._states[key] = {
                "last_alert": now,
                "issues": current_issues,
                "alerting": True,
            }
            log.debug("Alert fired for %s | issues=%s", key, current_issues)
        else:
            # Keep the issue set up to date even when not re-alerting
            if key in self._states:
                self._states[key]["issues"] = current_issues

        return fire

    def is_alerting(self, namespace: str, pod_name: str) -> bool:
        return self._states.get(_key(namespace, pod_name), {}).get("alerting", False)

    def resolve(self, namespace: str, pod_name: str) -> None:
        """Mark a pod as healthy (no active issues)."""
        key = _key(namespace, pod_name)
        if key in self._states:
            self._states[key]["alerting"] = False
            self._states[key]["issues"] = frozenset()
        log.debug("Resolved %s", key)

    def clear(self, namespace: str, pod_name: str) -> None:
        """Remove all state for a deleted pod (frees memory)."""
        self._states.pop(_key(namespace, pod_name), None)


def _key(namespace: str, pod_name: str) -> str:
    return f"{namespace}/{pod_name}"
