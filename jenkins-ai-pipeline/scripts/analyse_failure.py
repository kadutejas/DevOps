#!/usr/bin/env python3
"""
analyse_failure.py
------------------
Fetches the last 100 lines of a failed Jenkins build's console log via the
Jenkins REST API, sends the log to AWS Bedrock (Claude Haiku) with a structured
prompt requesting root-cause analysis and a fix suggestion, then posts the AI
response to a Slack incoming webhook.

Usage (called automatically by the Jenkinsfile post{failure} block):
    python scripts/analyse_failure.py \
        --jenkins-url  http://localhost:8080 \
        --job-name     my-job \
        --build-number 42 \
        --jenkins-user admin \
        --jenkins-token <api-token> \
        --slack-webhook https://hooks.slack.com/services/...

Exit codes:
    0  — success
    1  — HTTP or parsing error (printed to stderr)
"""

import argparse
import json
import os
import sys
import boto3
import requests

# ── Configuration constants ──────────────────────────────────────────────────

BEDROCK_MODEL_ID   = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
BEDROCK_MAX_TOKENS = 500
LOG_TAIL_LINES     = 100        # How many trailing log lines to send to the LLM
BEDROCK_TIMEOUT_S  = 120        # Bedrock can be slow on first request; be generous
HTTP_TIMEOUT_S     = 30


# ── Jenkins helpers ──────────────────────────────────────────────────────────

def fetch_console_log(jenkins_url: str, job_name: str, build_number: str,
                      user: str, token: str) -> str:
    """
    Download the plain-text console log for the given build and return the
    last LOG_TAIL_LINES lines as a single string.

    Jenkins endpoint: GET /job/<name>/<number>/consoleText
    """
    url = f"{jenkins_url.rstrip('/')}/job/{job_name}/{build_number}/consoleText"

    response = requests.get(
        url,
        auth=(user, token),
        timeout=HTTP_TIMEOUT_S,
    )
    response.raise_for_status()

    lines = response.text.splitlines()
    tail  = lines[-LOG_TAIL_LINES:]      # Keep only the most relevant tail
    return "\n".join(tail)


# ── Bedrock helpers ──────────────────────────────────────────────────────────

def analyse_with_bedrock(log_snippet: str) -> str:
    """
    Send the log excerpt to AWS Bedrock (Claude Haiku) and return the LLM's
    structured analysis.

    The prompt enforces a fixed output format so downstream parsing is
    straightforward if needed later.
    """
    prompt = (
        "You are a senior DevOps engineer reviewing a Jenkins CI/CD build failure.\n"
        "Analyse the following console log excerpt and respond in EXACTLY this format "
        "(do not add any extra text before or after):\n\n"
        "ROOT CAUSE:\n"
        "<one-paragraph description of what went wrong and why>\n\n"
        "FIX SUGGESTION:\n"
        "<concrete, actionable numbered steps to resolve the issue>\n\n"
        "SEVERITY: <Low | Medium | High>\n\n"
        "--- LOG START ---\n"
        f"{log_snippet}\n"
        "--- LOG END ---"
    )

    client = boto3.client(
        "bedrock-runtime",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )
    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": BEDROCK_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )
    result = json.loads(response["body"].read())
    return result["content"][0]["text"].strip()


# ── Slack helpers ────────────────────────────────────────────────────────────

