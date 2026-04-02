"""
k8s_watcher.py
Connects to the Kubernetes API, runs an initial pod scan on startup,
then streams real-time pod events and triggers Slack alerts when issues are found.
"""

import time
import logging

from kubernetes import client, config, watch

log = logging.getLogger("kube-bot.watcher")

# ── Issue classifiers ───────────────────────────────────────────────────────

# Container waiting.reason values that indicate a real problem
PROBLEM_WAITING = frozenset(
    {
        "CrashLoopBackOff",
        "ImagePullBackOff",
        "ErrImagePull",
        "CreateContainerConfigError",
        "CreateContainerError",
        "InvalidImageName",
        "ContainerCannotRun",
        "RunContainerError",
    }
)

# Container terminated.reason values that indicate a real problem
PROBLEM_TERMINATED = frozenset(
    {
        "OOMKilled",
        "Error",
        "ContainerCannotRun",
    }
)


class KubernetesWatcher:
    """
    Watches Kubernetes pod events in one or all namespaces.
    Calls notifier.send_alert() when a pod develops an issue.
    Calls notifier.send_recovery() when the pod becomes healthy again.
    Calls notifier.send_escalation_warning() when restart count grows rapidly.
    Calls notifier.send_spread_warning() when same issue hits multiple pods.
    """

    def __init__(
        self,
        namespace,
        ignored_namespaces,
        rules,
        notifier,
        alerts,
        channel_router=None,
        history_tracker=None,
    ):
        self.namespace = namespace                  # "" = all namespaces
        self.ignored_namespaces = ignored_namespaces
        self.rules = rules
        self.notifier = notifier
        self.alerts = alerts
        self.channel_router = channel_router        # ChannelRouter | None
        self.history_tracker = history_tracker      # HistoryTracker | None
        self._running = False
        self._spread_alerted: set = set()           # (namespace, issue) already warned

        # Load Kubernetes credentials ─────────────────────────────────────────
        # Inside a cluster (deployed pod):  uses the mounted ServiceAccount token.
        # Outside a cluster (local dev):    uses ~/.kube/config.
        try:
            config.load_incluster_config()
            log.info("Using in-cluster Kubernetes credentials.")
        except config.ConfigException:
            config.load_kube_config()
            log.info("Using local kubeconfig.")

        self._v1 = client.CoreV1Api()

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def run(self) -> None:
        self._running = True
        self._initial_scan()
        self._watch_loop()

    def stop(self) -> None:
        self._running = False

    # ── Initial scan ────────────────────────────────────────────────────────

    def _initial_scan(self) -> None:
        """
        List all pods once at startup and alert on any that already have issues.
        This catches problems that existed before the bot was deployed.
        """
        log.info("Running initial pod scan …")
        try:
            pods = self._list_pods()
            total = len(pods.items)
            problems = 0

            for pod in pods.items:
                if self._is_ignored(pod):
                    continue
                issues = self._analyze_pod(pod)
                if issues:
                    problems += 1
                    self._trigger_alert(pod, issues)

            log.info("Initial scan done: %d pods checked, %d with issues.", total, problems)
        except Exception as exc:
            log.error("Initial scan failed: %s", exc)

    # ── Watch loop ──────────────────────────────────────────────────────────

    def _watch_loop(self) -> None:
        """Stream pod events indefinitely, reconnecting on errors."""
        backoff = 5  # seconds
        while self._running:
            try:
                log.info("Starting pod watch stream …")
                self._stream_events()
                backoff = 5  # reset after a clean disconnect
            except Exception as exc:
                log.error("Watch stream error: %s – reconnecting in %ds.", exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)  # cap at 60 s

    def _stream_events(self) -> None:
        w = watch.Watch()

        # timeout_seconds: how long to keep a single HTTP stream open.
        # The outer _watch_loop reconnects automatically after each timeout.
        stream_kwargs = {"timeout_seconds": 300}

        if self.namespace:
            stream = w.stream(
                self._v1.list_namespaced_pod, self.namespace, **stream_kwargs
            )
        else:
            stream = w.stream(self._v1.list_pod_for_all_namespaces, **stream_kwargs)

        for event in stream:
            if not self._running:
                w.stop()
                return

            event_type: str = event["type"]
            pod = event["object"]

            if self._is_ignored(pod):
                continue

            ns = pod.metadata.namespace
            name = pod.metadata.name

            if event_type == "DELETED":
                # Pod is gone – free its state
                self.alerts.clear(ns, name)
                if self.history_tracker:
                    self.history_tracker.clear(ns, name)
                continue

            if event_type in ("ADDED", "MODIFIED"):
                issues = self._analyze_pod(pod)
                restart_count = self._get_restart_count(pod)

                # Update history tracker with current state
                if self.history_tracker:
                    self.history_tracker.record_pod_state(ns, name, restart_count, issues)

                if issues:
                    self._trigger_alert(pod, issues, restart_count)
                    self._check_escalation(ns, name, restart_count)
                    self._check_spread(ns, issues)
                else:
                    # Pod healthy – send recovery if it was previously alerting
                    if self.alerts.is_alerting(ns, name):
                        channel = self._channel_for(ns)
                        self.alerts.resolve(ns, name)
                        self.notifier.send_recovery(ns, name, channel=channel)
                        log.info("Pod recovered: %s/%s", ns, name)

    # ── Alert helper ────────────────────────────────────────────────────────

    def _trigger_alert(self, pod, issues: list, restart_count: int = 0) -> None:
        ns = pod.metadata.namespace
        name = pod.metadata.name

        if self.alerts.should_alert(ns, name, issues):
            suggestions = {
                issue: self.rules.get_suggestions(issue, name, ns)
                for issue in issues
            }
            channel = self._channel_for(ns)
            self.notifier.send_alert(
                ns, name, issues, suggestions, pod,
                channel=channel,
                restart_count=restart_count,
            )
            log.info("Alert sent: %s/%s  issues=%s  channel=%s", ns, name, issues, channel)

    def _check_escalation(self, namespace: str, pod_name: str, restart_count: int) -> None:
        """Send escalation warning if restart count is growing fast."""
        if self.history_tracker and self.history_tracker.is_restart_escalating(namespace, pod_name):
            channel = self._channel_for(namespace)
            self.notifier.send_escalation_warning(
                namespace, pod_name, restart_count, channel=channel
            )
            log.info(
                "Escalation warning sent: %s/%s restarts=%d",
                namespace, pod_name, restart_count,
            )

    def _check_spread(self, namespace: str, issues: list) -> None:
        """Send namespace-wide warning when same issue hits multiple pods."""
        if not self.history_tracker:
            return
        for issue in issues:
            spread_key = (namespace, issue)
            if spread_key in self._spread_alerted:
                continue
            count = self.history_tracker.get_spread_warning(namespace, issue)
            if count is not None:
                affected = self.history_tracker.get_affected_pods_in_namespace(namespace, issue)
                channel = self._channel_for(namespace)
                self.notifier.send_spread_warning(namespace, issue, affected, channel=channel)
                self._spread_alerted.add(spread_key)
                log.info(
                    "Spread warning sent: %s/%s – %d pods affected",
                    namespace, issue, count,
                )

    def _channel_for(self, namespace: str) -> str:
        """Return the Slack channel for a namespace (via router or default)."""
        if self.channel_router:
            return self.channel_router.get_channel(namespace)
        return ""

    def _get_restart_count(self, pod) -> int:
        """Return total restart count across all containers in the pod."""
        total = 0
        status = pod.status
        if not status:
            return total
        for cs_list in (
            status.container_statuses or [],
            status.init_container_statuses or [],
        ):
            for cs in cs_list:
                total += cs.restart_count or 0
        return total

    # ── Pod analysis ────────────────────────────────────────────────────────

    def _analyze_pod(self, pod) -> list:
        """
        Return a deduplicated list of issue-type strings for the given pod.
        Returns an empty list when the pod is healthy.
        """
        issues: list = []

        # Ignore pods that are being gracefully deleted
        if pod.metadata.deletion_timestamp:
            return issues

        status = pod.status
        if not status:
            return issues

        # ── Eviction ────────────────────────────────────────────────────────
        if status.reason == "Evicted":
            return ["Evicted"]

        # ── Pod phase ───────────────────────────────────────────────────────
        if status.phase in ("Failed", "Unknown"):
            issues.append(status.phase)

        # ── Container statuses (regular + init containers) ──────────────────
        all_statuses = []
        if status.container_statuses:
            all_statuses.extend(status.container_statuses)
        if status.init_container_statuses:
            all_statuses.extend(status.init_container_statuses)

        for cs in all_statuses:
            if not cs.state:
                continue

            waiting = cs.state.waiting
            terminated = cs.state.terminated

            if waiting and waiting.reason in PROBLEM_WAITING:
                if waiting.reason not in issues:
                    issues.append(waiting.reason)

            elif terminated and terminated.reason in PROBLEM_TERMINATED:
                if terminated.reason not in issues:
                    issues.append(terminated.reason)

        return issues

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _list_pods(self):
        if self.namespace:
            return self._v1.list_namespaced_pod(self.namespace)
        return self._v1.list_pod_for_all_namespaces()

    def _is_ignored(self, pod) -> bool:
        return pod.metadata.namespace in self.ignored_namespaces

    def reset_spread_state(self) -> None:
        """
        Clear tracked spread alerts so warnings can fire again.
        Called automatically when rules are hot-reloaded.
        """
        self._spread_alerted.clear()
