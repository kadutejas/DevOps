# ================================================================
# kube-bot Dockerfile
# ================================================================
# Builds a minimal, non-root Python image.
# No secrets or credentials are baked in – all sensitive values
# are injected at runtime via Kubernetes Secrets.
# ================================================================

FROM python:3.11-slim

# ── Non-root user for security ───────────────────────────────────
RUN useradd -r -u 1001 -g root kube-bot

WORKDIR /app

# ── Python dependencies (cached layer) ───────────────────────────
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────────────
COPY src/ ./src/

# ── Drop to non-root ──────────────────────────────────────────────
USER 1001

# ── Healthcheck – verify core imports load without error ─────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "from rules import RulesEngine; from slack_notifier import SlackNotifier" || exit 1

# ── Run unbuffered so logs appear immediately ────────────────────
CMD ["python", "-u", "src/main.py"]
