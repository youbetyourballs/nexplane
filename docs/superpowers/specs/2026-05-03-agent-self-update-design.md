# Agent Self-Update — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** Agents check `/downloads/version` on startup and self-update by downloading the versioned binary, verifying its SHA256 checksum, and atomically replacing themselves via `exec()`. Linux only for MVP.

---

## Background

The agent binary is currently hardcoded to version `0.1.0` and the Dockerfile writes `"dev"` to the version file — they never agree. Binaries are also served with unversioned filenames, so there is no safe way for an agent to download "the specific version the server expects." This spec wires up end-to-end: a `VERSION` file drives build-time version injection, versioned binary filenames enable safe targeted downloads, and a new `updater` package handles the startup check and atomic swap.

---

## Design Decisions

- **Agent-driven, startup-only:** Agent checks the control plane on startup. No operator action needed, no background goroutine, no new server endpoints. Every restart is a sync check.
- **Versioned filenames:** `nexplane-agent-linux-amd64-0.2.0` rather than `nexplane-agent-linux-amd64`. Multiple versions coexist on the server; the agent downloads exactly the version it expects.
- **`VERSION` file at repo root:** Single source of truth. Dockerfile injects it into binaries via `-ldflags "-X main.Version=..."` and copies it to `dist/version`. Bumping a release = edit one file + rebuild.
- **Settings panel fetches version dynamically:** `useQuery` hits `GET /downloads/version`; the panel constructs versioned download URLs. Falls back to `<VERSION>` placeholder on failure.
- **Windows not supported for MVP:** `os.Exec()` (i.e. `syscall.Exec`) doesn't exist on Windows. The updater returns a graceful error on Windows and logs a manual-update message. The agent continues running as-is.
- **Old binary kept as `.old`:** Before the swap, the current binary is copied to `{execPath}.old`. Allows manual rollback without re-downloading.

---

## Section 1: VERSION File + Build Pipeline

**New file:** `VERSION` at repo root. Initial content: `0.1.0`.

**`backend/Dockerfile` changes:**

```dockerfile
# Stage 1 — Build agent binaries
FROM golang:1.26-alpine AS agent-builder
WORKDIR /agent
COPY agent/ .
COPY VERSION /VERSION
RUN go mod download
RUN export BUILD_VERSION=$(cat /VERSION) && \
    GOOS=linux   GOARCH=amd64  go build -ldflags="-s -w -X main.Version=${BUILD_VERSION}" \
        -o dist/nexplane-agent-linux-amd64-${BUILD_VERSION}     ./ && \
    GOOS=linux   GOARCH=arm64  go build -ldflags="-s -w -X main.Version=${BUILD_VERSION}" \
        -o dist/nexplane-agent-linux-arm64-${BUILD_VERSION}     ./ && \
    GOOS=windows GOARCH=amd64  go build -ldflags="-s -w -X main.Version=${BUILD_VERSION}" \
        -o dist/nexplane-agent-windows-amd64-${BUILD_VERSION}.exe ./
RUN export BUILD_VERSION=$(cat /VERSION) && \
    sha256sum dist/nexplane-agent-linux-amd64-${BUILD_VERSION}       | tee dist/nexplane-agent-linux-amd64-${BUILD_VERSION}.sha256 && \
    sha256sum dist/nexplane-agent-linux-arm64-${BUILD_VERSION}       | tee dist/nexplane-agent-linux-arm64-${BUILD_VERSION}.sha256 && \
    sha256sum dist/nexplane-agent-windows-amd64-${BUILD_VERSION}.exe | tee dist/nexplane-agent-windows-amd64-${BUILD_VERSION}.exe.sha256
RUN cat /VERSION > dist/version
```

