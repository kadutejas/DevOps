"""Kubernetes resource polling.

WHY polling instead of watch: Watch streams are elegant but fragile — a single
network hiccup drops the connection and requires bookmark-based resumption logic.
Polling every N seconds is simpler, predictable, and sufficient for an advisory
service where sub-second latency is not required.  If real-time detection becomes
critical, this module can be upgraded to watch+bookmark without touching callers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from kubernetes import client, config as k8s_config
from kubernetes.client import (
    CoreV1Api,
    AppsV1Api,
    V1Pod,
    V1Event,
    V1Deployment,
    V1ReplicaSet,
)
from kubernetes.client.rest import ApiException

from k8s_advisor.config import Config

logger = logging.getLogger(__name__)


@dataclass
class NamespaceSnapshot:
    """Point-in-time snapshot of resources in the target namespace."""

    pods: list[V1Pod] = field(default_factory=list)
    events: list[V1Event] = field(default_factory=list)
    deployments: list[V1Deployment] = field(default_factory=list)
    replica_sets: list[V1ReplicaSet] = field(default_factory=list)


class KubeWatcher:
    """Fetches read-only snapshots of namespace resources from the K8s API."""

    def __init__(self, cfg: Config) -> None:
        self._namespace = cfg.target_namespace

        # WHY try in-cluster first: When running as a pod the service-account
        # token is mounted automatically.  Fall back to kubeconfig for local
        # development (e.g. Kind, Minikube).
        try:
            k8s_config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
            logger.info("Loaded kubeconfig (local dev mode)")

        self._core = CoreV1Api()
        self._apps = AppsV1Api()

    def snapshot(self) -> NamespaceSnapshot:
        """Fetch all monitored resources — one snapshot per poll cycle."""
        snap = NamespaceSnapshot()

        snap.pods = self._list_pods()
        snap.events = self._list_events()
        snap.deployments = self._list_deployments()
        snap.replica_sets = self._list_replica_sets()

        logger.debug(
            "Snapshot: %d pods, %d events, %d deploys, %d rs",
            len(snap.pods),
            len(snap.events),
            len(snap.deployments),
            len(snap.replica_sets),
        )
        return snap

    # ── internal helpers ───────────────────────────────────────

    def _list_pods(self) -> list[V1Pod]:
        try:
            resp = self._core.list_namespaced_pod(self._namespace)
            return resp.items or []
        except ApiException as exc:
            logger.error("Failed to list pods: %s", exc.reason)
            return []

    def _list_events(self) -> list[V1Event]:
        try:
            resp = self._core.list_namespaced_event(self._namespace)
            return resp.items or []
        except ApiException as exc:
            logger.error("Failed to list events: %s", exc.reason)
            return []

    def _list_deployments(self) -> list[V1Deployment]:
        try:
            resp = self._apps.list_namespaced_deployment(self._namespace)
            return resp.items or []
        except ApiException as exc:
            logger.error("Failed to list deployments: %s", exc.reason)
            return []

    def _list_replica_sets(self) -> list[V1ReplicaSet]:
        try:
            resp = self._apps.list_namespaced_replica_set(self._namespace)
            return resp.items or []
        except ApiException as exc:
            logger.error("Failed to list replicasets: %s", exc.reason)
            return []

    def get_events_for_pod(self, pod_name: str) -> list[V1Event]:
        """Retrieve events referencing a specific pod (field selector)."""
        try:
            resp = self._core.list_namespaced_event(
                self._namespace,
                field_selector=f"involvedObject.name={pod_name}",
            )
            return resp.items or []
        except ApiException as exc:
            logger.error("Failed to get events for pod %s: %s", pod_name, exc.reason)
            return []
