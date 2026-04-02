"""
history_tracker.py
Tracks Kubernetes pod/deployment event history in memory.

Two things this module does:
  1. TRACKS restart counts and issue timestamps per pod so we can detect
     escalating failure patterns.
  2. PREDICTS likely failures early – before a pod enters CrashLoopBackOff –
     by watching restart count growth rate and deployment rollout failures.

All data is in-memory (no external DB needed).  It resets if the bot restarts,
which is fine – the watcher re-scans on startup anyway.

HOW PREDICTIONS WORK
─────────────────────
• If a pod's restart count grows by RESTART_RATE_THRESHOLD in RATE_WINDOW_SECONDS,
  it's flagged as "escalating" even before it officially enters CrashLoopBackOff.
• If the SAME issue appears in ≥ SPREAD_THRESHOLD pods in the same namespace,
  it's flagged as a "cluster-wide" problem in that namespace.
• Predictions are sent as a warning alert (not a full alert) so you have a
  heads-up before the pager goes off at 3 AM.
"""

import time
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

log = logging.getLogger("kube-bot.history")

# ── Tunable thresholds ────────────────────────────────────────────────────────
# Restart growth: if a pod adds this many restarts within RATE_WINDOW_SECONDS,
# it's considered escalating.
RESTART_RATE_THRESHOLD = 3
RATE_WINDOW_SECONDS = 300  # 5 minutes

# Spread: if this many pods in the same namespace share the same issue,
# emit a namespace-wide warning.
SPREAD_THRESHOLD = 3

# How long (seconds) to keep per-restart timestamps in the ring buffer
MAX_TIMESTAMP_AGE = 3600  # 1 hour


@dataclass
class PodHistory:
    """All historical data we track for one pod."""

    # Timestamps of each restart captured (capped ring buffer)
    restart_timestamps: Deque[float] = field(default_factory=lambda: deque(maxlen=50))

    # Latest known restart count from the K8s API
    last_restart_count: int = 0

    # Current active issues (set of reason strings e.g. {"CrashLoopBackOff"})
    active_issues: set = field(default_factory=set)

    # Whether we have already sent an "escalating" prediction alert
    escalation_alerted: bool = False

    # Time when this pod was first seen with issues
    first_issue_time: Optional[float] = None


class HistoryTracker:
    """
    Tracks restart counts and issue history for all pods across all namespaces.
    Call record_pod_state() on every K8s event; query predict_*() for warnings.
    """

    def __init__(self) -> None:
        # "namespace/pod-name" → PodHistory
        self._pods: Dict[str, PodHistory] = {}

    # ── Main entry point ────────────────────────────────────────────────────

    def record_pod_state(
        self,
        namespace: str,
        pod_name: str,
        restart_count: int,
        issues: List[str],
    ) -> None:
        """
        Called every time we see a pod event.  Updates internal history.
        """
        key = _key(namespace, pod_name)
        history = self._pods.setdefault(key, PodHistory())
        now = time.monotonic()

        # Record restart count growth
        if restart_count > history.last_restart_count:
            added = restart_count - history.last_restart_count
            for _ in range(added):
                history.restart_timestamps.append(now)
            history.last_restart_count = restart_count

        # Track active issues
        history.active_issues = set(issues)
        if issues and history.first_issue_time is None:
            history.first_issue_time = now

        # Clear escalation flag when pod recovers
        if not issues:
            history.escalation_alerted = False
            history.first_issue_time = None

    def clear(self, namespace: str, pod_name: str) -> None:
        """Remove all history for a deleted pod."""
        self._pods.pop(_key(namespace, pod_name), None)

    # ── Prediction queries ──────────────────────────────────────────────────

    def is_restart_escalating(self, namespace: str, pod_name: str) -> bool:
        """
        Returns True if this pod's restart count is growing quickly
        AND we haven't already sent an escalation warning for it.
        Useful for catching problems BEFORE CrashLoopBackOff appears.
        """
        key = _key(namespace, pod_name)
        history = self._pods.get(key)
        if not history or history.escalation_alerted:
            return False

        now = time.monotonic()
        window_start = now - RATE_WINDOW_SECONDS
        recent = sum(1 for t in history.restart_timestamps if t >= window_start)

        if recent >= RESTART_RATE_THRESHOLD:
            history.escalation_alerted = True
            log.info(
                "Escalation predicted for %s/%s: %d restarts in last %ds",
                namespace,
                pod_name,
                recent,
                RATE_WINDOW_SECONDS,
            )
            return True
        return False

    def get_restart_count(self, namespace: str, pod_name: str) -> int:
        """Return the last known restart count for a pod."""
        history = self._pods.get(_key(namespace, pod_name))
        return history.last_restart_count if history else 0

    def get_spread_warning(self, namespace: str, issue: str) -> Optional[int]:
        """
        Returns the number of pods affected if SPREAD_THRESHOLD or more pods
        in the same namespace share `issue`.  Returns None otherwise.

        Use this to detect a namespace-wide outage vs. a single broken pod.
        """
        count = 0
        for key, history in self._pods.items():
            if key.startswith(f"{namespace}/") and issue in history.active_issues:
                count += 1
        if count >= SPREAD_THRESHOLD:
            return count
        return None

    def get_affected_pods_in_namespace(self, namespace: str, issue: str) -> List[str]:
        """Return list of pod names in `namespace` that have `issue` active."""
        pods = []
        for key, history in self._pods.items():
            ns, name = key.split("/", 1)
            if ns == namespace and issue in history.active_issues:
                pods.append(name)
        return pods

    def get_summary(self) -> dict:
        """
        Returns a summary dict useful for a /status CLI command or health endpoint.
        """
        summary: dict = defaultdict(lambda: {"pods": 0, "issues": 0})
        for key, history in self._pods.items():
            ns, _ = key.split("/", 1)
            summary[ns]["pods"] += 1
            if history.active_issues:
                summary[ns]["issues"] += 1
        return dict(summary)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _key(namespace: str, pod_name: str) -> str:
    return f"{namespace}/{pod_name}"
