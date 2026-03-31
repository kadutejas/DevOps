# IDP Platform Architecture

> **Visual representation of the Internal Developer Platform components and their interactions**

## Architecture Diagram

```mermaid
graph TB
    subgraph "Developer Experience Layer"
        DEV[👩‍💻 Developer]
        BACKSTAGE[🏠 Backstage Portal<br/>localhost:30002]
        GIT[📁 GitHub Repository<br/>idp-platform-local]
    end
    
    subgraph "GitOps Engine"
        ARGOCD[🚀 ArgoCD<br/>localhost:30000]
        APPOFAPPS[📋 App-of-Apps]
        APPS[📦 Applications<br/>• monitoring<br/>• backstage<br/>• kyverno]
    end
    
    subgraph "Policy & Security Layer"
        KYVERNO[🛡️ Kyverno Engine]
        POLICIES[📜 Policies<br/>• block-latest-tag<br/>• require-limits<br/>• block-privileged<br/>• add-labels<br/>• block-root]
    end
    
    subgraph "Observability Stack"
        PROMETHEUS[📊 Prometheus<br/>Metrics Collection]
        GRAFANA[📈 Grafana<br/>localhost:30001]
        KUBESTATE[📋 Kube-State-Metrics]
        NODEEXP[🖥️ Node Exporter]
    end
    
    subgraph "Kubernetes Cluster (kind)"
        CONTROL[⚙️ Control Plane]
        WORKER1[🔧 Worker Node 1]
        WORKER2[🔧 Worker Node 2]
        API[🔌 Kubernetes API]
    end
    
    subgraph "Workloads & Resources"
        PODS[📦 Application Pods]
        SERVICES[🔗 Services]
        SECRETS[🔐 Secrets]
        CONFIGS[⚙️ ConfigMaps]
    end
    
    %% Developer Flow
    DEV -->|1. Browse Services| BACKSTAGE
    DEV -->|2. Commit Changes| GIT
    BACKSTAGE -->|Links to Dashboards| GRAFANA
    
    %% GitOps Flow
    GIT -->|3. Webhook/Poll| ARGOCD
    ARGOCD -->|Manages| APPOFAPPS
    APPOFAPPS -->|Deploys| APPS
    APPS -->|Creates/Updates| API
    
    %% Policy Enforcement
    API -->|Intercepts Resources| KYVERNO
    KYVERNO -->|Applies| POLICIES
    POLICIES -->|Validates/Mutates| PODS
    POLICIES -->|Validates/Mutates| SERVICES
    
    %% Observability Flow
    PODS -->|Expose /metrics| PROMETHEUS
    WORKER1 -->|Host Metrics| NODEEXP
    WORKER2 -->|Host Metrics| NODEEXP
    API -->|Cluster State| KUBESTATE
    NODEEXP -->|Scrape| PROMETHEUS
    KUBESTATE -->|Scrape| PROMETHEUS
    PROMETHEUS -->|Query| GRAFANA
    
    %% Deployment to Nodes
    API -->|Schedules| CONTROL
    CONTROL -->|Distributes| WORKER1
    CONTROL -->|Distributes| WORKER2
    
    %% Components on Nodes
    WORKER1 -.->|Hosts| PODS
    WORKER2 -.->|Hosts| PODS
    WORKER1 -.->|Hosts| SERVICES
    WORKER2 -.->|Hosts| SERVICES
```

## Data Flow Patterns

### 1. **Developer Workflow**
```mermaid
sequenceDiagram
    participant Developer
    participant Backstage
    participant GitHub
    participant ArgoCD
    participant Kubernetes
    
    Developer->>Backstage: 1. Browse services
    Developer->>GitHub: 2. Commit changes
    GitHub->>ArgoCD: 3. Webhook notification
    ArgoCD->>Kubernetes: 4. Apply manifests
    Kubernetes->>Backstage: 5. Service update
    Backstage->>Developer: 6. View updated service
```

### 2. **GitOps Deployment Flow**
```mermaid
sequenceDiagram
    participant Git as GitHub Repo
    participant ArgoCD
    participant K8s as Kubernetes API
    participant Kyverno
    participant Workload
    
    Git->>ArgoCD: 1. Config updated
    ArgoCD->>K8s: 2. Apply manifests
    K8s->>Kyverno: 3. Admission webhook
    Kyverno->>K8s: 4. Policy validation
    K8s->>Workload: 5. Create/update pods
    ArgoCD->>Git: 6. Sync status update
```

