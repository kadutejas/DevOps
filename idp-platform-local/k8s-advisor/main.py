"""
k8s-advisor — main entry point.

Runs a continuous poll loop:
  1. Fetch a snapshot of the target namespace (pods, events, deployments)
  2. Detect issues with the pattern-match detectors
  3. Gather diagnostic context for each new issue
  4. Ask the LLM for root-cause analysis
  5. Post an actionable Slack message if the issue is new / cooldown expired
  6. Sleep for POLL_INTERVAL_SECONDS, then repeat

All configuration is loaded from environment variables (see config.py).
"""

import logging
import os
import signal
import sys
import time

from k8s_advisor.config import Config
from k8s_advisor.detectors import detect_issues
from k8s_advisor.diagnostics import DiagnosticsGatherer
from k8s_advisor.llm.factory import create_llm_provider
from k8s_advisor.notifier.slack import SlackNotifier
from k8s_advisor.state import StateTracker
from k8s_advisor.watcher import KubeWatcher

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger("k8s-advisor")

_running = True


def _shutdown(sig, _frame):
    global _running
    log.info("Shutdown signal received – stopping advisor.")
    _running = False


def main() -> None:
    cfg = Config()

    log.info(
        "k8s-advisor starting | namespace=%s | llm=%s | interval=%ds",
        cfg.target_namespace,
        cfg.llm_provider,
        cfg.poll_interval_seconds,
    )

    watcher = KubeWatcher(cfg)
    state = StateTracker(cfg)
    notifier = SlackNotifier(cfg)

    try:
        llm = create_llm_provider(cfg)
    except Exception as exc:
        log.error("Failed to initialise LLM provider: %s", exc)
        sys.exit(1)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while _running:
        try:
            snapshot = watcher.get_snapshot()
            issues = detect_issues(snapshot)
            log.debug("Poll complete: %d issue(s) detected", len(issues))

            gatherer = DiagnosticsGatherer(watcher, snapshot)

            for issue in issues:
                if not state.should_alert(issue):
                    log.debug("Suppressing duplicate alert: %s", issue.fingerprint)
                    continue

                try:
                    context = gatherer.gather(issue)
                    advice = llm.analyse(context)
                    notifier.send(context, advice)
                    state.record_alert(issue)
                    log.info(
                        "Alert sent: %s/%s [%s]",
                        issue.namespace,
                        issue.pod_name,
                        issue.issue_type.value,
                    )
                except Exception as exc:
                    log.error(
                        "Failed to process issue %s: %s",
                        issue.fingerprint,
                        exc,
                    )

        except Exception as exc:
            log.error("Poll loop error: %s", exc)

        if _running:
            time.sleep(cfg.poll_interval_seconds)

    log.info("k8s-advisor stopped.")


if __name__ == "__main__":
    main()
