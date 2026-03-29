#!/usr/bin/env bash
# bootstrap.sh — One-shot IDP Platform Phase 1 setup
#
# What this script does (in order):
#   1. Validates prerequisites are installed
#   2. Creates a 3-node kind cluster (idempotent — skips if already exists)
#   3. Installs ArgoCD via Helm with local-dev values
#   4. Waits for ArgoCD to become fully ready
#   5. Applies the App-of-Apps manifest to hand control to GitOps
#   6. Prints access URLs and the initial ArgoCD admin password
#
# Safe to re-run: each step checks for existing state before acting.
#
# Prerequisites (must be in PATH):
#   - kind    https://kind.sigs.k8s.io/docs/user/quick-start/#installation
#   - kubectl https://kubernetes.io/docs/tasks/tools/
#   - helm    https://helm.sh/docs/intro/install/
#
# IMPORTANT: edit argocd/apps/app-of-apps.yaml and replace YOUR_GITHUB_USERNAME
# with your actual GitHub username before running this script.

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────
CLUSTER_NAME="idp-local"
ARGOCD_NAMESPACE="argocd"
ARGOCD_HELM_REPO="https://argoproj.github.io/argo-helm"

# Paths are relative to the repo root (the directory containing this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIND_CONFIG="${SCRIPT_DIR}/kind/cluster-config.yaml"
ARGOCD_VALUES="${SCRIPT_DIR}/argocd/values-kind.yaml"
APP_OF_APPS="${SCRIPT_DIR}/argocd/apps/app-of-apps.yaml"

# ── Colour helpers ────────────────────────────────────────────────────────
info()  { printf '\n\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
ok()    { printf '\033[1;32m[ OK ]\033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*"; }
die()   { printf '\033[1;31m[ERR ]\033[0m  %s\n' "$*" >&2; exit 1; }

require() {
  command -v "$1" &>/dev/null \
    || die "'$1' not found in PATH. Install it and re-run: $2"
}

# ── 0. Sanity checks ──────────────────────────────────────────────────────
info "Checking prerequisites..."

require kind    "https://kind.sigs.k8s.io/docs/user/quick-start/#installation"
require kubectl "https://kubernetes.io/docs/tasks/tools/"
require helm    "https://helm.sh/docs/intro/install/"

# Warn if the GitHub username placeholder hasn't been replaced
if grep -q "YOUR_GITHUB_USERNAME" "${APP_OF_APPS}"; then
  warn "argocd/apps/app-of-apps.yaml still contains YOUR_GITHUB_USERNAME."
  warn "ArgoCD won't be able to sync until you replace it with your actual username."
  warn "Continuing — you can fix this after bootstrapping."
fi

ok "Prerequisites satisfied."

# ── 1. Create kind cluster ────────────────────────────────────────────────
info "Checking for existing kind cluster '${CLUSTER_NAME}'..."

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  warn "Cluster '${CLUSTER_NAME}' already exists — skipping creation."
else
  info "Creating kind cluster '${CLUSTER_NAME}'..."
  kind create cluster \
    --config "${KIND_CONFIG}" \
    --name   "${CLUSTER_NAME}"
  ok "Cluster created."
fi

# Point kubectl at the new cluster
kubectl config use-context "kind-${CLUSTER_NAME}"
ok "kubectl context set to kind-${CLUSTER_NAME}."

# ── 2. Install ArgoCD via Helm ─────────────────────────────────────────────
info "Adding / updating ArgoCD Helm repo..."
helm repo add argo "${ARGOCD_HELM_REPO}" --force-update
helm repo update argo

info "Installing ArgoCD (helm upgrade --install — safe to re-run)..."
helm upgrade --install argocd argo/argo-cd \
  --namespace "${ARGOCD_NAMESPACE}" \
  --create-namespace \
  --values   "${ARGOCD_VALUES}" \
  --wait \
  --timeout  6m

ok "ArgoCD Helm release deployed."

# ── 3. Wait for ArgoCD server ─────────────────────────────────────────────
info "Waiting for ArgoCD server deployment to become ready..."
kubectl rollout status deployment/argocd-server \
  --namespace "${ARGOCD_NAMESPACE}" \
  --timeout   4m
ok "ArgoCD server is ready."

# ── 4. Apply App-of-Apps ──────────────────────────────────────────────────
info "Applying App-of-Apps (hands GitOps control to ArgoCD)..."
kubectl apply -f "${APP_OF_APPS}"
ok "App-of-Apps applied. ArgoCD will now sync all apps in argocd/apps/."

# ── 5. Retrieve initial admin password ────────────────────────────────────
info "Fetching ArgoCD initial admin password..."
ARGOCD_PASSWORD=$(
  kubectl -n "${ARGOCD_NAMESPACE}" get secret argocd-initial-admin-secret \
    -o jsonpath="{.data.password}" 2>/dev/null | base64 --decode
) || ARGOCD_PASSWORD="(secret not found — may have been already rotated)"

# ── Done ──────────────────────────────────────────────────────────────────
cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  IDP Platform — Phase 1 Bootstrap Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ArgoCD UI  →  http://localhost:30000
               username : admin
               password : ${ARGOCD_PASSWORD}

  Grafana    →  http://localhost:30001
               (available ~2 min after monitoring app syncs)
               username : admin
               password : idp-grafana-admin

  Watch sync progress:
    kubectl get applications -n argocd -w

  Check pod health:
    kubectl get pods -n argocd
    kubectl get pods -n monitoring

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Next steps
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Push this repo to GitHub (if not done yet)
  2. Fix YOUR_GITHUB_USERNAME in argocd/apps/app-of-apps.yaml
  3. Re-apply:  kubectl apply -f argocd/apps/app-of-apps.yaml
  4. See roadmap in README.md for Phase 2 (Backstage)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EOF