### 3. **Observability Data Flow**
```mermaid
sequenceDiagram
    participant Workload
    participant Prometheus
    participant Grafana
    participant Developer
    
    Workload->>Prometheus: 1. Expose /metrics
    Prometheus->>Prometheus: 2. Scrape & store
    Grafana->>Prometheus: 3. Query metrics
    Developer->>Grafana: 4. View dashboards
    Grafana->>Developer: 5. Alert notifications
```

## Component Integration Matrix

| Component    | Depends On              | Provides To             | Communication Method |
|--------------|-------------------------|-------------------------|---------------------|
| **kind**     | Docker                  | All components          | Kubernetes API      |
| **ArgoCD**   | Kubernetes, Git         | All deployments         | kubectl, Git API    |
| **Prometheus** | Kubernetes            | Grafana                 | HTTP scraping       |
| **Grafana**  | Prometheus              | Developers              | HTTP queries        |
| **Backstage** | Kubernetes, Git        | Developers              | REST APIs           |
| **Kyverno**  | Kubernetes              | All workloads           | Admission webhooks  |

## Network Architecture

### Port Mappings
- **30000** → ArgoCD UI (admin interface)
- **30001** → Grafana UI (metrics visualization)
- **30002** → Backstage UI (developer portal)

### Internal Service Communication
- **ArgoCD** communicates with GitHub via polling (30s interval)
- **Prometheus** scrapes metrics from all pods with `/metrics` endpoint
- **Kyverno** intercepts API calls via admission controllers
- **Backstage** discovers services via Kubernetes API

### Data Persistence
- **Prometheus**: Ephemeral (7-day retention, no PVC)
- **Grafana**: Ephemeral (dashboards from ConfigMaps)
- **ArgoCD**: Ephemeral (Git as source of truth)
- **Backstage**: Ephemeral (bundled PostgreSQL, reset on restart)

## Security Architecture

### Authentication & Authorization
```mermaid
graph LR
    subgraph "Local Development"
        A[ArgoCD - admin/password]
        G[Grafana - admin/idp-grafana-admin]
        B[Backstage - Guest auth]
    end
    
    subgraph "Production Considerations"
        OIDC[OIDC Integration]
        RBAC[Kubernetes RBAC]
        TLS[TLS Certificates]
        NP[Network Policies]
    end
```

### Policy Enforcement Points
1. **Admission Control** → Kyverno policies at Kubernetes API
2. **Resource Validation** → Required labels, limits, security contexts
3. **Mutation** → Auto-add labels and default configurations
4. **Audit** → Policy violation logging and reporting

## Scaling Considerations

### Horizontal Scaling
- **ArgoCD**: Multi-replica for HA
- **Prometheus**: Federation for multi-cluster
- **Grafana**: LoadBalancer with shared storage
- **Kyverno**: Multiple admission controllers

### Vertical Scaling
- **Memory**: Increase for large clusters (>100 nodes)
- **CPU**: Scale based on workload deployment frequency
- **Storage**: persistent volumes for long-term metric retention

### Multi-Environment Strategy
```
Development (kind) → Staging (EKS/GKE) → Production (Multi-AZ)
     ↓                    ↓                    ↓
Single-node          3-node cluster      Multi-zone cluster
Ephemeral storage    Short-term PVCs     Long-term storage
Guest auth           Basic RBAC          Full OIDC + RBAC
```

## Disaster Recovery

### Backup Strategy
- **Git repository** → Source of truth (GitHub backup/mirror)
- **Prometheus data** → Export/snapshot for long-term storage
- **Grafana dashboards** → Stored as code in ConfigMaps
- **Kyverno policies** → Git-managed ClusterPolicy YAML

### Recovery Procedures
1. **Cluster failure**: `kind delete cluster && make bootstrap`
2. **ArgoCD failure**: Redeploy via Helm, sync restores all apps
3. **Data loss**: Git provides declarative restoration
4. **Partial failure**: ArgoCD self-heal restores drift

---

This architecture demonstrates production-ready platform engineering patterns that scale from local development to enterprise deployments.