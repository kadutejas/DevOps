# ================================================================
# k8s-advisor Dockerfile
# ================================================================
# Builds a minimal, non-root Python image.
# No API keys or credentials are baked in – all sensitive values
# are injected at runtime via Kubernetes Secrets.
# ================================================================

FROM python:3.11-slim

# ── Non-root user for security ─────────────────────────────────
RUN useradd -r -u 1001 -g root k8s-advisor

WORKDIR /app

# ── Python dependencies (cached layer) ─────────────────────────
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ─────────────────────────────────────────
COPY k8s_advisor/ ./k8s_advisor/
COPY main.py ./

# ── Drop to non-root ───────────────────────────────────────────
USER 1001

# ── Healthcheck – verify core imports load without error ────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "from k8s_advisor.config import Config" || exit 1

# ── Run unbuffered so logs appear immediately ──────────────────
CMD ["python", "-u", "main.py"]
