# IDP Platform (Local)

> A production-pattern Internal Developer Platform running entirely on a local `kind` cluster — no cloud account required.

Built as a portfolio project demonstrating GitOps, observability, and platform engineering practices a senior/staff DevOps engineer would apply in a real environment.

---

## Architecture

```
Windows 11 / WSL2 (Ubuntu)
└── Docker Desktop
    └── kind cluster: idp-local (3 nodes)
        │
        ├── control-plane  ── port 30000 → ArgoCD UI
        │                  ── port 30001 → Grafana UI
        ├── worker-1
        └── worker-2
            │
            ├── namespace: argocd
            │   └── ArgoCD  (GitOps engine)
            │       └── watches: github.com/<you>/idp-platform-local
            │
            └── namespace: monitoring
                ├── Prometheus  (metrics store)
                ├── Grafana     (dashboards)
                └── AlertManager
```

**GitOps flow:**

```
Git push → GitHub Actions (CI) → ArgoCD detects drift → syncs cluster
```

---

## Prerequisites

| Tool | Minimum version | Install |
|------|----------------|---------|
| Docker Desktop | 4.x | https://docs.docker.com/desktop/install/windows-install/ |
| kind | 0.20+ | `winget install Kubernetes.kind` |
| kubectl | 1.28+ | `winget install Kubernetes.kubectl` |
| Helm | 3.13+ | `winget install Helm.Helm` |

All commands run from inside WSL2 (Ubuntu).

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_GITHUB_USERNAME/idp-platform-local
cd idp-platform-local

# 2. Update the GitHub repo URL in the App-of-Apps manifest
#    Replace YOUR_GITHUB_USERNAME in argocd/apps/app-of-apps.yaml

# 3. Run the bootstrap (takes ~5 minutes)
chmod +x bootstrap.sh
./bootstrap.sh
```

That's it. The script is idempotent — safe to re-run if something fails.

---

## Access

| Service | URL | Credentials |
|---------|-----|-------------|
| ArgoCD UI | http://localhost:30000 | `admin` / printed by bootstrap.sh |
| Grafana | http://localhost:30001 | `admin` / `idp-grafana-admin` |

Grafana becomes available ~2 minutes after the monitoring app finishes syncing.

---

## Repo Structure

```
idp-platform-local/
├── kind/
│   └── cluster-config.yaml      # 3-node kind cluster + NodePort mappings
│
├── argocd/
│   ├── values-kind.yaml          # ArgoCD Helm values (NodePort, lean resources)
│   └── apps/
│       ├── app-of-apps.yaml      # ArgoCD self-managing bootstrap app
│       └── monitoring.yaml       # Prometheus + Grafana via kube-prometheus-stack
│
├── bootstrap.sh                  # One-shot setup (idempotent)
└── README.md
```

### Key design decisions

| Decision | Reason |
|----------|--------|
| `server.insecure: true` in ArgoCD | No TLS needed for localhost-only traffic |
| Single replicas throughout | Fits comfortably in 10 GB WSL2 allocation |
| `dex: enabled: false` | No external SSO in local dev |
| `ServerSideApply=true` for monitoring | kube-prometheus-stack objects exceed kubectl annotation size limit |
| `persistence: false` for Grafana | Dashboards load from ConfigMaps; no PVC needed for dev |
| `storageSpec: {}` for Prometheus | Ephemeral metrics; fast cluster teardown |

---

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| **1** | ✅ Done | kind cluster · ArgoCD · Prometheus · Grafana |
| **2** | Planned | Backstage Developer Portal |
| **3** | Planned | Kyverno Policy-as-Code |
| **4** | Planned | Custom Grafana dashboards · Architecture diagram |
| **5** | Planned | Resume polish · Demo recording |

---

## Teardown

```bash
kind delete cluster --name idp-local
```

---

## Troubleshooting

**ArgoCD pods are stuck in `Pending`**
```bash
kubectl describe pods -n argocd      # look for resource/node pressure
kubectl get nodes                    # confirm all 3 kind nodes are Ready
```

**Grafana NodePort not reachable at localhost:30001**
```bash
# Confirm the service is NodePort on 30001
kubectl get svc -n monitoring -l app.kubernetes.io/name=grafana

# Confirm the kind port mapping is in place
docker inspect idp-local-control-plane | grep -A5 PortBindings
```

**ArgoCD sync fails with `too long annotation` error**
Add `ServerSideApply=true` to the app's syncOptions (already done in monitoring.yaml).

**Cluster ran out of memory**
Confirm WSL2 has at least 8 GB available:
```bash
free -h
# If low, increase memory in %USERPROFILE%\.wslconfig and restart WSL2:
# [wsl2]
# memory=10GB
```
