"""
kube-bot – entry point
Reads configuration from environment variables and starts the pod watcher.
"""

import os
import signal
import sys
import logging

from k8s_watcher import KubernetesWatcher
from slack_notifier import SlackNotifier
from alert_manager import AlertManager
from rules import RulesEngine
from channel_router import ChannelRouter
from history_tracker import HistoryTracker

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger("kube-bot")


def main() -> None:
    # ── Required ────────────────────────────────────────────────────────────
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    if not slack_token:
        log.error("SLACK_BOT_TOKEN environment variable is required – exiting.")
        sys.exit(1)

    # ── Optional / defaults ─────────────────────────────────────────────────
    # Default fallback channel used when namespace has no channel-map entry
    default_channel = os.environ.get("SLACK_CHANNEL", "#kubernetes-alerts")

    # Empty string  → watch every namespace (requires ClusterRole in k8s/rbac.yaml)
    # A name        → watch only that namespace (Role is enough)
    watch_namespace = os.environ.get("WATCH_NAMESPACE", "")

    # Comma-separated namespaces to skip (system namespaces by default)
    ignored_ns = set(
        filter(
            None,
            os.environ.get(
                "IGNORED_NAMESPACES", "kube-system,kube-public,kube-node-lease"
            ).split(","),
        )
    )

    # How long (seconds) to wait before re-alerting the same pod for the same issue
    cooldown = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "1800"))

    # Path where rules.yaml is mounted (via ConfigMap in k8s/configmap.yaml)
    rules_path = os.environ.get("RULES_CONFIG_PATH", "/config/rules.yaml")

    # Path where channel_map.yaml is mounted (via ConfigMap)
    channel_map_path = os.environ.get("CHANNEL_MAP_PATH", "/config/channel_map.yaml")

    # ── Wire components together ─────────────────────────────────────────────
    rules = RulesEngine(rules_path)
    notifier = SlackNotifier(token=slack_token, default_channel=default_channel)
    alerts = AlertManager(cooldown_seconds=cooldown)
    channel_router = ChannelRouter(map_path=channel_map_path)
    history = HistoryTracker()

    watcher = KubernetesWatcher(
        namespace=watch_namespace,
        ignored_namespaces=ignored_ns,
        rules=rules,
        notifier=notifier,
        alerts=alerts,
        channel_router=channel_router,
        history_tracker=history,
    )

    # ── Graceful shutdown ────────────────────────────────────────────────────
    def _shutdown(sig, _frame):
        log.info("Shutdown signal received – stopping watcher.")
        watcher.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info(
        "kube-bot started | namespace=%s | default_channel=%s | cooldown=%ds",
        watch_namespace or "ALL",
        default_channel,
        cooldown,
    )
    watcher.run()


if __name__ == "__main__":
    main()
