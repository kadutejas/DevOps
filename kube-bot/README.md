## kube-bot — Kubernetes Pod Monitor + Slack Alerts

Watches pods across your microservice cluster, detects issues, and posts **fix suggestions** to Slack.

---

### How it works

```
Kubernetes API  ──►  k8s_watcher.py  ──►  rules.py (your rules)
                                    ──►  alert_manager.py (cooldown / dedup)
                                    ──►  slack_notifier.py  ──►  Slack channel
```

| Detected issue | Example alert trigger |
|---|---|
| `CrashLoopBackOff` | App keeps crashing on startup |
| `OOMKilled` | Container exceeds memory limit |
| `ImagePullBackOff` / `ErrImagePull` | Wrong image name or missing pull secret |
| `CreateContainerConfigError` | Missing ConfigMap or Secret |
| `Evicted` | Node under disk/memory pressure |
| `Failed` / `Unknown` | Pod phase failure |
| and more … | (see `config/rules.yaml`) |

Recovery is also detected — a ✅ message is sent when a pod returns healthy.

---

### Project layout

```
kube-bot/
├── src/
│   ├── main.py            ← entry point, reads env vars
│   ├── k8s_watcher.py     ← connects to K8s, streams pod events
│   ├── alert_manager.py   ← deduplication / cooldown logic
│   ├── slack_notifier.py  ← formats and posts Slack messages
│   └── rules.py           ← loads your fix suggestions from rules.yaml
├── config/
│   └── rules.yaml         ← ✏️  EDIT THIS to add your custom suggestions
├── k8s/
│   ├── namespace.yaml
│   ├── rbac.yaml          ← ServiceAccount + ClusterRole (minimal permissions)
│   ├── secret.yaml        ← template for your Slack token
│   ├── configmap.yaml     ← rules.yaml mounted into the pod
│   └── deployment.yaml    ← the bot deployment
├── Dockerfile
├── docker-compose.yml     ← local dev
├── requirements.txt
└── .env.example
```

---

### Quick start

#### 1 — Build your private image

```bash
# Replace with your own registry (no public image is used)
docker build -t your-registry/kube-bot:latest .
docker push your-registry/kube-bot:latest
```

Update `image:` in `k8s/deployment.yaml` to match.

#### 2 — Create the namespace

```bash
kubectl apply -f k8s/namespace.yaml
```

#### 3 — Apply RBAC

```bash
kubectl apply -f k8s/rbac.yaml
```

#### 4 — Create the Slack secret

```bash
kubectl create secret generic kube-bot-secrets \
  --from-literal=SLACK_BOT_TOKEN=xoxb-your-token-here \
  -n kube-bot
```

> Do **not** commit real tokens to source control. The `k8s/secret.yaml` file is a template only.

#### 5 — Apply the ConfigMap (rules) and deploy

```bash
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/deployment.yaml
```

#### 6 — Verify

```bash
kubectl get pods -n kube-bot
kubectl logs -f deployment/kube-bot -n kube-bot
```

---

### Adding your own fix suggestions

Edit `config/rules.yaml` (locally) or the embedded YAML in `k8s/configmap.yaml`:

```yaml
rules:

  # Your microservice-specific rule
  MyServiceCrash:
    severity: critical
    suggestions:
      - "Check the DB connection: kubectl logs {pod_name} -n {namespace} | grep 'connection refused'"
      - "Verify Secret my-service-db exists: kubectl get secret my-service-db -n {namespace}"
      - "kubectl describe pod {pod_name} -n {namespace}"
```

After editing the ConfigMap:

```bash
kubectl apply -f k8s/configmap.yaml
kubectl rollout restart deployment/kube-bot -n kube-bot
```

Available placeholders in suggestions: `{pod_name}` and `{namespace}`.

---

### Configuration reference

| Environment variable | Default | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | *(required)* | Slack Bot OAuth token (`xoxb-…`) |
| `SLACK_CHANNEL` | `#kubernetes-alerts` | Channel to post in |
| `WATCH_NAMESPACE` | *(empty = all)* | Single namespace to watch |
| `IGNORED_NAMESPACES` | `kube-system,kube-public,kube-node-lease` | Namespaces to skip |
| `ALERT_COOLDOWN_SECONDS` | `1800` | Seconds before re-alerting the same pod |
| `RULES_CONFIG_PATH` | `/config/rules.yaml` | Path to the rules file |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

---

### Local development

```bash
cp .env.example .env     # add your SLACK_BOT_TOKEN
docker compose up --build
```

This mounts `~/.kube/config` and `./config/rules.yaml` into the container so you can edit rules live.

---

### Getting a Slack Bot Token

1. Go to <https://api.slack.com/apps> → **Create New App**
2. **OAuth & Permissions** → Add Bot Token Scopes: `chat:write`, `chat:write.public`
3. **Install App to Workspace**
4. Copy the **Bot User OAuth Token** (`xoxb-…`)
5. Invite the bot to your channel: `/invite @kube-bot`
