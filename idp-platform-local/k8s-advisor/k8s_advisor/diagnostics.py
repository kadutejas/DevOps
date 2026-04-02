"""Diagnostic context assembler.

After a detector flags an issue, this module gathers supporting evidence
from the cluster so the LLM gets a complete picture — not just the symptom
but the spec, events, and ownership chain.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from kubernetes.client import V1Pod, V1Event

from k8s_advisor.config import Config
from k8s_advisor.models import DiagnosticContext, PodIssue
from k8s_advisor.watcher import KubeWatcher, NamespaceSnapshot

logger = logging.getLogger(__name__)


class DiagnosticsGatherer:
    """Assembles a DiagnosticContext for each detected issue."""

    def __init__(self, watcher: KubeWatcher, snapshot: NamespaceSnapshot) -> None:
        self._watcher = watcher
        self._snapshot = snapshot
        # Index pods by name for O(1) lookup
        self._pods_by_name: dict[str, V1Pod] = {
            p.metadata.name: p for p in snapshot.pods
        }

    def gather(self, issue: PodIssue) -> DiagnosticContext:
        pod = self._pods_by_name.get(issue.pod_name)
        if pod is None:
            logger.warning("Pod %s no longer exists in snapshot", issue.pod_name)
            return DiagnosticContext(issue=issue)

        return DiagnosticContext(
            issue=issue,
            pod_status_summary=self._summarise_status(pod),
            recent_events=self._recent_events(issue.pod_name),
            owner_reference=self._resolve_owner(pod),
            container_specs=self._extract_container_specs(pod),
        )

    # ── internal helpers ───────────────────────────────────────

    def _summarise_status(self, pod: V1Pod) -> dict[str, Any]:
        status = pod.status
        summary: dict[str, Any] = {
            "phase": status.phase,
            "reason": status.reason,
            "message": status.message,
            "host_ip": status.host_ip,
            "pod_ip": status.pod_ip,
            "start_time": str(status.start_time) if status.start_time else None,
        }
        # Per-container status
        containers: list[dict] = []
        for cs in (status.container_statuses or []):
            entry: dict[str, Any] = {
                "name": cs.name,
                "ready": cs.ready,
                "restart_count": cs.restart_count,
                "started": cs.started,
            }
            if cs.state:
                if cs.state.waiting:
                    entry["state"] = f"Waiting: {cs.state.waiting.reason}"
                elif cs.state.running:
                    entry["state"] = f"Running since {cs.state.running.started_at}"
                elif cs.state.terminated:
                    entry["state"] = (
                        f"Terminated: {cs.state.terminated.reason} "
                        f"(exit {cs.state.terminated.exit_code})"
                    )
            containers.append(entry)
        summary["containers"] = containers
        return summary

    def _recent_events(self, pod_name: str) -> list[dict[str, Any]]:
        """Get events for this pod, sorted most-recent-first."""
        events = self._watcher.get_events_for_pod(pod_name)
        # Sort by last_timestamp descending (None sorts last)
        events.sort(
            key=lambda e: e.last_timestamp or e.first_timestamp or "",
            reverse=True,
        )
        result: list[dict[str, Any]] = []
        for ev in events[:10]:
            result.append({
                "reason": ev.reason,
                "message": ev.message,
                "type": ev.type,
                "count": ev.count,
                "first_timestamp": str(ev.first_timestamp) if ev.first_timestamp else None,
                "last_timestamp": str(ev.last_timestamp) if ev.last_timestamp else None,
                "source": ev.source.component if ev.source else None,
            })
        return result

    def _resolve_owner(self, pod: V1Pod) -> Optional[dict[str, Any]]:
        """Walk owner references to the highest-level controller we track."""
        refs = pod.metadata.owner_references
        if not refs:
            return None

        # WHY only first owner ref: Kubernetes pods almost always have a single
        # owner (ReplicaSet, StatefulSet, Job, etc.).
        ref = refs[0]
        owner: dict[str, Any] = {
            "kind": ref.kind,
            "name": ref.name,
            "api_version": ref.api_version,
        }

        # If the owner is a ReplicaSet, look up its Deployment parent
        if ref.kind == "ReplicaSet":
            for rs in self._snapshot.replica_sets:
                if rs.metadata.name == ref.name:
                    rs_refs = rs.metadata.owner_references or []
                    if rs_refs:
                        owner["parent"] = {
                            "kind": rs_refs[0].kind,
                            "name": rs_refs[0].name,
                        }
                    owner["replicas_desired"] = rs.spec.replicas
                    owner["replicas_ready"] = (
                        rs.status.ready_replicas if rs.status else None
                    )
                    break

        return owner

    def _extract_container_specs(self, pod: V1Pod) -> list[dict[str, Any]]:
        """Extract resource requests/limits and image info per container."""
        specs: list[dict[str, Any]] = []
        for container in pod.spec.containers:
            entry: dict[str, Any] = {
                "name": container.name,
                "image": container.image,
            }
            if container.resources:
                entry["resources"] = {
                    "requests": (
                        dict(container.resources.requests)
                        if container.resources.requests
                        else None
                    ),
                    "limits": (
                        dict(container.resources.limits)
                        if container.resources.limits
                        else None
                    ),
                }
            if container.liveness_probe:
                entry["liveness_probe"] = True
            if container.readiness_probe:
                entry["readiness_probe"] = True
            specs.append(entry)
        return specs