def post_to_slack(webhook_url: str, job_name: str, build_number: str,
                  jenkins_url: str, analysis: str) -> None:
    """
    Format and post the AI analysis to a Slack channel via an incoming webhook.

    Message format (Block Kit):
    ┌──────────────────────────────────────────────────────┐
    │  🔴  Build Failure Analysis — <job> #<number>        │
    ├──────────────────────────────────────────────────────┤
    │  Build URL: <link>                                   │
    │  ─────────────────────────────────────────────────── │
    │  🤖 AI Analysis (Claude Haiku):                      │
    │  ```<root cause / fix / severity>```                 │
    └──────────────────────────────────────────────────────┘
    """
    build_url = f"{jenkins_url.rstrip('/')}/job/{job_name}/{build_number}/"

    # Slack's section block text limit is 3 000 characters
    if len(analysis) > 2800:
        analysis = analysis[:2800] + "\n…(truncated — see build log for full output)"

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f":red_circle: Build Failure Analysis — {job_name} #{build_number}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Build URL:* <{build_url}|{job_name} #{build_number}>",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*:robot_face: AI Analysis (Claude Haiku):*\n```{analysis}```",
                },
            },
        ]
    }

    response = requests.post(webhook_url, json=payload, timeout=HTTP_TIMEOUT_S)
    response.raise_for_status()
    print("Slack notification sent successfully.")


# ── CLI argument parsing ─────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyse a Jenkins build failure using AWS Bedrock (Claude Haiku) and post to Slack."
    )
    parser.add_argument(
        "--jenkins-url",
        required=True,
        help="Base Jenkins URL, e.g. http://localhost:8080",
    )
    parser.add_argument(
        "--job-name",
        required=True,
        help="Jenkins job / pipeline name (URL-encoded if it contains spaces)",
    )
    parser.add_argument(
        "--build-number",
        required=True,
        help="Jenkins build number to analyse",
    )
    parser.add_argument(
        "--jenkins-user",
        required=True,
        help="Jenkins username for API authentication",
    )
    parser.add_argument(
        "--jenkins-token",
        required=True,
        help="Jenkins API token (generate at /user/<name>/configure)",
    )
    parser.add_argument(
        "--slack-webhook",
        required=True,
        help="Slack incoming webhook URL",
    )
    return parser.parse_args()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # Step 1: Retrieve the console log tail from Jenkins
    print(
        f"[1/3] Fetching last {LOG_TAIL_LINES} lines of console log "
        f"for {args.job_name} #{args.build_number} …"
    )
    try:
        log_snippet = fetch_console_log(
            args.jenkins_url,
            args.job_name,
            args.build_number,
            args.jenkins_user,
            args.jenkins_token,
        )
    except requests.HTTPError as exc:
        print(f"ERROR fetching console log: {exc}", file=sys.stderr)
        sys.exit(1)

    # Step 2: Send to Bedrock for analysis
    line_count = len(log_snippet.splitlines())
    print(f"[2/3] Sending {line_count} log lines to AWS Bedrock ({BEDROCK_MODEL_ID}) …")
    try:
        analysis = analyse_with_bedrock(log_snippet)
    except Exception as exc:
        print(f"ERROR calling AWS Bedrock: {exc}", file=sys.stderr)
        _post_fallback_to_slack(args.slack_webhook, args.job_name, args.build_number)
        print("AI analysis unavailable — fallback Slack message sent. Continuing.")
        sys.exit(0)

    print("      Analysis received:")
    print("      " + "\n      ".join(analysis.splitlines()[:6]) + " …")

    # Step 3: Post to Slack
    print("[3/3] Posting analysis to Slack …")
    try:
        post_to_slack(
            args.slack_webhook,
            args.job_name,
            args.build_number,
            args.jenkins_url,
            analysis,
        )
    except requests.HTTPError as exc:
        print(f"ERROR posting to Slack: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Done.")


def _post_fallback_to_slack(webhook_url: str, job_name: str, build_number: str) -> None:
    """Post a minimal fallback message when Bedrock is unavailable."""
    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *AI analysis unavailable* for "
                        f"{job_name} #{build_number}.\n"
                        "AWS Bedrock could not be reached — check credentials, "
                        "region, and model access."
                    ),
                },
            }
        ]
    }
    try:
        requests.post(webhook_url, json=payload, timeout=HTTP_TIMEOUT_S)
    except Exception:
        pass  # Never let a notification failure propagate


if __name__ == "__main__":
    main()
