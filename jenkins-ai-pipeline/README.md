# Jenkins AI Pipeline

A self-contained CI/CD stack where **Jenkins uses AWS Bedrock (Claude Haiku)**
to make the pipeline *intelligent*:

- **Before a build** — an AI **risk prediction** reads recent build history and
  posts a Slack warning if the next build looks risky.
- **After a failure** — an AI **failure analysis** reads the console log, explains
  the root cause + fix, and posts it to Slack.

AI calls go to **AWS Bedrock** (`anthropic.claude-haiku-20240307-v1:0`) — no
local GPU or model download required. AWS credentials must be configured before
starting the stack (see step 2 below).

```
Recent builds ─┐
               ├─> AWS Bedrock (Claude Haiku) ─> risk score ─> Slack warning   (pre-build)
Console log  ──┘                              ─> root cause  ─> Slack analysis (on failure)

Git ─> Jenkins ─> Build ─> Test ─> Docker build ─> Trivy ─> Push (localhost:5000)
```

---

## Layout

```
jenkins-ai-pipeline/
├── docker-compose.yml          # Jenkins + local registry
├── .env.example                # AWS credential template — copy to .env before starting
├── Jenkinsfile                 # AI-augmented pipeline
├── jenkins/
│   ├── Dockerfile              # Jenkins + Docker CLI + Python + Maven + Trivy
│   └── plugins.txt             # pre-installed plugins
├── scripts/
│   ├── predict_failure.py      # pre-build AI risk prediction
│   ├── analyse_failure.py      # on-failure AI root-cause analysis
│   └── requirements.txt
└── app/                        # sample Spring Boot service
    ├── pom.xml
    ├── Dockerfile
    └── src/...
```

---

## 1. Prerequisites

```bash
docker --version      # Docker installed
docker info           # daemon running
```

## 2. Configure AWS credentials

Follow these steps exactly — takes about 5 minutes.

### Step 1 — Create an IAM user

