# IDP Platform — Component Breakdown

> **Purpose:** This document explains every component in the Internal Developer Platform, what it does, why it's essential, and how it integrates with other tools.

---

## Overview: What is an Internal Developer Platform?

An **Internal Developer Platform (IDP)** is a self-service layer that sits between developers and infrastructure. It provides standardized workflows, automated deployments, observability, and governance — freeing developers to focus on features instead of cluster management.

**This IDP includes:**
- **GitOps** → automated deployments from Git
- **Observability** → metrics, dashboards, alerting
- **Developer Portal** → service discovery, documentation hub
- **Policy-as-Code** → automated security and compliance

---

## Component Deep Dive

### 🐳 **kind (Kubernetes IN Docker)**
**What it is:** Local Kubernetes cluster that runs entirely in Docker containers.

**Why we use it:**
- **Zero cloud costs** — runs entirely on your laptop
- **Fast iteration** — create/destroy clusters in seconds
- **Production parity** — real Kubernetes API, not a simulation
- **Multi-node** — tests workload scheduling across nodes
- **Port mapping** — exposes services via `localhost:30XXX`

**In the real world:** Replace with EKS, GKE, or AKS. The platform code stays identical.

**Key config:** [kind/cluster-config.yaml](../kind/cluster-config.yaml)
- Maps ports 30000 (ArgoCD), 30001 (Grafana), 30002 (Backstage) to localhost
- 3 nodes (1 control-plane + 2 workers) mirror production topology

---

### 🚀 **ArgoCD (GitOps Engine)**
**What it is:** Kubernetes operator that syncs cluster state to match what's declared in Git.

**Why we use it:**
- **Git as single source of truth** — no `kubectl apply` needed after bootstrap
- **Drift detection** — spots when cluster state diverges from Git
- **Self-healing** — automatically reverts manual changes
- **Multi-app management** — manages the entire platform from one place
- **Audit trail** — every cluster change is a Git commit

**How it works in our IDP:**
1. Watches `idp-platform-local/argocd/apps/` in GitHub
2. Every `.yaml` file becomes an ArgoCD Application
3. Applications deploy services (monitoring, backstage, policies)
4. When you `git push`, ArgoCD syncs the changes automatically

**Key concepts:**
- **App-of-Apps pattern** — one parent app manages all child apps
- **Sync waves** — controls deployment order (engine before policies)
- **Health status** — monitors workload readiness, not just deployment

**Access:** http://localhost:30000 (admin / generated password)

---

### 📊 **Prometheus (Metrics Collection)**
**What it is:** Time-series database that scrapes metrics from every component in the cluster.

**Why we use it:**
- **Pull model** — Prometheus polls targets; no agent deployment needed
- **Battle-tested** — CNCF graduated project, industry standard
- **PromQL** — powerful query language for metrics analysis
- **Service discovery** — automatically finds new targets via Kubernetes API
- **Alerting integration** — triggers alerts based on metric thresholds

**What it monitors in our IDP:**
- **Node metrics** — CPU, memory, disk, network per Kubernetes node
- **Pod metrics** — resource usage, restart counts, readiness probes
- **Application metrics** — ArgoCD sync status, Backstage health, policy violations
- **Cluster metrics** — API server performance, etcd health, scheduler latency

**Data retention:** 7 days (configurable in [argocd/apps/monitoring.yaml](../argocd/apps/monitoring.yaml))

**Scrape targets:**
- `kube-state-metrics` → converts Kubernetes object state to metrics
- `node-exporter` → exposes host-level metrics (CPU, memory, disk)
- Service endpoints with `/metrics` paths → application-specific metrics

---

### 📈 **Grafana (Visualization Layer)**
**What it is:** Dashboard platform that queries Prometheus and renders charts, graphs, and alerts.

**Why we use it:**
- **Rich visualizations** — time series, heatmaps, tables, single stats
- **Dashboard-as-Code** — JSON dashboards stored in Git
- **Alerting** — visual alerts with notification channels (Slack, email)
- **Multi-datasource** — supports Prometheus, Loki, Jaeger, CloudWatch
- **Templating** — dynamic dashboards with variable substitution

