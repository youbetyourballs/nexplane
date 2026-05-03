# Agent Binary Hosting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile the Nexplane Agent Go binaries during the Docker image build and serve them as unauthenticated static files from the backend at `/downloads/`, then update the Settings deploy panel to show real curl/PowerShell download one-liners.

**Architecture:** A Go builder stage is added to `backend/Dockerfile` that cross-compiles three agent binaries and writes SHA256 checksums and a version file into `/app/downloads/`. FastAPI mounts that directory as `StaticFiles` at `/downloads`. The Settings page deploy panel replaces its build-from-source notes with download commands derived from the backend URL, and adds a Linux ARM64 tab.

**Tech Stack:** Docker multi-stage build, Go 1.22, FastAPI `StaticFiles`, React/TypeScript

---

## File Map

**Modified:**
- `backend/Dockerfile` — add Go builder stage; copy binaries + checksums + version.txt
- `backend/app/main.py` — mount `/app/downloads/` as `StaticFiles` at `/downloads`
- `docker-compose.yml` — add `VITE_AGENT_DOWNLOAD_URL` to frontend env
- `frontend/src/pages/Settings.tsx` — replace build-from-source notes with download commands; add ARM64 tab

**Note:** No new files — everything is either modifications to existing files or artifacts produced during the Docker build.

---

### Task 1: Multi-stage Dockerfile — build agent binaries

**Files:**
- Modify: `backend/Dockerfile`

The current Dockerfile is a single stage that copies the backend Python source. We prefix it with a Go builder stage that cross-compiles the agent and produces the download artifacts.

- [ ] **Step 1: Replace `backend/Dockerfile` with this content**

```dockerfile
# Stage 1 — Build agent binaries
FROM golang:1.22-alpine AS agent-builder
WORKDIR /agent
COPY ../agent/ .
RUN go mod download
RUN GOOS=linux  GOARCH=amd64  go build -ldflags="-s -w" -o dist/nexplane-agent-linux-amd64     ./
RUN GOOS=linux  GOARCH=arm64  go build -ldflags="-s -w" -o dist/nexplane-agent-linux-arm64     ./
RUN GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o dist/nexplane-agent-windows-amd64.exe ./
RUN sha256sum dist/nexplane-agent-linux-amd64       | tee dist/nexplane-agent-linux-amd64.sha256
RUN sha256sum dist/nexplane-agent-linux-arm64       | tee dist/nexplane-agent-linux-arm64.sha256
RUN sha256sum dist/nexplane-agent-windows-amd64.exe | tee dist/nexplane-agent-windows-amd64.exe.sha256
RUN git describe --tags --always --dirty 2>/dev/null | tee dist/version.txt || echo "dev" > dist/version.txt

# Stage 2 — Python backend
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=agent-builder /agent/dist/ /app/downloads/

EXPOSE 8000
```

**Important:** The `COPY ../agent/ .` in the builder stage requires the Docker build context to be the repo root, not `./backend`. The `docker-compose.yml` build context must change from `./backend` to `.` (the repo root) and the Dockerfile path adjusted accordingly.

- [ ] **Step 2: Update `docker-compose.yml` backend build context**

Find the backend service build config:
```yaml
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
```

Replace with:
```yaml
  backend:
    build:
      context: .
      dockerfile: backend/Dockerfile
```

Also update the volumes mount — the `./backend` volume will need to become `./backend:/app` but referencing the repo-root context. Verify the existing volumes entry:
```yaml
    volumes:
      - ./backend:/app
```
This stays the same — it's a runtime mount, not a build-time path.

- [ ] **Step 3: Verify the build works**

```bash
cd f:/Nexplane/nexplane
docker compose build backend 2>&1 | tail -20
```
Expected: `Image nexplane-backend Built` — with Go compilation output visible for all three targets before the Python stage.

If the build fails with `COPY ../agent/` path errors, the build context change in Step 2 is required — verify it was applied.

- [ ] **Step 4: Verify binaries are in the image**

