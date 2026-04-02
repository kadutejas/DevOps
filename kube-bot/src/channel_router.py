"""
channel_router.py
Maps a Kubernetes namespace to a Slack channel name.

Default behaviour  →  channel = "#<namespace>"
                       e.g.   namespace "production" → "#production"

Overrides          →  defined in config/channel_map.yaml (mounted via ConfigMap)
                       e.g.   monitoring: "#platform-monitoring"

HOW TO EDIT (no code changes needed):
  Open kube-bot/config/channel_map.yaml and add / change entries under `overrides:`.
  Then run: kubectl apply -f k8s/configmap.yaml && kubectl rollout restart deployment/kube-bot -n kube-bot
"""

import logging
from pathlib import Path

import yaml

log = logging.getLogger("kube-bot.channel_router")


class ChannelRouter:
    """
    Resolves the Slack channel for a given namespace.

    config/channel_map.yaml format:
        channel_map:
          default_pattern: "{namespace}"   # → #production, #staging, etc.
          overrides:
            some-namespace: "some-other-channel"
    """

    def __init__(self, map_path: str) -> None:
        self._path = Path(map_path)
        self._overrides: dict = {}
        self._default_pattern: str = "{namespace}"
        self._load()

    # ── Public API ──────────────────────────────────────────────────────────

    def get_channel(self, namespace: str) -> str:
        """
        Return the Slack channel name for `namespace`.

        Priority:
          1. Exact namespace match in overrides
          2. Default pattern (default: #{namespace})
        """
        if namespace in self._overrides:
            channel = self._overrides[namespace]
            log.debug("Namespace %s → override channel %s", namespace, channel)
        else:
            channel = self._default_pattern.format(namespace=namespace)
            log.debug("Namespace %s → default channel %s", namespace, channel)

        # Ensure the channel starts with "#"
        if not channel.startswith("#"):
            channel = f"#{channel}"
        return channel

    def reload(self) -> None:
        """Hot-reload channel map without restarting the bot."""
        self._load()

    # ── Internals ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            log.warning(
                "Channel map file not found at %s – using default pattern #{namespace}.",
                self._path,
            )
            self._overrides = {}
            self._default_pattern = "{namespace}"
            return

        try:
            with open(self._path) as fh:
                data = yaml.safe_load(fh) or {}
            cfg = data.get("channel_map", {})
            self._default_pattern = cfg.get("default_pattern", "{namespace}")
            self._overrides = cfg.get("overrides", {}) or {}
            log.info(
                "Loaded channel map: %d override(s) | default pattern: '%s'",
                len(self._overrides),
                self._default_pattern,
            )
        except Exception as exc:
            log.error("Failed to load channel map from %s: %s", self._path, exc)
            self._overrides = {}
            self._default_pattern = "{namespace}"
