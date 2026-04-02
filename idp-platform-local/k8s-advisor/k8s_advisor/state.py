"""Issue state tracker — prevents duplicate alerts.

WHY in-memory instead of Redis/DB: This service runs as a single replica.
If the pod restarts, losing the dedup window is acceptable — it's better
to re-alert once than to add an external dependency for state persistence.
If multi-replica is needed later, swap this for a Redis-backed implementation
behind the same interface.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from threading import Lock

from k8s_advisor.config import Config
from k8s_advisor.models import PodIssue

logger = logging.getLogger(__name__)


class StateTracker:
    """Tracks which issues have been recently alerted to suppress duplicates."""

    def __init__(self, cfg: Config) -> None:
        self._cooldown = timedelta(minutes=cfg.alert_cooldown_minutes)
        # fingerprint → last alert time
        self._seen: dict[str, datetime] = {}
        self._lock = Lock()

    def should_alert(self, issue: PodIssue) -> bool:
        """Return True if this issue has NOT been alerted within the cooldown window."""
        fp = issue.fingerprint
        now = datetime.utcnow()

        with self._lock:
            last_alerted = self._seen.get(fp)
            if last_alerted and (now - last_alerted) < self._cooldown:
                logger.debug(
                    "Suppressing duplicate alert for %s (last: %s)",
                    fp,
                    last_alerted.isoformat(),
                )
                return False
            return True

    def record_alert(self, issue: PodIssue) -> None:
        """Record that we sent an alert for this issue."""
        fp = issue.fingerprint
        with self._lock:
            self._seen[fp] = datetime.utcnow()
            logger.debug("Recorded alert for %s", fp)

    def cleanup_expired(self) -> int:
        """Remove entries older than 2× cooldown to prevent unbounded growth.

        WHY 2×: Keeps a safety margin so an issue that recurs just after the
        cooldown still has its previous timestamp for logging context.
        """
        cutoff = datetime.utcnow() - (self._cooldown * 2)
        removed = 0
        with self._lock:
            expired = [fp for fp, ts in self._seen.items() if ts < cutoff]
            for fp in expired:
                del self._seen[fp]
                removed += 1
        if removed:
            logger.info("Cleaned up %d expired state entries", removed)
        return removed
