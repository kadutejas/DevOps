"""Domain models.

All data exchanged between components is typed here.  Using dataclasses
(not dicts) so that every consumer has compile-time-checkable contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    """Issue severity — drives Slack message colour and triage order."""

    CRITICAL = "critical"  # Pod crash-looping, unable to serve traffic
    HIGH = "high"  # Image pull failures, OOM kills
    MEDIUM = "medium"  # Pending for extended period
    LOW = "low"  # Elevated restart count, transient events


class IssueType(str, Enum):
    """Known pod failure patterns the detectors can identify."""

    CRASH_LOOP_BACKOFF = "CrashLoopBackOff"
    IMAGE_PULL_BACKOFF = "ImagePullBackOff"
    ERR_IMAGE_PULL = "ErrImagePull"
    PENDING_SCHEDULING = "PendingScheduling"
    PENDING_RESOURCES = "PendingResources"
    REPEATED_RESTARTS = "RepeatedRestarts"
    OOM_KILLED = "OOMKilled"


@dataclass
class PodIssue:
    """A single detected problem on a specific pod."""

    pod_name: str
    namespace: str
    issue_type: IssueType
    severity: Severity
    container_name: Optional[str] = None
    message: str = ""
    restart_count: int = 0
    detected_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def fingerprint(self) -> str:
        """Stable identifier for dedup — same pod + issue = same fingerprint."""
        return f"{self.namespace}/{self.pod_name}/{self.issue_type.value}"


@dataclass
class DiagnosticContext:
    """Structured context gathered for an issue, sent to the LLM."""

    issue: PodIssue
    pod_status_summary: dict = field(default_factory=dict)
    recent_events: list[dict] = field(default_factory=list)
    owner_reference: Optional[dict] = None
    container_specs: list[dict] = field(default_factory=list)
    node_conditions: Optional[dict] = None

    def to_prompt_text(self) -> str:
        """Render the context as a human-readable prompt section."""
        lines = [
            f"Namespace: {self.issue.namespace}",
            f"Pod: {self.issue.pod_name}",
            f"Issue: {self.issue.issue_type.value}",
            f"Severity: {self.issue.severity.value}",
            f"Container: {self.issue.container_name or 'N/A'}",
            f"Restart count: {self.issue.restart_count}",
            f"Message: {self.issue.message}",
            "",
            "--- Pod Status ---",
            _format_dict(self.pod_status_summary),
            "",
            "--- Recent Events (last 10) ---",
            _format_events(self.recent_events),
            "",
            "--- Owner Controller ---",
            _format_dict(self.owner_reference) if self.owner_reference else "None",
            "",
            "--- Container Specs ---",
            _format_list_of_dicts(self.container_specs),
        ]
        return "\n".join(lines)


@dataclass
class LLMAdvice:
    """Response from the LLM provider."""

    root_cause: str
    remediation_steps: list[str]
    confidence: str = "medium"  # low / medium / high — informational only
    raw_response: str = ""


# ── helpers ────────────────────────────────────────────────────

def _format_dict(d: dict | None) -> str:
    if not d:
        return "  (empty)"
    return "\n".join(f"  {k}: {v}" for k, v in d.items())


def _format_events(events: list[dict]) -> str:
    if not events:
        return "  (no events)"
    lines: list[str] = []
    for ev in events[:10]:
        reason = ev.get("reason", "?")
        msg = ev.get("message", "")
        ts = ev.get("last_timestamp", "")
        lines.append(f"  [{ts}] {reason}: {msg}")
    return "\n".join(lines)


def _format_list_of_dicts(items: list[dict]) -> str:
    if not items:
        return "  (none)"
    parts: list[str] = []
    for item in items:
        parts.append(_format_dict(item))
    return "\n".join(parts)
