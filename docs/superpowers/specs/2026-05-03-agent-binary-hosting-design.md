# Agent Binary Hosting — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** Compile the Nexplane Agent Go binaries during the Docker image build and serve them as unauthenticated static files from the backend at `/downloads/`. Update the Settings page deploy panel to show real `curl`/`Invoke-WebRequest` download one-liners.

---

## Background

The agent deploy instructions panel in Settings currently tells operators to build from source (`cd agent && make build`). This blocks operators who don't have Go installed or access to the source tree. The binaries should be available directly from the control plane so a single `curl` command can download and run the agent on any managed machine.

---

## Design Decisions

- **Multi-stage Docker build** — a Go builder stage compiles all three targets (`linux-amd64`, `linux-arm64`, `windows-amd64.exe`) and writes SHA256 checksums. The final Python stage copies only the compiled artifacts — no Go toolchain in the final image.
- **Unauthenticated static files** — FastAPI mounts `/downloads/` as `StaticFiles`. No auth required so `curl` works without a token on managed machines that haven't registered yet.
- **Version file** — a `version.txt` is written during build containing the git describe tag (e.g. `v0.1.0-abc1234`). Served as `/downloads/version` for future self-update support.
- **SHA256 checksums** — each binary gets a `.sha256` sidecar file, also served statically. Enables verification in the curl one-liner and future self-update integrity checks.
- **Override URL** — the frontend derives the download base URL from `window.location.origin` by default. If binaries are hosted elsewhere (S3, CDN), an operator can set `VITE_AGENT_DOWNLOAD_URL` in docker-compose to override.
- **No runtime changes** — the backend serves static files; no new API routes, no database changes.

---

## Section 1: Dockerfile Changes

The `backend/Dockerfile` gains a Go builder stage before the Python stage:

```dockerfile
# Stage 1: Build agent binaries
FROM golang:1.22-alpine AS agent-builder
WORKDIR /agent
COPY agent/ .
RUN go mod download
RUN GOOS=linux  GOARCH=amd64 go build -ldflags="-s -w" -o dist/nexplane-agent-linux-amd64   ./
RUN GOOS=linux  GOARCH=arm64 go build -ldflags="-s -w" -o dist/nexplane-agent-linux-arm64   ./
RUN GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o dist/nexplane-agent-windows-amd64.exe ./
RUN sha256sum dist/nexplane-agent-linux-amd64   > dist/nexplane-agent-linux-amd64.sha256
RUN sha256sum dist/nexplane-agent-linux-arm64   > dist/nexplane-agent-linux-arm64.sha256
RUN sha256sum dist/nexplane-agent-windows-amd64.exe > dist/nexplane-agent-windows-amd64.exe.sha256
RUN git describe --tags --always --dirty 2>/dev/null | tee dist/version.txt || echo "dev" > dist/version.txt

# Stage 2: Python backend (existing)
FROM python:3.12-slim
...
COPY --from=agent-builder /agent/dist/ /app/downloads/
```

**Note:** The `git describe` command requires git history. The docker build context must include `.git/` or the version falls back to `"dev"`. In `docker-compose.yml`, `context: .` already includes `.git/`.

---

## Section 2: FastAPI Static Serving

In `backend/app/main.py`, mount the downloads directory after app creation:

```python
from fastapi.staticfiles import StaticFiles
import pathlib

downloads_dir = pathlib.Path("/app/downloads")
if downloads_dir.exists():
    app.mount("/downloads", StaticFiles(directory=str(downloads_dir)), name="downloads")
```

Files served:
- `GET /downloads/nexplane-agent-linux-amd64` — Linux x86_64 binary
- `GET /downloads/nexplane-agent-linux-arm64` — Linux ARM64 binary
- `GET /downloads/nexplane-agent-windows-amd64.exe` — Windows binary
- `GET /downloads/nexplane-agent-linux-amd64.sha256` — checksum
- `GET /downloads/nexplane-agent-linux-arm64.sha256` — checksum
- `GET /downloads/nexplane-agent-windows-amd64.exe.sha256` — checksum
- `GET /downloads/version` — plain text version string

No authentication. CORS is not an issue since these are same-origin requests from the browser or direct `curl` commands.

---

## Section 3: Frontend — Download One-Liners

In `frontend/src/pages/Settings.tsx`, the deploy panel currently shows a build-from-source note. Replace it with real download commands.

The base URL is:
```typescript
const downloadBase = import.meta.env.VITE_AGENT_DOWNLOAD_URL || window.location.origin;
```

**Linux curl commands replace the build note:**

```bash
# Download (amd64)
curl -fsSL ${downloadBase}/downloads/nexplane-agent-linux-amd64 -o nexplane-agent
chmod +x nexplane-agent

# Verify checksum
curl -fsSL ${downloadBase}/downloads/nexplane-agent-linux-amd64.sha256 | sha256sum -c
```

The ephemeral and service run commands remain as-is (they already use the binary name `nexplane-agent`).

**Windows PowerShell download:**

```powershell
Invoke-WebRequest -Uri "${downloadBase}/downloads/nexplane-agent-windows-amd64.exe" -OutFile nexplane-agent.exe
```

**ARM64 tab** — add a third platform option "🐧 Linux (ARM64)" that uses `nexplane-agent-linux-arm64`.

---

## Section 4: docker-compose.yml

Add `VITE_AGENT_DOWNLOAD_URL` to the frontend environment with an empty default (falls back to `window.location.origin`):

```yaml
frontend:
  environment:
    VITE_API_URL: http://localhost:8000
    VITE_AGENT_DOWNLOAD_URL: ""
```

No change needed to the backend service — `/downloads/` is served by the backend which is already reachable.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/Dockerfile` | Add Go builder stage; copy binaries + checksums + version.txt into `/app/downloads/` |
| `backend/app/main.py` | Mount `/app/downloads/` as `StaticFiles` at `/downloads` |
| `frontend/src/pages/Settings.tsx` | Replace build-from-source note with curl/PowerShell download one-liners; add ARM64 platform tab |
| `docker-compose.yml` | Add `VITE_AGENT_DOWNLOAD_URL` env var to frontend service |