Files served at `/downloads/`:
- `nexplane-agent-linux-amd64-0.1.0`
- `nexplane-agent-linux-arm64-0.1.0`
- `nexplane-agent-windows-amd64-0.1.0.exe`
- `nexplane-agent-linux-amd64-0.1.0.sha256`
- `nexplane-agent-linux-arm64-0.1.0.sha256`
- `nexplane-agent-windows-amd64-0.1.0.exe.sha256`
- `version` (plain text: `0.1.0`)

---

## Section 2: Agent Updater Package

**New file:** `agent/updater/updater.go`

```go
package updater

import (
    "context"
    "crypto/sha256"
    "encoding/hex"
    "errors"
    "fmt"
    "io"
    "net/http"
    "os"
    "runtime"
    "strings"
    "syscall"
)

// CheckAndUpdate fetches the server version, downloads the versioned binary if
// different from currentVersion, verifies its SHA256, atomically replaces the
// running binary, and exec()s the new binary. Returns (true, nil) if update
// succeeded (exec() replaces the process so the caller never sees this).
// Returns (false, nil) if already up to date.
// Returns (false, err) if the check or update failed (caller should log and continue).
func CheckAndUpdate(ctx context.Context, controlPlaneURL, currentVersion string) (bool, error) {
    if runtime.GOOS == "windows" {
        return false, errors.New("self-update not supported on Windows — replace binary manually from " + controlPlaneURL + "/downloads/")
    }

    // 1. Fetch server version
    serverVersion, err := fetchVersion(ctx, controlPlaneURL)
    if err != nil {
        return false, fmt.Errorf("version check failed: %w", err)
    }
    if serverVersion == currentVersion {
        return false, nil
    }

    // 2. Determine binary name for this platform
    arch := runtime.GOARCH // "amd64" or "arm64"
    binaryName := fmt.Sprintf("nexplane-agent-linux-%s-%s", arch, serverVersion)

    // 3. Find current executable path
    execPath, err := os.Executable()
    if err != nil {
        return false, fmt.Errorf("cannot determine executable path: %w", err)
    }

    // 4. Download new binary to a temp file
    newPath := execPath + ".new"
    if err := downloadFile(ctx, controlPlaneURL+"/downloads/"+binaryName, newPath); err != nil {
        return false, fmt.Errorf("download failed: %w", err)
    }

    // 5. Verify SHA256
    if err := verifySHA256(ctx, controlPlaneURL+"/downloads/"+binaryName+".sha256", newPath); err != nil {
        _ = os.Remove(newPath)
        return false, fmt.Errorf("checksum verification failed: %w", err)
    }

    // 6. Make executable
    if err := os.Chmod(newPath, 0755); err != nil {
        _ = os.Remove(newPath)
        return false, fmt.Errorf("chmod failed: %w", err)
    }

    // 7. Keep old binary as .old for manual rollback
    _ = os.Rename(execPath, execPath+".old")

    // 8. Atomic rename: new → current path
    if err := os.Rename(newPath, execPath); err != nil {
        // Attempt to restore old binary
        _ = os.Rename(execPath+".old", execPath)
        return false, fmt.Errorf("binary swap failed: %w", err)
    }

    // 9. Exec new binary (replaces this process)
    return true, syscall.Exec(execPath, os.Args, os.Environ())
}

func fetchVersion(ctx context.Context, controlPlaneURL string) (string, error) {
    req, _ := http.NewRequestWithContext(ctx, http.MethodGet, controlPlaneURL+"/downloads/version", nil)
    resp, err := http.DefaultClient.Do(req)
    if err != nil {
        return "", err
    }
    defer resp.Body.Close()
    body, err := io.ReadAll(io.LimitReader(resp.Body, 64))
    if err != nil {
        return "", err
    }
    return strings.TrimSpace(string(body)), nil
}

func downloadFile(ctx context.Context, url, destPath string) error {
    req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
    resp, err := http.DefaultClient.Do(req)
    if err != nil {
        return err
    }
    defer resp.Body.Close()
    if resp.StatusCode != http.StatusOK {
        return fmt.Errorf("HTTP %d downloading %s", resp.StatusCode, url)
    }
    f, err := os.Create(destPath)
    if err != nil {
        return err
    }
    defer f.Close()
    _, err = io.Copy(f, resp.Body)
    return err
}

func verifySHA256(ctx context.Context, checksumURL, filePath string) error {
    req, _ := http.NewRequestWithContext(ctx, http.MethodGet, checksumURL, nil)
    resp, err := http.DefaultClient.Do(req)
    if err != nil {
        return err
    }
    defer resp.Body.Close()
    body, err := io.ReadAll(io.LimitReader(resp.Body, 256))
    if err != nil {
        return err
    }
    // sha256sum output: "<hash>  <filename>"
    expectedHash := strings.Fields(string(body))[0]

    f, err := os.Open(filePath)
    if err != nil {
        return err
    }
    defer f.Close()
    h := sha256.New()
    if _, err := io.Copy(h, f); err != nil {
        return err
    }
    actualHash := hex.EncodeToString(h.Sum(nil))
    if actualHash != expectedHash {
        return fmt.Errorf("SHA256 mismatch: expected %s, got %s", expectedHash, actualHash)
    }
    return nil
}
```