1. Open the [AWS Console](https://console.aws.amazon.com) and sign in.
2. Go to **IAM → Users → Create user**.
3. Enter a username, e.g. `jenkins-bedrock`.
4. On the **Set permissions** screen choose **Attach policies directly**.
5. Click **Create policy**, switch to the **JSON** tab, paste this:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": "bedrock:InvokeModel",
         "Resource": "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-20240307-v1:0"
       }
     ]
   }
   ```
6. Name the policy `BedrockInvokeClaudeHaiku` → **Create policy**.
7. Back on the user screen, attach the new policy → **Next → Create user**.

### Step 2 — Create an access key

1. Open the user you just created → **Security credentials** tab.
2. Scroll to **Access keys** → **Create access key**.
3. Choose **Application running outside AWS** → **Next → Create**.
4. **Copy both values now** — the secret is shown only once:
   - `Access key ID`
   - `Secret access key`

### Step 3 — Enable Claude Haiku in Bedrock

1. Go to **Amazon Bedrock** in the AWS Console (make sure you are in
   `us-east-1` or your chosen region — Bedrock is region-specific).
2. In the left menu click **Model access**.
3. Click **Modify model access** (top right).
4. Tick **Claude Haiku** under Anthropic → **Next → Submit**.
5. Wait for the status to change to **Access granted** (usually under 1 minute).

### Step 4 — Add credentials to your local .env

```bash
cp .env.example .env
```

Open `.env` and fill in the three values:

```
AWS_ACCESS_KEY_ID=AKIA...          # your access key ID
AWS_SECRET_ACCESS_KEY=abc123...    # your secret access key
AWS_REGION=us-east-1               # must match the region where you enabled Haiku
```

That's it — Docker Compose injects these into the Jenkins container automatically.

## 3. Start the stack

```bash
cd jenkins-ai-pipeline
docker compose up -d --build          # first build takes a few minutes
docker compose ps                     # jenkins, registry = running
```

## 4. Unlock Jenkins

```bash
docker exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword
```

Open http://localhost:8080, paste the password, **Install suggested plugins**,
and create your admin user. The pipeline-specific plugins are already baked into
the custom image (see [jenkins/plugins.txt](jenkins/plugins.txt)).

## 5. Add credentials (Manage Jenkins → Credentials → Global)

| Kind        | ID                  | Value                                             |
| ----------- | ------------------- | ------------------------------------------------- |
| Secret text | `slack-webhook-url` | Your Slack incoming webhook URL                   |
| Secret text | `jenkins-api-token` | A Jenkins API token (User → Configure → API Token)|

> The AI scripts read Jenkins build history/logs via the REST API (needs the
> token) and post results to Slack (needs the webhook). Both are injected as
> masked secrets — never hardcoded.

---

## 6. Create the pipeline job

1. **New Item** → name `ai-pipeline` → **Pipeline** → **OK**.
2. **Pipeline → Definition:** `Pipeline script from SCM` → **Git**.
3. **Repository URL:** this repo's URL (push it to GitHub, or use a local Git remote).
4. **Script Path:** `Jenkinsfile`.
5. **Save** → **Build Now**.

Initialize Git first if needed:

```bash
cd jenkins-ai-pipeline
git init && git add . && git commit -m "Jenkins AI pipeline"
```

---

## 7. Pipeline stages

| Stage                 | What it does                                              |
| --------------------- | -------------------------------------------------------- |
| **AI Risk Prediction**| Bedrock scores next-build risk → Slack warning if med/high|
| Force Failure         | Optional — only when `FORCE_FAILURE=true`                |
| Checkout / Build / Test | Standard Maven build + unit tests (JUnit published)    |
| Docker Build          | Builds the image tagged with the build number            |
| Trivy Scan            | Fails on HIGH/CRITICAL vulnerabilities                   |
| Push to Registry      | Pushes to `localhost:5000`                               |
| **post{failure}**     | Bedrock analyses the log → root cause + fix → Slack      |

---

## 7. Demo the AI failure analysis

1. **Build with Parameters** → check **FORCE_FAILURE** → **Build**.
2. The build fails on purpose.
3. `analyse_failure.py` fetches the log, asks AWS Bedrock (Claude Haiku) for a
   root-cause analysis, and posts a red **Build Failure Analysis** card to your
   Slack channel.

## 8. Verify the app image

After a green run:

```bash
curl http://localhost:5000/v2/_catalog                       # image is in the registry
docker run -d --name ai-app -p 8080:8080 localhost:5000/ai-pipeline-app:latest
curl 'http://localhost:8080/greet?name=Tejas'                # {"message":"Hello, Tejas!"}
curl http://localhost:8080/actuator/health                   # {"status":"UP"}
```

---

## Cleanup

```bash
docker rm -f ai-app          # stop the test container
docker compose down          # stop the stack
docker compose down -v        # full reset (also removes Jenkins/Ollama/registry data)
```

---

## How the AI scripts work

- **[scripts/predict_failure.py](scripts/predict_failure.py)** — pulls the last
  10 builds from the Jenkins API, sends them to AWS Bedrock (Claude Haiku) with
  a JSON-schema prompt, and posts a Slack warning for medium/high risk.
  Non-blocking (`|| true`).
- **[scripts/analyse_failure.py](scripts/analyse_failure.py)** — pulls the last
  100 console-log lines, asks Bedrock for `ROOT CAUSE / FIX / SEVERITY`, and
  posts a Slack Block Kit card.

Both use `anthropic.claude-haiku-20240307-v1:0`; change `BEDROCK_MODEL_ID` at
the top of each script to switch models. AWS region is read from the
`AWS_REGION` environment variable (default: `us-east-1`).

If Bedrock is unreachable (auth error, timeout, service unavailable), both
scripts catch the exception, post a fallback `"AI analysis unavailable"`
Slack message, and exit `0` — the Jenkins build result is never blocked by an
AI call failure.

## Troubleshooting

| Symptom | Fix |
| ------- | --- |
| `UnauthorizedClientException` from Bedrock | Check `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in `.env` and that the IAM policy grants `bedrock:InvokeModel`. |
| `AccessDeniedException: model access denied` | Enable Claude Haiku in the Bedrock console: **Bedrock → Model access → Anthropic → Claude Haiku → Request access**. |
| `EndpointResolutionError` | Check `AWS_REGION` is set to a region where Bedrock is available (e.g. `us-east-1`). |
| AI step posts "unavailable" Slack message | See Jenkins console log for the full exception — Bedrock failures are non-blocking. |
| `docker: not found` in pipeline | Rebuild the Jenkins image: `docker compose build --no-cache jenkins`. |
| Slack step fails | Check the `slack-webhook-url` credential and that the webhook is active. |
| Jenkins API 401 | Regenerate the `jenkins-api-token` and confirm `JENKINS_API_USER`. |
