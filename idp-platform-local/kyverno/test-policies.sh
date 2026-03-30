#!/usr/bin/env bash
# kyverno/test-policies.sh — Smoke-test all Phase 3 Kyverno policies
#
# WHAT THIS DOES:
#   Fires real kubectl commands against your live cluster to confirm each
#   policy behaves as expected. Tests both the ALLOW path and the DENY path.
#
# PREREQUISITES:
#   - Kyverno engine running:  kubectl get pods -n kyverno
#   - Policies synced:         kubectl get clusterpolicy
#
# HOW TO RUN:
#   chmod +x kyverno/test-policies.sh
#   ./kyverno/test-policies.sh
#
# OUTPUT:
#   PASS — pod was created/blocked as expected
#   FAIL — unexpected behaviour (investigate with kubectl describe)

set -uo pipefail   # -e intentionally omitted: we test expected failures

# ── Colour helpers ────────────────────────────────────────────────────────
pass() { printf '\033[1;32m[PASS]\033[0m  %s\n' "$*"; }
fail() { printf '\033[1;31m[FAIL]\033[0m  %s\n' "$*"; FAILED=$((FAILED+1)); }
info() { printf '\n\033[1;34m────────────────────────────────────────\033[0m\n\033[1;34m  %s\033[0m\n\033[1;34m────────────────────────────────────────\033[0m\n' "$*"; }

FAILED=0
NS="kyverno-test"

# ── Setup — create a throwaway namespace for tests ────────────────────────
kubectl create namespace "${NS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1

cleanup() {
  kubectl delete namespace "${NS}" --ignore-not-found >/dev/null 2>&1
}
trap cleanup EXIT   # always delete the test namespace on script exit

# Helper: try to create a pod, return 0=created 1=blocked
try_create() {
  kubectl run "$1" \
    --namespace "${NS}" \
    --image="$2" \
    --restart=Never \
    ${3:+--overrides="$3"} \
    --dry-run=server \
    -o name > /dev/null 2>&1
  return $?
}

# ── Test 1: block-latest-tag (Audit) ─────────────────────────────────────
info "Test 1: block-latest-tag"

# ALLOW: pinned tag — should be allowed
if try_create "test-pinned" "nginx:1.25.3" ""; then
  pass "pinned tag (nginx:1.25.3) → allowed"
else
  fail "pinned tag (nginx:1.25.3) → unexpectedly blocked"
fi

# NOTE: In Audit mode, :latest is LOGGED but not blocked.
# This test confirms audit mode doesn't prevent the pod (expected ALLOW in audit).
if try_create "test-latest" "nginx:latest" ""; then
  pass ":latest tag → allowed (Audit mode — violation logged, not blocked)"
else
  fail ":latest tag → blocked in Audit mode (policy may be misconfigured as Enforce)"
fi

# ── Test 2: require-resource-limits (Audit) ───────────────────────────────
info "Test 2: require-resource-limits"

OVERRIDES_WITH_LIMITS='{
  "spec": {
    "containers": [{
      "name": "test",
      "image": "nginx:1.25.3",
      "resources": {
        "requests": {"cpu": "50m", "memory": "64Mi"},
        "limits":   {"cpu": "100m","memory": "128Mi"}
      }
    }]
  }
}'

if try_create "test-with-limits" "nginx:1.25.3" "${OVERRIDES_WITH_LIMITS}"; then
  pass "container with resource limits → allowed"
else
  fail "container with resource limits → unexpectedly blocked"
fi

# In Audit mode, missing limits should still be allowed (just logged)
if try_create "test-no-limits" "nginx:1.25.3" ""; then
  pass "container without limits → allowed (Audit mode — violation logged)"
else
  fail "container without limits → blocked in Audit mode"
fi

# ── Test 3: block-root-containers (Audit) ────────────────────────────────
info "Test 3: block-root-containers"

OVERRIDES_NONROOT='{
  "spec": {
    "containers": [{
      "name": "test",
      "image": "nginx:1.25.3",
      "securityContext": {"runAsNonRoot": true, "runAsUser": 1000}
    }]
  }
}'

if try_create "test-nonroot" "nginx:1.25.3" "${OVERRIDES_NONROOT}"; then
  pass "non-root container (runAsUser: 1000) → allowed"
else
  fail "non-root container → unexpectedly blocked"
fi

# ── Test 4: add-default-labels (Mutate) ──────────────────────────────────
info "Test 4: add-default-labels"

# Create a real pod (not dry-run) so we can inspect its labels after mutation
kubectl run test-labels \
  --namespace "${NS}" \
  --image=nginx:1.25.3 \
  --restart=Never \
  >/dev/null 2>&1 || true

sleep 2   # give Kyverno a moment to mutate the pod

LABELS=$(kubectl get pod test-labels -n "${NS}" \
  -o jsonpath='{.metadata.labels}' 2>/dev/null || echo "{}")

if echo "${LABELS}" | grep -q '"platform":"idp"'; then
  pass "label platform=idp was auto-added by mutation"
else
  fail "label platform=idp was NOT added (mutation may have failed)"
fi

if echo "${LABELS}" | grep -q '"managed-by":"argocd"'; then
  pass "label managed-by=argocd was auto-added by mutation"
else
  fail "label managed-by=argocd was NOT added"
fi

# ── Test 5: block-privileged-containers (Enforce) ────────────────────────
info "Test 5: block-privileged-containers"

OVERRIDES_PRIVILEGED='{
  "spec": {
    "containers": [{
      "name": "test",
      "image": "nginx:1.25.3",
      "securityContext": {"privileged": true}
    }]
  }
}'

# This is Enforce — the pod MUST be blocked
if try_create "test-privileged" "nginx:1.25.3" "${OVERRIDES_PRIVILEGED}"; then
  fail "privileged container → was ALLOWED (policy not enforcing!)"
else
  pass "privileged container → blocked by Enforce policy"
fi

# Non-privileged should pass
OVERRIDES_NONPRIVILEGED='{
  "spec": {
    "containers": [{
      "name": "test",
      "image": "nginx:1.25.3",
      "securityContext": {"privileged": false}
    }]
  }
}'

if try_create "test-not-privileged" "nginx:1.25.3" "${OVERRIDES_NONPRIVILEGED}"; then
  pass "non-privileged container → allowed"
else
  fail "non-privileged container → unexpectedly blocked"
fi

# ── Summary ───────────────────────────────────────────────────────────────
printf '\n\033[1;34m════════════════════════════════════════\033[0m\n'
if [[ ${FAILED} -eq 0 ]]; then
  printf '\033[1;32m  ALL TESTS PASSED\033[0m\n'
else
  printf '\033[1;31m  %d TEST(S) FAILED\033[0m\n' "${FAILED}"
  printf '  Check: kubectl get clusterpolicy\n'
  printf '  Check: kubectl get policyreport -A\n'
fi
printf '\033[1;34m════════════════════════════════════════\033[0m\n\n'

exit ${FAILED}
