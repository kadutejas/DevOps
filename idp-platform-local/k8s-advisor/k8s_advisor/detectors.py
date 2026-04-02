"""Issue detection engine.

Each detector function examines a single V1Pod and returns zero or more
PodIssue objects.  The top-level `detect_issues` function runs all detectors
over every pod in a snapshot.

WHY individual functions instead of a class hierarchy: The detection rules
are pure functions — no shared state, easy to unit-test in isolation, trivial
to add new ones.  A registry pattern is used so new detectors are automatically
included by appending to `_DETECTORS`.
"""

from __future__ import annotations

import logging
from typing import Callable

from kubernetes.client import V1Pod, V1ContainerStatus

from k8s_advisor.models import IssueType, PodIssue, Severity
from k8s_advisor.watcher import NamespaceSnapshot

logger = logging.getLogger(__name__)

# Type alias for a detector function
Detector = Callable[[V1Pod], list[PodIssue]]


# ── Individual detectors ──────────────────────────────────────


def detect_crash_loop(pod: V1Pod) -> list[PodIssue]:
    """CrashLoopBackOff on any container → CRITICAL."""
    issues: list[PodIssue] = []
    for cs in _all_container_statuses(pod):
        waiting = cs.state and cs.state.waiting
        if waiting and waiting.reason == "CrashLoopBackOff":
            issues.append(
                PodIssue(
                    pod_name=pod.metadata.name,
                    namespace=pod.metadata.namespace,
                    issue_type=IssueType.CRASH_LOOP_BACKOFF,
                    severity=Severity.CRITICAL,
                    container_name=cs.name,
                    message=waiting.message or "Container is crash-looping",
                    restart_count=cs.restart_count or 0,
                )
            )
    return issues


def detect_image_pull_errors(pod: V1Pod) -> list[PodIssue]:
    """ImagePullBackOff / ErrImagePull → HIGH."""
    issues: list[PodIssue] = []
    for cs in _all_container_statuses(pod):
        waiting = cs.state and cs.state.waiting
        if not waiting:
            continue
        if waiting.reason == "ImagePullBackOff":
            issue_type = IssueType.IMAGE_PULL_BACKOFF
        elif waiting.reason == "ErrImagePull":
            issue_type = IssueType.ERR_IMAGE_PULL
        else:
            continue
        issues.append(
            PodIssue(
                pod_name=pod.metadata.name,
                namespace=pod.metadata.namespace,
                issue_type=issue_type,
                severity=Severity.HIGH,
                container_name=cs.name,
                message=waiting.message or f"Image pull failed: {waiting.reason}",
            )
        )
    return issues


def detect_pending(pod: V1Pod) -> list[PodIssue]:
    """Pod stuck in Pending phase → MEDIUM.

    WHY we check conditions: A pod can be Pending because the scheduler
    cannot place it (Unschedulable) or because there are insufficient
    resources.  The condition messages disambiguate.
    """
    if pod.status.phase != "Pending":
        return []

    issue_type = IssueType.PENDING_SCHEDULING
    severity = Severity.MEDIUM
    message = "Pod is pending"

    conditions = pod.status.conditions or []
    for cond in conditions:
        if cond.type == "PodScheduled" and cond.status == "False":
            reason = cond.reason or ""
            if "Insufficient" in (cond.message or ""):
                issue_type = IssueType.PENDING_RESOURCES
                message = cond.message
            else:
                message = cond.message or f"Unschedulable: {reason}"
            break

    return [
        PodIssue(
            pod_name=pod.metadata.name,
            namespace=pod.metadata.namespace,
            issue_type=issue_type,
            severity=severity,
            message=message,
        )
    ]


def detect_repeated_restarts(pod: V1Pod, threshold: int = 5) -> list[PodIssue]:
    """Containers with restart count above threshold → LOW/MEDIUM.

    WHY a separate detector from CrashLoop: A container may have restarted
    many times but currently be Running.  This catches the *history* of
    instability even when the pod isn't actively crash-looping right now.
    """
    issues: list[PodIssue] = []
    for cs in _all_container_statuses(pod):
        # Skip if already caught as CrashLoopBackOff
        waiting = cs.state and cs.state.waiting
        if waiting and waiting.reason == "CrashLoopBackOff":
            continue

        restarts = cs.restart_count or 0
        if restarts >= threshold:
            severity = Severity.MEDIUM if restarts >= threshold * 2 else Severity.LOW
            issues.append(
                PodIssue(
                    pod_name=pod.metadata.name,
                    namespace=pod.metadata.namespace,
                    issue_type=IssueType.REPEATED_RESTARTS,
                    severity=severity,
                    container_name=cs.name,
                    message=f"Container has restarted {restarts} times",
                    restart_count=restarts,
                )
            )
    return issues


def detect_oom_killed(pod: V1Pod) -> list[PodIssue]:
    """Last termination was OOMKilled → HIGH."""
    issues: list[PodIssue] = []
    for cs in _all_container_statuses(pod):
        last = cs.last_state and cs.last_state.terminated
        if last and last.reason == "OOMKilled":
            issues.append(
                PodIssue(
                    pod_name=pod.metadata.name,
                    namespace=pod.metadata.namespace,
                    issue_type=IssueType.OOM_KILLED,
                    severity=Severity.HIGH,
                    container_name=cs.name,
                    message=f"OOMKilled (exit code {last.exit_code})",
                    restart_count=cs.restart_count or 0,
                )
            )
    return issues


# ── Detector registry ─────────────────────────────────────────

_DETECTORS: list[Detector] = [
    detect_crash_loop,
    detect_image_pull_errors,
    detect_pending,
    detect_repeated_restarts,
    detect_oom_killed,
]


def detect_issues(snapshot: NamespaceSnapshot) -> list[PodIssue]:
    """Run every registered detector over every pod in the snapshot."""
    all_issues: list[PodIssue] = []
    for pod in snapshot.pods:
        for detector in _DETECTORS:
            try:
                issues = detector(pod)
                all_issues.extend(issues)
            except Exception:
                logger.exception(
                    "Detector %s failed on pod %s",
                    detector.__name__,
                    pod.metadata.name,
                )
    return all_issues


# ── helpers ────────────────────────────────────────────────────

def _all_container_statuses(pod: V1Pod) -> list[V1ContainerStatus]:
    """Merge container_statuses and init_container_statuses."""
    statuses: list[V1ContainerStatus] = []
    if pod.status and pod.status.container_statuses:
        statuses.extend(pod.status.container_statuses)
    if pod.status and pod.status.init_container_statuses:
        statuses.extend(pod.status.init_container_statuses)
    return statuses