```bash
docker compose run --rm backend ls /app/downloads/
```
Expected output:
```
nexplane-agent-linux-amd64
nexplane-agent-linux-amd64.sha256
nexplane-agent-linux-arm64
nexplane-agent-linux-arm64.sha256
nexplane-agent-windows-amd64.exe
nexplane-agent-windows-amd64.exe.sha256
version.txt
```

- [ ] **Step 5: Commit**

```bash
cd f:/Nexplane/nexplane
git add backend/Dockerfile docker-compose.yml
git commit -m "feat: add Go builder stage to backend Dockerfile — compiles agent binaries and SHA256 checksums into /app/downloads/"
```

---

### Task 2: FastAPI static file serving at /downloads

**Files:**
- Modify: `backend/app/main.py`

FastAPI's `StaticFiles` serves a directory of files at a given mount path. The mount is unauthenticated — no token needed — so `curl` works directly.

- [ ] **Step 1: Add StaticFiles mount to `backend/app/main.py`**

Add the import at the top of the file (after the existing imports):
```python
from fastapi.staticfiles import StaticFiles
import pathlib
```

Add the mount immediately after the CORS middleware block (after `app.add_middleware(...)`):
```python
_downloads_dir = pathlib.Path("/app/downloads")
if _downloads_dir.exists():
    app.mount("/downloads", StaticFiles(directory=str(_downloads_dir)), name="downloads")
```

