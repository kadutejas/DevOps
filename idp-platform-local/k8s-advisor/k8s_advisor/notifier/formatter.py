"""Slack Block Kit message formatter.

WHY Block Kit: Raw text messages are hard to scan.  Block Kit gives structured
layout with headers, colour-coded severity, code blocks for diagnostics, and
clearly separated remediation steps.
"""

from __future__ import annotations

from k8s_advisor.models import DiagnosticContext, LLMAdvice, Severity

# Severity → emoji + colour attachment mapping
_SEVERITY_MAP: dict[Severity, tuple[str, str]] = {
    Severity.CRITICAL: (":red_circle:", "#dc3545"),
    Severity.HIGH: (":large_orange_circle:", "#fd7e14"),
    Severity.MEDIUM: (":large_yellow_circle:", "#ffc107"),
    Severity.LOW: (":white_circle:", "#6c757d"),
}


def format_slack_message(
    context: DiagnosticContext,
    advice: LLMAdvice,
) -> dict:
    """Build a Slack Block Kit payload for a single issue + advice pair.

    Returns a dict suitable for posting to the Slack API (blocks + optional
    attachments for the colour sidebar).
    """
    issue = context.issue
    emoji, colour = _SEVERITY_MAP.get(issue.severity, (":question:", "#6c757d"))

    # ── Header ─────────────────────────────────────────────────
    header = f"{emoji}  *{issue.issue_type.value}* — `{issue.namespace}/{issue.pod_name}`"

    # ── Severity + metadata ────────────────────────────────────
    meta_lines = [
        f"*Severity:* {issue.severity.value.upper()}",
        f"*Container:* `{issue.container_name or 'N/A'}`",
        f"*Restarts:* {issue.restart_count}",
        f"*Detected:* {issue.detected_at:%Y-%m-%d %H:%M:%S UTC}",
    ]
    if context.owner_reference:
        owner = context.owner_reference
        parent = owner.get("parent")
        if parent:
            meta_lines.append(
                f"*Owner:* {parent['kind']}/{parent['name']} → {owner['kind']}/{owner['name']}"
            )
        else:
            meta_lines.append(f"*Owner:* {owner['kind']}/{owner['name']}")

    # ── Root cause ─────────────────────────────────────────────
    root_cause_text = advice.root_cause

    # ── Remediation steps ──────────────────────────────────────
    steps_text = "\n".join(
        f"{i}. {step}" for i, step in enumerate(advice.remediation_steps, 1)
    )

    # ── Assemble blocks ───────────────────────────────────────
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(meta_lines)},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":mag: *Root Cause Analysis*\n{root_cause_text}",
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":wrench: *Suggested Remediation*\n{steps_text}",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"_Confidence: {advice.confidence}_ · "
                        f"_k8s-advisor · advisory only — no changes applied_"
                    ),
                }
            ],
        },
    ]

    return {
        "blocks": blocks,
        "attachments": [{"color": colour, "blocks": []}],
    }
