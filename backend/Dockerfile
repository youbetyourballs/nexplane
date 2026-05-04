# Stage 1 — Build agent binaries
FROM golang:1.26-alpine AS agent-builder
WORKDIR /agent
COPY agent/ .
COPY VERSION /VERSION
RUN go mod download
RUN export BUILD_VERSION=$(tr -d '\r' < /VERSION) && \
    GOOS=linux   GOARCH=amd64  go build -ldflags="-s -w -X main.Version=${BUILD_VERSION}" \
        -o dist/nexplane-agent-linux-amd64-${BUILD_VERSION}     ./ && \
    GOOS=linux   GOARCH=arm64  go build -ldflags="-s -w -X main.Version=${BUILD_VERSION}" \
        -o dist/nexplane-agent-linux-arm64-${BUILD_VERSION}     ./ && \
    GOOS=windows GOARCH=amd64  go build -ldflags="-s -w -X main.Version=${BUILD_VERSION}" \
        -o dist/nexplane-agent-windows-amd64-${BUILD_VERSION}.exe ./
RUN export BUILD_VERSION=$(tr -d '\r' < /VERSION) && \
    sha256sum dist/nexplane-agent-linux-amd64-${BUILD_VERSION}       | tee dist/nexplane-agent-linux-amd64-${BUILD_VERSION}.sha256 && \
    sha256sum dist/nexplane-agent-linux-arm64-${BUILD_VERSION}       | tee dist/nexplane-agent-linux-arm64-${BUILD_VERSION}.sha256 && \
    sha256sum dist/nexplane-agent-windows-amd64-${BUILD_VERSION}.exe | tee dist/nexplane-agent-windows-amd64-${BUILD_VERSION}.exe.sha256
RUN tr -d '\r' < /VERSION > dist/version

# Stage 2 — Python backend
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# Install curl, unzip, iproute2, iptables (needed for Tailscale + Terraform)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip iproute2 iptables \
    && rm -rf /var/lib/apt/lists/*

# Install Tailscale via official install script (handles OS detection automatically)
RUN curl -fsSL https://tailscale.com/install.sh | sh

# Install Terraform CLI for local IaC smoke tests
RUN curl -fsSL https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_linux_amd64.zip -o /tmp/tf.zip \
    && unzip /tmp/tf.zip -d /usr/local/bin/ \
    && rm /tmp/tf.zip \
    && chmod +x /usr/local/bin/terraform

# Install Ansible + AWS collections for local Ansible smoke tests
RUN pip install --no-cache-dir ansible boto3 botocore \
    && ansible-galaxy collection install amazon.aws community.aws --timeout 120

# Install AWS Session Manager Plugin (required by community.aws.aws_ssm Ansible connection)
RUN curl -fsSL https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb \
        -o /tmp/smp.deb \
    && dpkg -i /tmp/smp.deb \
    && rm /tmp/smp.deb

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=agent-builder /agent/dist/ /opt/nexplane-downloads/

RUN curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

EXPOSE 8000