**Dashboards included:**
- **Default Kubernetes dashboards** — node overview, pod metrics, namespace views
- **Custom IDP dashboard** — cluster resource usage by namespace
- **ArgoCD metrics** — sync status, application health
- **Kyverno policy reports** — violations, enforcement status

**Access:** http://localhost:30001 (admin / idp-grafana-admin)

**Dashboard loading:** ConfigMap sidecar auto-imports any JSON with label `grafana_dashboard: "1"`

---

### 🏠 **Backstage (Developer Portal)**
**What it is:** Developer-facing frontend that provides service discovery, documentation, and developer workflows.

**Why we use it:**
- **Service catalog** — single place to discover all services, APIs, and owners
- **Documentation hub** — TechDocs render Markdown from Git repos
- **Software scaffolder** — templates for creating new services
- **Plugin ecosystem** — integrates with every tool (ArgoCD, Grafana, Kyverno)
- **Self-service** — developers find what they need without asking DevOps

**Features in our IDP:**
- **Software catalog** — registers ArgoCD, Grafana, Prometheus, Backstage itself
- **Team ownership** — maps services to responsible teams
- **Links to live systems** — direct links to dashboards, Git repos, live URLs
- **Guest authentication** — no complex SSO setup for local development

**Catalog structure:**
- **Systems** — group related components (IDP Platform system)
- **Components** — deployable units (ArgoCD, Grafana, etc.)
- **Groups** — teams that own components (DevOps Team)

**Access:** http://localhost:30002 (click "Enter as Guest")

**Data source:** [backstage/catalog/idp-platform.yaml](../backstage/catalog/idp-platform.yaml)

---

### 🛡️ **Kyverno (Policy-as-Code Engine)**
**What it is:** Admission webhook that intercepts Kubernetes resource creation and applies policies.

**Why we use it:**
- **Admission control** — policies run before resources are saved to etcd
- **Multiple modes** — validate (allow/deny), mutate (auto-modify), generate (create companion resources)
- **YAML-based** — policies written in familiar Kubernetes YAML syntax
- **No learning curve** — uses JMESPath, same query language as kubectl
- **GitOps-friendly** — policies are versioned and managed like any other Kubernetes resource

**Policy modes explained:**
- **Audit** — logs violations but allows the resource (safe for learning)
- **Enforce** — hard rejects policy violations (production mode)
- **Mutate** — automatically modifies resources (add labels, set defaults)

**Policies in our IDP:**
| Policy | Mode | Purpose |
|--------|------|---------|
| `block-latest-tag` | Audit | Rejects `:latest` image tags |
| `require-resource-limits` | Audit | Enforces CPU/memory requests and limits |
| `block-root-containers` | Audit | Prevents containers running as root |
| `add-default-labels` | Mutate | Auto-adds `environment`, `managed-by`, `platform` labels |
| `block-privileged-containers` | **Enforce** | Hard blocks privileged containers |

**Policy testing:** Run [kyverno/test-policies.sh](../kyverno/test-policies.sh) to verify all policies work

**Violation monitoring:**
```bash
kubectl get policyreport -A                    # view all violations
kubectl get clusterpolicy                      # list active policies
```

---

### 🔧 **Helm (Package Manager)**
**What it is:** Kubernetes package manager that templates YAML manifests and manages releases.

**Why we use it:**
- **Templating** — one chart supports multiple environments (dev, staging, prod)
- **Dependency management** — charts can depend on other charts
- **Release management** — upgrade, rollback, uninstall entire applications
- **Community charts** — massive ecosystem of pre-built applications
- **Values override** — customize behavior without forking charts

**How we use it in the IDP:**
- **ArgoCD** deployed via official `argo/argo-cd` chart
- **Monitoring** deployed via `prometheus-community/kube-prometheus-stack` chart
- **Backstage** deployed via `backstage/backstage` chart
- **Kyverno** deployed via `kyverno/kyverno` chart

**Values customization:** Each app has a `values:` block in its ArgoCD Application manifest

---

### 🔀 **GitHub (Git Repository)**
**What it is:** Source control and the single source of truth for all platform configuration.

**Why we use it:**
- **Version control** — every change is tracked, auditable, and rollback-able
- **Collaboration** — pull requests for platform changes
- **Automation hooks** — GitHub Actions for CI, webhooks for ArgoCD sync
- **Documentation** — README, architecture docs, runbooks live alongside code
- **Access control** — branch protection, required reviews for production

