# Stage 1 — Build agent binaries
FROM golang:1.26-alpine AS agent-builder
WORKDIR /agent
COPY agent/ .
RUN go mod download
RUN GOOS=linux   GOARCH=amd64  go build -ldflags="-s -w" -o dist/nexplane-agent-linux-amd64     ./
RUN GOOS=linux   GOARCH=arm64  go build -ldflags="-s -w" -o dist/nexplane-agent-linux-arm64     ./
RUN GOOS=windows GOARCH=amd64  go build -ldflags="-s -w" -o dist/nexplane-agent-windows-amd64.exe ./
RUN sha256sum dist/nexplane-agent-linux-amd64       | tee dist/nexplane-agent-linux-amd64.sha256
RUN sha256sum dist/nexplane-agent-linux-arm64       | tee dist/nexplane-agent-linux-arm64.sha256
RUN sha256sum dist/nexplane-agent-windows-amd64.exe | tee dist/nexplane-agent-windows-amd64.exe.sha256
RUN printf "dev" > dist/version.txt

# Stage 2 — Python backend
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=agent-builder /agent/dist/ /app/downloads/

EXPOSE 8000
