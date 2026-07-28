#!/usr/bin/env python3
"""
predict_failure.py
------------------
Fetches the last 10 build results for a Jenkins job via the Jenkins REST API,
sends the build history to AWS Bedrock (Claude Haiku) and asks it to assess
the risk level (low / medium / high) for the NEXT build.

If the assessed risk is Medium or High, a warning is posted to a Slack
incoming webhook before the build starts.

Usage (called automatically by the Jenkinsfile pre-build stage):
    python scripts/predict_failure.py \
        --jenkins-url  http://localhost:8080 \
        --job-name     my-job \
        --jenkins-user admin \
        --jenkins-token <api-token> \
        --slack-webhook https://hooks.slack.com/services/...

Exit codes:
    0  — success (including low-risk / first-build scenarios)
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
BUILD_HISTORY_N    = 10          # Number of past builds to include in the prompt
HTTP_TIMEOUT_S     = 30

# Risk levels that trigger a Slack warning
WARN_RISK_LEVELS = {"medium", "high"}

# Emoji per risk level for Slack messages
RISK_EMOJI = {
    "low":    ":large_green_circle:",
    "medium": ":large_yellow_circle:",
    "high":   ":red_circle:",
}


# ── Jenkins helpers ──────────────────────────────────────────────────────────

def fetch_build_history(jenkins_url: str, job_name: str,
                        user: str, token: str) -> list:
    """
    Return the last BUILD_HISTORY_N completed builds as a list of dicts:
        {number, result, duration_s, branch}

    Jenkins endpoint: GET /job/<name>/api/json?tree=builds[...]
    The `tree` query parameter limits the response to only the fields we need,
    keeping the payload small.
    """
    tree = (
        f"builds["
        f"number,result,duration,"
        f"actions[parameters[name,value],buildsByBranchName[*]]"
        f"]{{{0},{BUILD_HISTORY_N}}}"
    )
    url = f"{jenkins_url.rstrip('/')}/job/{job_name}/api/json"

    response = requests.get(
        url,
        auth=(user, token),
        params={"tree": tree},
        timeout=HTTP_TIMEOUT_S,
    )
    response.raise_for_status()

    raw_builds = response.json().get("builds", [])
    builds = []
    for b in raw_builds:
        builds.append({
            "number":     b.get("number"),
            "result":     b.get("result") or "IN_PROGRESS",   # null while running
            "duration_s": round(b.get("duration", 0) / 1000),
            "branch":     _extract_branch(b),
        })
    return builds


def _extract_branch(build: dict) -> str:
    """
    Best-effort extraction of the branch name from a build's action list.
    Handles:
      - Explicit BRANCH / GIT_BRANCH build parameters
      - Multibranch pipeline buildsByBranchName action
    """
    for action in build.get("actions", []):
        # Standard build parameters
        for param in action.get("parameters", []):
            if param.get("name", "").lower() in (
                "branch", "git_branch", "ghprbsourcebranch", "branch_name"
            ):
                return param.get("value", "unknown")
        # Multibranch pipeline injects a buildsByBranchName map
        bbn = action.get("buildsByBranchName", {})
        if bbn:
            return next(iter(bbn.keys()), "unknown")
    return "unknown"


# ── Bedrock helpers ──────────────────────────────────────────────────────────

def predict_risk_with_bedrock(history: list) -> dict:
    """
    Send build history to AWS Bedrock (Claude Haiku) and return a dict:
        { "risk_level": "low"|"medium"|"high",
          "reason": "...",
          "recommendation": "..." }
    """
    bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "eu-west-1"))

    history_text = "\n".join(
        f"Build #{b['number']}: {b['result']} | {b['duration_s']}s | branch={b['branch']}"
        for b in history
    )

    prompt = (
        "You are a CI/CD expert. Given the following Jenkins build history, "
        "assess the risk level for the NEXT build as low, medium, or high.\n\n"
        f"Build history (most recent first):\n{history_text}\n\n"
        "Respond ONLY with valid JSON in this exact format:\n"
        '{"risk_level": "low"|"medium"|"high", "reason": "one sentence", "recommendation": "one sentence"}'
    )

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": BEDROCK_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    })

    response = bedrock.invoke_model(modelId=BEDROCK_MODEL_ID, body=body)
    content = json.loads(response["body"].read())
    text = content["content"][0]["text"].strip()

    # Extract JSON from response (Claude sometimes wraps it in markdown)
    import re
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return {"risk_level": "low", "reason": text, "recommendation": "Proceed with caution."}


# ── Slack helpers ────────────────────────────────────────────────────────────

def post_warning_to_slack(webhook_url: str, job_name: str,
                          jenkins_url: str, prediction: dict) -> None:
    """
    Post a pre-build risk warning to Slack.

    Message format (Block Kit):
    ┌──────────────────────────────────────────────────────┐
    │  🟡  Pre-Build Risk Warning — <job>                  │
    ├────────────────────────┬─────────────────────────────┤
    │  Job: <link>           │  Risk Level: MEDIUM          │
    ├──────────────────────────────────────────────────────┤
    │  🤖 AI Assessment (Claude Haiku):                  │
    │  Reason: …                                           │
    │  Recommendation: …                                   │
    └──────────────────────────────────────────────────────┘
    """
    risk   = prediction.get("risk_level", "unknown").lower()
    reason = prediction.get("reason", "N/A")
    reco   = prediction.get("recommendation", "N/A")
    emoji  = RISK_EMOJI.get(risk, ":white_circle:")
    job_url = f"{jenkins_url.rstrip('/')}/job/{job_name}/"

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} Pre-Build Risk Warning — {job_name}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Job:*\n<{job_url}|{job_name}>",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Risk Level:*\n{risk.upper()}",
                    },
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*:robot_face: AI Assessment (Claude Haiku):*\n\n"
                        f"*Reason:* {reason}\n\n"
                        f"*Recommendation:* {reco}"
                    ),
                },
            },
        ]
    }

    response = requests.post(webhook_url, json=payload, timeout=HTTP_TIMEOUT_S)
    response.raise_for_status()
    print("Slack warning sent successfully.")


# ── CLI argument parsing ─────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Predict Jenkins pipeline failure risk using AWS Bedrock (Claude Haiku) "
            "and optionally post a Slack warning."
        )
    )
    parser.add_argument(
        "--jenkins-url",
        required=True,
        help="Base Jenkins URL, e.g. http://localhost:8080",
    )
    parser.add_argument(
        "--job-name",
        required=True,
        help="Jenkins job / pipeline name",
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

    # Step 1: Retrieve build history from Jenkins
    print(f"[1/3] Fetching last {BUILD_HISTORY_N} build results for '{args.job_name}' …")
    try:
        history = fetch_build_history(
            args.jenkins_url,
            args.job_name,
            args.jenkins_user,
            args.jenkins_token,
        )
    except requests.HTTPError as exc:
        print(f"ERROR fetching build history: {exc}", file=sys.stderr)
        sys.exit(1)

    if not history:
        # First-ever build — no history to predict from, skip gracefully
        print("No build history found — skipping risk prediction (likely the first build).")
        sys.exit(0)

    # Step 2: Send history to Bedrock for risk assessment
    print(
        f"[2/3] Sending {len(history)} build records to AWS Bedrock ({BEDROCK_MODEL_ID}) "
        f"for risk assessment …"
    )
    try:
        prediction = predict_risk_with_bedrock(history)
    except Exception as exc:
        print(f"ERROR calling AWS Bedrock: {exc}", file=sys.stderr)
        _post_fallback_to_slack(args.slack_webhook, args.job_name)
        print("AI analysis unavailable — fallback Slack message sent. Continuing.")
        sys.exit(0)

    risk_level = prediction.get("risk_level", "low").lower()
    print(f"      Assessed risk level : {risk_level.upper()}")
    print(f"      Reason              : {prediction.get('reason', 'N/A')}")
    print(f"      Recommendation      : {prediction.get('recommendation', 'N/A')}")

    # Step 3: Post Slack warning only for medium / high risk
    if risk_level in WARN_RISK_LEVELS:
        print(f"[3/3] Risk is {risk_level.upper()} — posting Slack warning …")
        try:
            post_warning_to_slack(
                args.slack_webhook,
                args.job_name,
                args.jenkins_url,
                prediction,
            )
        except requests.HTTPError as exc:
            print(f"ERROR posting to Slack: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print("[3/3] Risk is LOW — no Slack warning needed. Proceeding with build.")

    print("Done.")


def _post_fallback_to_slack(webhook_url: str, job_name: str) -> None:
    """Post a minimal fallback message when Bedrock is unavailable."""
    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *AI risk prediction unavailable* for *{job_name}*.\n"
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