**Repository structure:**
```
idp-platform-local/
├── kind/                    # Cluster configuration
├── argocd/                  # GitOps applications
│   ├── apps/                # Apps managed by ArgoCD
│   └── values-kind.yaml     # ArgoCD Helm values
├── kyverno/                 # Policy-as-Code
│   ├── policies/            # ClusterPolicy manifests
│   └── test-policies.sh     # Policy validation script
├── backstage/               # Developer portal
│   └── catalog/             # Service catalog definitions
├── grafana/                 # Custom dashboards
│   └── dashboards/          # Dashboard JSON files
└── docs/                    # Documentation
```

---

### 🧪 **CI/CD Pipeline (GitHub Actions)**
**What it is:** Automated testing and validation that runs on every Git push.

**Why we use it (future):**
- **Shift-left testing** — catch policy violations before they reach the cluster
- **Automated security scanning** — vulnerability scanning, secret detection
- **Infrastructure validation** — validate Kubernetes YAML, Helm charts
- **Policy dry-run** — test Kyverno policies against sample workloads

**Planned pipeline stages:**
1. **Lint** — YAML validation, Helm chart validation
2. **Security** — Trivy container scanning, SAST analysis
3. **Policy test** — run `kyverno/test-policies.sh`
4. **Notification** — Slack notification on failures

---

## Integration Points: How Components Work Together

### **GitOps Flow**
```
Developer commits → GitHub → ArgoCD detects change → ArgoCD syncs cluster
```

### **Observability Flow**
```
Workloads expose /metrics → Prometheus scrapes → Grafana visualizes → Alerts fire
```

### **Policy Flow**
```
kubectl/ArgoCD creates resource → Kyverno intercepts → Policy evaluates → Allow/Deny/Mutate
```

### **Developer Experience Flow**
```
Developer opens Backstage → Discovers service → Clicks dashboard link → Views Grafana → Debugs with metrics
```

---

## Production Considerations

### **Security Hardening**
- **TLS everywhere** — enable TLS for ArgoCD, Grafana, Backstage
- **RBAC** — restrict ArgoCD to specific namespaces
- **Network policies** — segment traffic between platform components
- **Pod security standards** — enforce baseline/restricted pod security
- **Secret management** — use external secret operators (ESO, Vault)

### **High Availability**
- **Multi-replica everything** — remove single points of failure
- **Anti-affinity** — spread replicas across nodes/zones
- **Persistent storage** — PVCs for Grafana dashboards, Prometheus metrics
- **Backup strategy** — etcd backups, ArgoCD application exports

### **Scalability**
- **Horizontal pod autoscaling** — scale based on CPU/memory metrics
- **Vertical pod autoscaling** — right-size resource requests/limits
- **Cluster autoscaling** — add nodes dynamically
- **Prometheus federation** — federate metrics across clusters

### **Operational Excellence**
- **SLIs/SLOs** — define and monitor service level objectives
- **Runbooks** — documented procedures for common incidents
- **Disaster recovery** — cross-region cluster replication
- **Cost optimization** — right-sizing, spot instances, cluster scaling

---

## Why This Architecture Matters

### **For Developers**
- **Self-service** — deploy, monitor, debug without waiting for DevOps
- **Consistency** — same tools, same workflows across all environments
- **Faster feedback** — immediate dashboard visibility on every deploy
- **Safety** — policies prevent common mistakes automatically

### **For DevOps/Platform Teams**
- **Reduced toil** — no manual deployments, no kubectl firefighting
- **Audit compliance** — every change is Git-tracked and policy-validated
- **Standardization** — same platform patterns across all teams
- **Scalability** — platform grows with teams, not DevOps headcount

### **For the Business**
- **Faster time-to-market** — developers ship features instead of managing infrastructure
- **Reduced risk** — automated policies prevent security and compliance violations
- **Cost optimization** — resource limits and monitoring prevent waste
- **Talent retention** — engineers prefer working with modern tooling

---

This IDP demonstrates enterprise-grade platform engineering practices that scale from startup to Fortune 500 — all running on a laptop for zero cloud cost.