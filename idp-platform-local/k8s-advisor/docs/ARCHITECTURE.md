# K8s Advisor — Architecture

## Overview

K8s Advisor is an in-cluster Kubernetes advisory service that continuously monitors
workloads in a target namespace, detects common pod failure patterns, queries an LLM
for root-cause analysis, and delivers actionable Slack notifications. It is **read-only
and advisory-only** — it never mutates cluster state.

## System Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│  Target Namespace                                                │
│  ┌──────┐ ┌──────┐ ┌────────────┐ ┌─────────────┐              │
│  │ Pods │ │Events│ │Deployments │ │ ReplicaSets │              │
│  └──┬───┘ └──┬───┘ └─────┬──────┘ └──────┬──────┘              │
│     │        │            │               │                      │
│     └────────┴────────────┴───────────────┘                      │
│                       │  K8s API (read-only)                     │
│                       ▼                                          │
│  ┌─────────────────────────────────────────┐                     │
│  │            k8s-advisor pod              │                     │
│  │  ┌─────────┐  ┌──────────┐  ┌────────┐ │                     │
│  │  │ Watcher │→│ Detector │→│Diagnostics│                     │
│  │  └─────────┘  └──────────┘  └────┬───┘ │                     │
│  │                                   │     │                     │
│  │                    ┌──────────────┘     │                     │
│  │                    ▼                    │                     │
│  │  ┌────────────────────────┐             │                     │
│  │  │  LLM Provider Layer    │             │                     │
│  │  │  ┌───────┐┌────────┐  │             │                     │
│  │  │  │Ollama ││OpenAI/ │  │             │                     │
│  │  │  │(local)││Claude  │  │             │                     │
│  │  │  └───────┘└────────┘  │             │                     │
│  │  └───────────┬────────────┘             │                     │
│  │              │                          │                     │
│  │              ▼                          │                     │
│  │  ┌────────────────────┐                 │                     │
│  │  │  State Tracker     │                 │                     │
│  │  │  (dedup / cooldown)│                 │                     │
│  │  └────────┬───────────┘                 │                     │
│  │           │                             │                     │
│  │           ▼                             │                     │
│  │  ┌────────────────────┐                 │                     │
│  │  │  Slack Notifier    │─── HTTPS ──────────→  Slack API       │
│  │  └────────────────────┘                 │                     │
│  └─────────────────────────────────────────┘                     │
└──────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | File | Purpose |
|-----------|------|---------|
| **Config** | `config.py` | Loads all settings from environment variables with sane defaults |
| **Models** | `models.py` | Typed dataclasses for `PodIssue`, `DiagnosticContext`, `LLMAdvice` |
| **Watcher** | `watcher.py` | Polls K8s API for Pods, Events, Deployments, ReplicaSets in the target namespace |
| **Detectors** | `detectors.py` | Pattern-matches pod/container statuses to known failure modes |
| **Diagnostics** | `diagnostics.py` | Assembles structured context: status, events, owner refs, resource specs |
| **LLM Base** | `llm/base.py` | Abstract `LLMProvider` interface |
| **Ollama** | `llm/ollama.py` | Default local LLM provider via Ollama HTTP API |
| **OpenAI** | `llm/openai.py` | OpenAI-compatible provider (GPT-4, etc.) |
| **Claude** | `llm/claude.py` | Anthropic Claude provider |
| **LLM Factory** | `llm/factory.py` | Selects provider based on config |
| **Slack** | `notifier/slack.py` | Posts Block Kit messages to Slack via webhook or Bot token |
| **Formatter** | `notifier/formatter.py` | Builds structured Slack Block Kit payloads |
| **State Tracker** | `state.py` | Prevents duplicate alerts via cooldown windows and issue fingerprinting |
| **Entrypoint** | `__main__.py` | Main loop: watch → detect → diagnose → advise → notify |

## Data Flow

1. **Watch** — Watcher polls the K8s API every N seconds for pod/event state.
2. **Detect** — Detectors evaluate each pod for known failure patterns and assign severity.
3. **Diagnose** — Diagnostics gathers events, owner controller, resource spec excerpts.
4. **Fingerprint** — State tracker checks if this issue was already reported within the cooldown window.
5. **Advise** — Diagnostic context is sent to the LLM provider; it returns root-cause and remediation.
6. **Notify** — Slack notifier posts a formatted Block Kit message.
7. **Track** — State tracker records the issue fingerprint + timestamp to suppress duplicates.

## Security Model

- **No cluster-admin.** The ServiceAccount has only `get`, `list`, `watch` on Pods, Events, Deployments, ReplicaSets — scoped to a single namespace.
- **Local LLM by default.** Ollama runs in-cluster; no diagnostic data leaves the network boundary unless an external provider is explicitly configured.
- **No write access.** The service never creates, patches, or deletes any Kubernetes resource.
- **Secrets via K8s Secrets.** API keys are mounted as env vars from Kubernetes Secrets, never hardcoded.

## Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `TARGET_NAMESPACE` | `default` | Namespace to monitor |
| `POLL_INTERVAL_SECONDS` | `30` | Seconds between poll cycles |
| `LLM_PROVIDER` | `ollama` | `ollama`, `openai`, or `claude` |
| `OLLAMA_URL` | `http://ollama.ollama:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3` | Model name for Ollama |
| `OPENAI_API_KEY` | — | OpenAI API key (enables openai provider) |
| `OPENAI_MODEL` | `gpt-4` | OpenAI model |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (enables claude provider) |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Claude model |
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook URL |
| `SLACK_BOT_TOKEN` | — | Alternative: Slack Bot OAuth token |
| `SLACK_CHANNEL` | `#k8s-alerts` | Channel for bot-token mode |
| `ALERT_COOLDOWN_MINUTES` | `30` | Suppress duplicate alerts for this window |
| `LOG_LEVEL` | `INFO` | Python logging level |