The `if _downloads_dir.exists()` guard means the app starts cleanly in local dev without Docker (where `/app/downloads/` doesn't exist).

- [ ] **Step 2: Restart backend and verify the endpoint**

```bash
docker compose up backend -d
```

Wait for startup, then:
```bash
curl -I http://localhost:8000/downloads/version
```
Expected: `HTTP/1.1 200 OK` with `content-type: text/plain` (or similar).

```bash
curl http://localhost:8000/downloads/version
```
Expected: a version string like `dev` or `v0.1.0-abc1234`.

```bash
curl -I http://localhost:8000/downloads/nexplane-agent-linux-amd64
```
Expected: `HTTP/1.1 200 OK` with `content-type: application/octet-stream`.

- [ ] **Step 3: Verify download actually works**

```bash
curl -fL http://localhost:8000/downloads/nexplane-agent-linux-amd64 -o /tmp/test-agent
ls -lh /tmp/test-agent
```
Expected: file present, size > 5MB (Go binaries are typically 8-15MB after `-ldflags="-s -w"`).

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane
git add backend/app/main.py
git commit -m "feat: mount /app/downloads/ as unauthenticated StaticFiles at /downloads in FastAPI"
```

---

### Task 3: Frontend — download commands and ARM64 tab

**Files:**
- Modify: `docker-compose.yml` — add `VITE_AGENT_DOWNLOAD_URL`
- Modify: `frontend/src/pages/Settings.tsx` — update deploy panel

The current deploy panel shows "Build the binary first: `cd agent && make build`" before each platform's commands. Replace that with a download block, add the ARM64 Linux tab, and wire the download base URL from an env var with `window.location.origin` fallback.

- [ ] **Step 1: Add VITE_AGENT_DOWNLOAD_URL to docker-compose.yml**

Find the frontend environment block:
```yaml
    environment:
      VITE_API_URL: http://localhost:8000
```

Replace with:
```yaml
    environment:
      VITE_API_URL: http://localhost:8000
      VITE_AGENT_DOWNLOAD_URL: ""
```

The empty string means the frontend falls back to `window.location.origin` at runtime — pointing to the backend at port 8000 in dev.

- [ ] **Step 2: Update the platform state type in Settings.tsx**

Find:
```typescript
  const [agentPlatform, setAgentPlatform] = useState<"linux" | "windows">("linux");
```

Replace with:
```typescript
  const [agentPlatform, setAgentPlatform] = useState<"linux" | "linux-arm64" | "windows">("linux");
```

- [ ] **Step 3: Replace the deploy panel content in Settings.tsx**

Find the entire IIFE block starting with:
```tsx
        {(settings?.agent_configured || generatedSecret) && (() => {
          const secret = generatedSecret ?? "<YOUR-SECRET>";
          const controlPlane = window.location.origin;
          const linuxEphemeral = ...
```

Replace the entire block (everything through the final `})()}`) with:

```tsx
        {(settings?.agent_configured || generatedSecret) && (() => {
          const secret = generatedSecret ?? "<YOUR-SECRET>";
          const controlPlane = window.location.origin;
          const downloadBase = (import.meta.env.VITE_AGENT_DOWNLOAD_URL as string) || controlPlane;

          const binaryName = agentPlatform === "windows"
            ? "nexplane-agent-windows-amd64.exe"
            : agentPlatform === "linux-arm64"
            ? "nexplane-agent-linux-arm64"
            : "nexplane-agent-linux-amd64";

          const agentBin = agentPlatform === "windows" ? "nexplane-agent.exe" : "nexplane-agent";

          const linuxDownload = `curl -fsSL ${downloadBase}/downloads/${binaryName} -o ${agentBin} && chmod +x ${agentBin}`;
          const linuxVerify = `curl -fsSL ${downloadBase}/downloads/${binaryName}.sha256 | sha256sum -c`;
          const linuxEphemeral = `./${agentBin} \\\n  --control-plane ${controlPlane} \\\n  --secret ${secret} \\\n  --mode ephemeral`;
          const linuxService = `sudo ./${agentBin} \\\n  --control-plane ${controlPlane} \\\n  --secret ${secret} \\\n  --mode service \\\n  --poll-interval 30s`;
          const linuxSystemd = `[Unit]\nDescription=Nexplane Agent\nAfter=network.target\n\n[Service]\nExecStart=/usr/local/bin/nexplane-agent \\\n  --control-plane ${controlPlane} \\\n  --secret ${secret} \\\n  --mode service \\\n  --poll-interval 30s\nRestart=on-failure\n\n[Install]\nWantedBy=multi-user.target`;
          const winDownload = `Invoke-WebRequest -Uri "${downloadBase}/downloads/${binaryName}" -OutFile ${agentBin}`;
          const winEphemeral = `.\\${agentBin} \`\n  --control-plane ${controlPlane} \`\n  --secret ${secret} \`\n  --mode ephemeral`;
          const winService = `New-Service -Name "NexplaneAgent" \`\n  -BinaryPathName "C:\\nexplane\\nexplane-agent.exe --mode service --poll-interval 30s --control-plane ${controlPlane} --secret ${secret}" \`\n  -StartupType Automatic\nStart-Service NexplaneAgent`;

          const CmdBlock = ({ id, label, value }: { id: string; label: string; value: string }) => (
            <div className="mt-2">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-slate-500">{label}</span>
                <button
                  onClick={() => copyCmd(id, value)}
                  className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-700"
                >
                  {copiedCmd === id ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                  {copiedCmd === id ? "Copied" : "Copy"}
                </button>
              </div>
              <pre className="bg-slate-950 text-slate-100 text-xs rounded p-3 overflow-x-auto whitespace-pre">{value}</pre>
            </div>
          );

          return (
            <div className="mt-4 border-t border-slate-100 pt-4">
              <h3 className="text-xs font-semibold text-slate-700 mb-3">Deploy Agent</h3>
              {!generatedSecret && (
                <p className="text-xs text-slate-400 mb-3">
                  Replace <code className="font-mono bg-slate-100 px-1 rounded">&lt;YOUR-SECRET&gt;</code> with the secret from when you generated it. Rotate to get a new one.
                </p>
              )}
              <div className="flex gap-2 mb-3">
                {([
                  { id: "linux",      label: "🐧 Linux (x86_64)" },
                  { id: "linux-arm64", label: "🐧 Linux (ARM64)" },
                  { id: "windows",   label: "🪟 Windows" },
                ] as const).map((p) => (
                  <button
                    key={p.id}
                    onClick={() => setAgentPlatform(p.id)}
                    className={`px-3 py-1 text-xs rounded-md border transition-colors ${
                      agentPlatform === p.id
                        ? "bg-slate-900 text-white border-slate-900"
                        : "border-slate-200 text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              {agentPlatform !== "windows" ? (
                <>
                  <CmdBlock id="linux-download" label="1. Download binary" value={linuxDownload} />
                  <CmdBlock id="linux-verify"   label="2. Verify checksum" value={linuxVerify} />
                  <CmdBlock id="linux-ephemeral" label="3. Run once (ephemeral)" value={linuxEphemeral} />
                  <CmdBlock id="linux-service"   label="Run as foreground service" value={linuxService} />
                  <CmdBlock id="linux-systemd"   label="systemd unit (save to /etc/systemd/system/nexplane-agent.service)" value={linuxSystemd} />
                </>
              ) : (
                <>
                  <CmdBlock id="win-download"  label="1. Download binary (PowerShell)" value={winDownload} />
                  <CmdBlock id="win-ephemeral" label="2. Run once (ephemeral)" value={winEphemeral} />
                  <CmdBlock id="win-service"   label="Install as Windows Service (run as admin)" value={winService} />
                </>
              )}
            </div>
          );
        })()}
```

- [ ] **Step 4: Restart frontend and verify no TypeScript errors**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Wait 8 seconds:
```bash
docker compose logs frontend --tail=5 2>&1 | grep -v warning
```
Expected: `VITE v6.4.2  ready` with no errors.

- [ ] **Step 5: Manually verify in browser**

Open http://localhost:3000/settings (as admin with agent configured).

In the Deploy Agent section:
1. Confirm three tabs: `🐧 Linux (x86_64)`, `🐧 Linux (ARM64)`, `🪟 Windows`
2. On Linux tab: confirm first block shows a `curl` download command containing `/downloads/nexplane-agent-linux-amd64`, and a checksum verification command
3. On ARM64 tab: confirm download command uses `nexplane-agent-linux-arm64`
4. On Windows tab: confirm `Invoke-WebRequest` command with `nexplane-agent-windows-amd64.exe`
5. Click a Copy button — confirm it copies the correct text

Also verify the download works end-to-end in the browser's network tab: click Copy on the curl command, open a terminal, paste and run it — the binary should download.

- [ ] **Step 6: Commit**

```bash
cd f:/Nexplane/nexplane
git add docker-compose.yml frontend/src/pages/Settings.tsx
git commit -m "feat: update agent deploy panel with real download commands and ARM64 tab, wire VITE_AGENT_DOWNLOAD_URL override"
```

---

### Task 4: End-to-end smoke test

- [ ] **Step 1: Full rebuild to confirm everything assembles**

```bash
cd f:/Nexplane/nexplane
docker compose down && docker compose up --build -d
```

Wait for all three containers to be healthy:
```bash
docker compose ps
```
Expected: all three services `Up`.

- [ ] **Step 2: Verify all download artifacts are accessible**

```bash
curl -s http://localhost:8000/downloads/version
curl -I http://localhost:8000/downloads/nexplane-agent-linux-amd64
curl -I http://localhost:8000/downloads/nexplane-agent-linux-arm64
curl -I http://localhost:8000/downloads/nexplane-agent-windows-amd64.exe
curl -s http://localhost:8000/downloads/nexplane-agent-linux-amd64.sha256
```
Expected: version string returned; all binary HEAD requests return `200 OK`; checksum file returns valid `sha256sum` output.

- [ ] **Step 3: Download and verify the Linux binary**

```bash
curl -fL http://localhost:8000/downloads/nexplane-agent-linux-amd64 -o /tmp/nexplane-agent
chmod +x /tmp/nexplane-agent
file /tmp/nexplane-agent
```
Expected: `ELF 64-bit LSB executable, x86-64` (Linux binary, even if you're on Windows/Mac — it's cross-compiled).

- [ ] **Step 4: Verify backend tests still pass**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5"
```
Expected: all tests pass.

- [ ] **Step 5: Final commit if anything was adjusted**

```bash
cd f:/Nexplane/nexplane
git status
# If clean, nothing to commit. If any adjustments were needed, commit them:
git add -A
git diff --cached --stat
```
