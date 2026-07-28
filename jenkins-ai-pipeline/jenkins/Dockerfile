# Custom Jenkins controller image for the AI pipeline:
#   - Docker CLI    → build/push images via the mounted host socket
#   - Python 3 + pip → run the Ollama AI scripts (predict / analyse)
#   - Trivy          → image vulnerability scanning
#   - Pipeline plugins baked in
FROM jenkins/jenkins:lts-jdk17

USER root

# --- Docker CLI ---
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        apt-transport-https ca-certificates curl gnupg lsb-release \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/debian $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# --- Python 3 + requests (AI scripts) + Maven + Trivy ---
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv maven \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && ln -sf /usr/bin/pip3    /usr/local/bin/pip \
    && pip3 install --no-cache-dir --break-system-packages requests boto3 \
    && curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh \
        | sh -s -- -b /usr/local/bin \
    && rm -rf /var/lib/apt/lists/*

# --- Pipeline plugins ---
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

# Stay as root so Jenkins can use the mounted Docker socket on any host
# (simplest setup for a local POC).
