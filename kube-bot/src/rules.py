"""
rules.py
Loads fix suggestions from config/rules.yaml (mounted via Kubernetes ConfigMap).
Edit rules.yaml to add your own custom suggestions – no code changes required.
"""

import logging
import yaml
from pathlib import Path

log = logging.getLogger("kube-bot.rules")

# Used when no rule is defined for a given issue type
_DEFAULT_SUGGESTIONS = [
    "Check pod logs:   kubectl logs {pod_name} -n {namespace} --previous",
    "Describe the pod: kubectl describe pod {pod_name} -n {namespace}",
    "Recent events:    kubectl get events -n {namespace} --sort-by=.lastTimestamp",
]


class RulesEngine:
    """
    Reads rules.yaml and returns suggestions for each Kubernetes issue type.

    Usage in rules.yaml:
        rules:
          CrashLoopBackOff:
            severity: critical
            suggestions:
              - "Your custom suggestion (use {pod_name} and {namespace} as placeholders)"
    """

    def __init__(self, rules_path: str) -> None:
        self._path = Path(rules_path)
        self._rules: dict = {}
        self._load()

    # ── Public API ──────────────────────────────────────────────────────────

    def get_suggestions(self, issue_type: str, pod_name: str, namespace: str) -> list:
        """
        Return the list of suggestions for `issue_type` with placeholders filled in.
        Falls back to default suggestions when no specific rule is found.
        """
        rule = self._find_rule(issue_type)
        raw = rule.get("suggestions") if rule else None
        if not raw:
            raw = _DEFAULT_SUGGESTIONS

        return [s.format(pod_name=pod_name, namespace=namespace) for s in raw]

    def get_severity(self, issue_type: str) -> str:
        rule = self._find_rule(issue_type)
        return (rule.get("severity") if rule else None) or "warning"

    def reload(self) -> None:
        """Hot-reload rules without restarting the bot."""
        self._load()

    # ── Internals ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            log.warning("Rules file not found at %s – using defaults only.", self._path)
            self._rules = {}
            return
        try:
            with open(self._path) as fh:
                data = yaml.safe_load(fh) or {}
            self._rules = data.get("rules", {})
            log.info("Loaded %d rules from %s", len(self._rules), self._path)
        except Exception as exc:
            log.error("Failed to load rules from %s: %s", self._path, exc)
            self._rules = {}

    def _find_rule(self, issue_type: str) -> dict:
        """Case-sensitive lookup first, then case-insensitive fallback."""
        rule = self._rules.get(issue_type)
        if rule:
            return rule
        lower = issue_type.lower()
        for key, val in self._rules.items():
            if key.lower() == lower:
                return val
        return {}