---

## Section 3: main.go Integration

**`agent/main.go` changes:**

Replace the hardcoded constant:
```go
// Was: const AgentVersion = "0.1.0"
// Now:
var Version = "dev" // overridden by -ldflags "-X main.Version=..."
```

Add update check before registration:
```go
import "github.com/youbetyourballs/nexplane/agent/updater"

func main() {
    cfg := config.Load()

    // Check for updates before doing anything else.
    // If an update is applied, exec() replaces this process and we never reach the next line.
    updated, err := updater.CheckAndUpdate(context.Background(), cfg.ControlPlane, Version)
    if err != nil {
        log.Printf("[updater] skipping update: %v", err)
    }
    _ = updated // exec() already handled the true case

    // ... rest of startup: fingerprint, register, poll ...
}
```

---

## Section 4: Frontend Settings Panel

**`frontend/src/pages/Settings.tsx` changes:**

Add a query to fetch the current server version in the deploy panel:

```typescript
const { data: agentVersion } = useQuery({
  queryKey: ["agent-version"],
  queryFn: () =>
    fetch(`${import.meta.env.VITE_API_URL || ""}/downloads/version`)
      .then((r) => r.ok ? r.text() : Promise.reject())
      .then((t) => t.trim())
      .catch(() => null),
  staleTime: 300000, // 5 min
  enabled: !!(settings?.agent_configured || generatedSecret),
});
```

Use `agentVersion` in the deploy panel to construct versioned download URLs:

```typescript
const version = agentVersion ?? "<VERSION>";
const binaryName = agentPlatform === "windows"
  ? `nexplane-agent-windows-amd64-${version}.exe`
  : agentPlatform === "linux-arm64"
  ? `nexplane-agent-linux-arm64-${version}`
  : `nexplane-agent-linux-amd64-${version}`;

const linuxDownload = `curl -fsSL ${downloadBase}/downloads/${binaryName} -o nexplane-agent && chmod +x nexplane-agent`;
const linuxVerify   = `curl -fsSL ${downloadBase}/downloads/${binaryName}.sha256 | sha256sum -c`;
const winDownload   = `Invoke-WebRequest -Uri "${downloadBase}/downloads/${binaryName}" -OutFile nexplane-agent.exe`;
```

---

## Files Changed

| File | Change |
|------|--------|
| `VERSION` | New file at repo root — `0.1.0` |
| `backend/Dockerfile` | Inject `$(cat /VERSION)` into ldflags and binary filenames; produce versioned outputs |
| `agent/updater/updater.go` | New package — `CheckAndUpdate` function |
| `agent/main.go` | `var Version = "dev"` (ldflags-injectable); call `updater.CheckAndUpdate` on startup |
| `frontend/src/pages/Settings.tsx` | Fetch `/downloads/version`; use versioned filenames in download commands |
