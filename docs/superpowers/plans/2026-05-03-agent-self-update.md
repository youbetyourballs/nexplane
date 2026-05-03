# Agent Self-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up end-to-end agent self-update: a `VERSION` file drives build-time version injection into versioned binary filenames, the `updater` package checks the server version on startup and atomically replaces the binary, and the Settings page constructs versioned download URLs.

**Architecture:** A new `agent/updater` package exposes a single `CheckAndUpdate(ctx, controlPlaneURL, currentVersion)` function that fetches `/downloads/version`, downloads the versioned binary if behind, verifies its SHA256 checksum, and calls `syscall.Exec` to replace the running process. The `VERSION` file at repo root is the single source of truth — Dockerfile injects it via `-ldflags` into all binaries and writes it to `dist/version`; the frontend fetches `GET /downloads/version` to construct versioned download URLs.

**Tech Stack:** Go 1.26 (module `nexplane-agent`), `crypto/sha256`, `syscall.Exec`, React 18 + TanStack Query `useQuery`, FastAPI StaticFiles at `/opt/nexplane-downloads/`.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `VERSION` | Create | Repo-root single source of truth for current version |
| `agent/updater/updater.go` | Create | `CheckAndUpdate`, `fetchVersion`, `downloadFile`, `verifySHA256` |
| `agent/updater/updater_test.go` | Create | Unit tests using `net/http/httptest` |
| `agent/main.go` | Modify | `var Version = "dev"` (ldflags-injectable); call `CheckAndUpdate` before registration |
| `backend/Dockerfile` | Modify | `COPY VERSION /VERSION`; inject via `-ldflags "-X main.Version=${BUILD_VERSION}"`; versioned filenames; `cat /VERSION > dist/version` |
| `frontend/src/pages/Settings.tsx` | Modify | `useQuery` fetching `/downloads/version`; use versioned binary names in download commands |

---

## Task 1: Create VERSION file

**Files:**
- Create: `VERSION`

- [ ] **Step 1: Create the file**

```
0.1.0
```

Create at repo root (next to `agent/`, `backend/`, `frontend/`):

```bash
echo "0.1.0" > VERSION
```

- [ ] **Step 2: Verify**

```bash
cat VERSION
```
Expected output: `0.1.0`

- [ ] **Step 3: Commit**

```bash
git add VERSION
git commit -m "chore: add VERSION file (0.1.0) — single source of truth for agent version"
```

---

## Task 2: Create agent/updater package with tests

**Files:**
- Create: `agent/updater/updater.go`
- Create: `agent/updater/updater_test.go`

- [ ] **Step 1: Write the failing tests first**

Create `agent/updater/updater_test.go`:

```go
package updater_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"nexplane-agent/updater"
)

// helper: spin up a test server that serves a given version string at /downloads/version
func versionServer(t *testing.T, version string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/downloads/version" {
			fmt.Fprint(w, version)
			return
		}
		http.NotFound(w, r)
	}))
}

func TestCheckAndUpdate_AlreadyUpToDate(t *testing.T) {
	srv := versionServer(t, "0.1.0")
	defer srv.Close()

	updated, err := updater.CheckAndUpdate(context.Background(), srv.URL, "0.1.0")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if updated {
		t.Fatal("expected updated=false when versions match")
	}
}

func TestCheckAndUpdate_WindowsReturnsError(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows-only test")
	}
	srv := versionServer(t, "0.2.0")
	defer srv.Close()

	_, err := updater.CheckAndUpdate(context.Background(), srv.URL, "0.1.0")
	if err == nil {
		t.Fatal("expected error on Windows")
	}
	if !strings.Contains(err.Error(), "Windows") {
		t.Fatalf("expected Windows error message, got: %v", err)
	}
}

func TestCheckAndUpdate_VersionFetchError(t *testing.T) {
	// Point at a server that's already closed
	srv := versionServer(t, "0.2.0")
	srv.Close()

	_, err := updater.CheckAndUpdate(context.Background(), srv.URL, "0.1.0")
	if err == nil {
		t.Fatal("expected error when server is unreachable")
	}
}

func TestFetchVersion(t *testing.T) {
	srv := versionServer(t, "  0.2.0\n") // includes whitespace to test trimming
	defer srv.Close()

	version, err := updater.FetchVersion(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if version != "0.2.0" {
		t.Fatalf("expected '0.2.0', got %q", version)
	}
}

func TestDownloadFile(t *testing.T) {
	content := []byte("fake binary content")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(content)
	}))
	defer srv.Close()

	dest := filepath.Join(t.TempDir(), "downloaded")
	err := updater.DownloadFile(context.Background(), srv.URL+"/binary", dest)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got, _ := os.ReadFile(dest)
	if string(got) != string(content) {
		t.Fatalf("file content mismatch: got %q", got)
	}
}

func TestDownloadFile_HTTP404(t *testing.T) {
	srv := httptest.NewServer(http.NotFoundHandler())
	defer srv.Close()

	dest := filepath.Join(t.TempDir(), "downloaded")
	err := updater.DownloadFile(context.Background(), srv.URL+"/missing", dest)
	if err == nil {
		t.Fatal("expected error for HTTP 404")
	}
}

func TestVerifySHA256(t *testing.T) {
	data := []byte("binary content for sha256 test")
	h := sha256.Sum256(data)
	hashHex := hex.EncodeToString(h[:])

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// sha256sum output format: "<hash>  <filename>"
		fmt.Fprintf(w, "%s  nexplane-agent-linux-amd64-0.2.0\n", hashHex)
	}))
	defer srv.Close()

	tmpFile := filepath.Join(t.TempDir(), "binary")
	os.WriteFile(tmpFile, data, 0644)

	err := updater.VerifySHA256(context.Background(), srv.URL+"/binary.sha256", tmpFile)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestVerifySHA256_Mismatch(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "aabbccdd00112233aabbccdd00112233aabbccdd00112233aabbccdd00112233  somefile\n")
	}))
	defer srv.Close()

	tmpFile := filepath.Join(t.TempDir(), "binary")
	os.WriteFile(tmpFile, []byte("different content"), 0644)

	err := updater.VerifySHA256(context.Background(), srv.URL+"/binary.sha256", tmpFile)
	if err == nil {
		t.Fatal("expected SHA256 mismatch error")
	}
	if !strings.Contains(err.Error(), "SHA256 mismatch") {
		t.Fatalf("unexpected error message: %v", err)
	}
}
```

- [ ] **Step 2: Run tests — expect compile failure (package doesn't exist yet)**

```bash
cd agent && go test ./updater/... -v 2>&1 | head -20
```
Expected: `cannot find package "nexplane-agent/updater"` or similar build error.

- [ ] **Step 3: Implement agent/updater/updater.go**

Create `agent/updater/updater.go`:

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

// CheckAndUpdate fetches the server version and, if different from currentVersion,
// downloads the versioned binary, verifies its SHA256, atomically replaces the
// running binary, and exec()s the new process. Returns (true, nil) if update
// succeeded — but the caller never sees this because exec() replaces the process.
// Returns (false, nil) if already up to date.
// Returns (false, err) if anything failed — caller should log and continue.
func CheckAndUpdate(ctx context.Context, controlPlaneURL, currentVersion string) (bool, error) {
	if runtime.GOOS == "windows" {
		return false, errors.New("self-update not supported on Windows — replace binary manually from " + controlPlaneURL + "/downloads/")
	}

	serverVersion, err := FetchVersion(ctx, controlPlaneURL)
	if err != nil {
		return false, fmt.Errorf("version check failed: %w", err)
	}
	if serverVersion == currentVersion {
		return false, nil
	}

	arch := runtime.GOARCH
	binaryName := fmt.Sprintf("nexplane-agent-linux-%s-%s", arch, serverVersion)

	execPath, err := os.Executable()
	if err != nil {
		return false, fmt.Errorf("cannot determine executable path: %w", err)
	}

	newPath := execPath + ".new"
	if err := DownloadFile(ctx, controlPlaneURL+"/downloads/"+binaryName, newPath); err != nil {
		return false, fmt.Errorf("download failed: %w", err)
	}

	if err := VerifySHA256(ctx, controlPlaneURL+"/downloads/"+binaryName+".sha256", newPath); err != nil {
		_ = os.Remove(newPath)
		return false, fmt.Errorf("checksum verification failed: %w", err)
	}

	if err := os.Chmod(newPath, 0755); err != nil {
		_ = os.Remove(newPath)
		return false, fmt.Errorf("chmod failed: %w", err)
	}

	_ = os.Rename(execPath, execPath+".old")

	if err := os.Rename(newPath, execPath); err != nil {
		_ = os.Rename(execPath+".old", execPath)
		return false, fmt.Errorf("binary swap failed: %w", err)
	}

	return true, syscall.Exec(execPath, os.Args, os.Environ())
}

// FetchVersion fetches the plain-text version string from controlPlaneURL/downloads/version.
// Exported for testing.
func FetchVersion(ctx context.Context, controlPlaneURL string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, controlPlaneURL+"/downloads/version", nil)
	if err != nil {
		return "", err
	}
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

// DownloadFile downloads the file at url to destPath. Exported for testing.
func DownloadFile(ctx context.Context, url, destPath string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
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

// VerifySHA256 fetches checksumURL (sha256sum format), computes the SHA256 of
// filePath, and returns an error if they don't match. Exported for testing.
func VerifySHA256(ctx context.Context, checksumURL, filePath string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, checksumURL, nil)
	if err != nil {
		return err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 256))
	if err != nil {
		return err
	}
	fields := strings.Fields(string(body))
	if len(fields) == 0 {
		return fmt.Errorf("empty checksum file at %s", checksumURL)
	}
	expectedHash := fields[0]

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

- [ ] **Step 4: Run tests — expect pass**

```bash
cd agent && go test ./updater/... -v
```
Expected output (Linux/macOS — all pass):
```
--- PASS: TestCheckAndUpdate_AlreadyUpToDate (0.00s)
--- PASS: TestCheckAndUpdate_VersionFetchError (0.00s)
--- PASS: TestFetchVersion (0.00s)
--- PASS: TestDownloadFile (0.00s)
--- PASS: TestDownloadFile_HTTP404 (0.00s)
--- PASS: TestVerifySHA256 (0.00s)
--- PASS: TestVerifySHA256_Mismatch (0.00s)
PASS
ok      nexplane-agent/updater
```
(On Windows: `TestCheckAndUpdate_WindowsReturnsError` also passes; `TestCheckAndUpdate_AlreadyUpToDate` still passes because the version-match short-circuit runs before the OS check.)

- [ ] **Step 5: Run full agent test suite to catch regressions**

```bash
cd agent && go test ./... 2>&1
```
Expected: all existing tests pass plus the new updater tests.

- [ ] **Step 6: Commit**

```bash
git add agent/updater/updater.go agent/updater/updater_test.go
git commit -m "feat(agent): add updater package — CheckAndUpdate with SHA256 verify and atomic exec swap"
```

---

## Task 3: Wire updater into agent/main.go

**Files:**
- Modify: `agent/main.go`

Current state of `main.go`:
- Line 19: `const agentVersion = "0.1.0"` — hardcoded, not injectable
- Line 28: `log.Printf("Nexplane Agent %s starting ...", agentVersion, ...)`
- Line 43: `registration.Register(ctx, c, machineID, hostname, osType, agentVersion)`

- [ ] **Step 1: Replace the constant with an ldflags-injectable variable and add the updater call**

Replace the entire `main.go` with:

```go
package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"runtime"
	"syscall"

	"nexplane-agent/client"
	"nexplane-agent/config"
	"nexplane-agent/fingerprint"
	"nexplane-agent/poller"
	"nexplane-agent/registration"
	"nexplane-agent/updater"
)

// Version is injected at build time via -ldflags "-X main.Version=<version>".
// Falls back to "dev" for local builds.
var Version = "dev"

func main() {
	cfg, err := config.Load(os.Args[1:])
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}

	log.Printf("Nexplane Agent %s starting (mode=%s)", Version, cfg.Mode)

	// Check for updates before doing anything else. If an update is applied,
	// syscall.Exec replaces this process and we never reach the next line.
	if _, err := updater.CheckAndUpdate(context.Background(), cfg.ControlPlane, Version); err != nil {
		log.Printf("[updater] skipping update: %v", err)
	}

	machineID, err := fingerprint.GetMachineID()
	if err != nil {
		log.Fatalf("Cannot determine machine ID: %v", err)
	}

	hostname, _ := os.Hostname()
	osType := runtime.GOOS

	c := client.New(cfg.ControlPlane, cfg.Secret)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	info, err := registration.Register(ctx, c, machineID, hostname, osType, Version)
	if err != nil {
		log.Fatalf("Registration failed: %v", err)
	}
	log.Printf("Registered: agent_id=%s asset_id=%s", info.AgentID, info.AssetID)

	switch cfg.Mode {
	case "ephemeral":
		if err := poller.RunEphemeral(ctx, c, info.AgentID, cfg.Secret); err != nil {
			log.Fatalf("Ephemeral run failed: %v", err)
		}
	case "service":
		poller.RunService(ctx, c, info.AgentID, cfg.Secret, cfg.PollInterval)
	}

	log.Println("Agent stopped.")
}
```

- [ ] **Step 2: Build to verify it compiles**

```bash
cd agent && go build ./...
```
Expected: exits 0, no errors.

- [ ] **Step 3: Run all tests**

```bash
cd agent && go test ./...
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add agent/main.go
git commit -m "feat(agent): inject version via ldflags, call updater.CheckAndUpdate on startup"
```

---

## Task 4: Update Dockerfile — versioned filenames + ldflags + version file

**Files:**
- Modify: `backend/Dockerfile`

Current state of the relevant build stage (lines 1–13):
```dockerfile
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
RUN printf "dev" > dist/version
```

- [ ] **Step 1: Replace the agent-builder stage**

Edit `backend/Dockerfile` — replace lines 1–13 with:

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

The `COPY --from=agent-builder /agent/dist/ /opt/nexplane-downloads/` line in Stage 2 stays as-is — it copies the entire dist/ directory including versioned filenames.

- [ ] **Step 2: Rebuild the Docker image to verify**

From the repo root:

```bash
docker compose build backend
```
Expected: build succeeds. Look for lines like:
```
 => [agent-builder] RUN export BUILD_VERSION=$(cat /VERSION) ...
```

- [ ] **Step 3: Verify versioned files are present in the container**

```bash
docker compose run --rm backend ls /opt/nexplane-downloads/
```
Expected output includes:
```
nexplane-agent-linux-amd64-0.1.0
nexplane-agent-linux-amd64-0.1.0.sha256
nexplane-agent-linux-arm64-0.1.0
nexplane-agent-linux-arm64-0.1.0.sha256
nexplane-agent-windows-amd64-0.1.0.exe
nexplane-agent-windows-amd64-0.1.0.exe.sha256
version
```

- [ ] **Step 4: Verify the version file content**

```bash
docker compose run --rm backend cat /opt/nexplane-downloads/version
```
Expected: `0.1.0`

- [ ] **Step 5: Verify the binary has the right version baked in**

```bash
docker compose run --rm backend sh -c '/opt/nexplane-downloads/nexplane-agent-linux-amd64-0.1.0 --version 2>&1 || strings /opt/nexplane-downloads/nexplane-agent-linux-amd64-0.1.0 | grep "0\.1\.0"'
```
Expected: output contains `0.1.0`.

- [ ] **Step 6: Verify the version endpoint is reachable**

Start the stack, then:

```bash
curl -s http://localhost:8000/downloads/version
```
Expected: `0.1.0`

- [ ] **Step 7: Commit**

```bash
git add backend/Dockerfile
git commit -m "feat(docker): versioned agent binary filenames with ldflags version injection"
```

---

## Task 5: Update Settings.tsx — dynamic version fetch + versioned download URLs

**Files:**
- Modify: `frontend/src/pages/Settings.tsx`

The existing deploy-agent section (lines ~300–383) hardcodes binary names like `nexplane-agent-linux-amd64` without a version. This task replaces those with versioned names fetched from `/downloads/version`.

- [ ] **Step 1: Add the version query after the existing queries (around line 33)**

In `frontend/src/pages/Settings.tsx`, add this query after the `aiProviders` query block (after line 33, before the `updateKey` mutation):

```typescript
  const { data: agentVersion } = useQuery<string | null>({
    queryKey: ["agent-version"],
    queryFn: () =>
      apiClient
        .get<string>("/downloads/version", { responseType: "text" })
        .then((r) => (typeof r.data === "string" ? r.data.trim() : null))
        .catch(() => null),
    staleTime: 300_000, // 5 min
    enabled: !!(settings?.agent_configured || generatedSecret),
  });
```

Note: uses `apiClient` (axios instance already imported at line 6) so it goes to port 8000, consistent with all other API calls in this file.

- [ ] **Step 2: Replace hardcoded binary names with versioned names**

In the deploy-agent IIFE block (starting around line 300), replace the three `binaryName` / download URL lines:

**Find this block** (lines ~305–318):
```typescript
          const binaryName = agentPlatform === "windows"
            ? "nexplane-agent-windows-amd64.exe"
            : agentPlatform === "linux-arm64"
            ? "nexplane-agent-linux-arm64"
            : "nexplane-agent-linux-amd64";

          const agentBin = agentPlatform === "windows" ? "nexplane-agent.exe" : "nexplane-agent";

          const linuxDownload = `curl -fsSL ${downloadBase}/downloads/${binaryName} -o ${agentBin} && chmod +x ${agentBin}`;
          const linuxVerify = `curl -fsSL ${downloadBase}/downloads/${binaryName}.sha256 | sha256sum -c`;
```

**Replace with:**
```typescript
          const version = agentVersion ?? "<VERSION>";
          const binaryName = agentPlatform === "windows"
            ? `nexplane-agent-windows-amd64-${version}.exe`
            : agentPlatform === "linux-arm64"
            ? `nexplane-agent-linux-arm64-${version}`
            : `nexplane-agent-linux-amd64-${version}`;

          const agentBin = agentPlatform === "windows" ? "nexplane-agent.exe" : "nexplane-agent";

          const linuxDownload = `curl -fsSL ${downloadBase}/downloads/${binaryName} -o ${agentBin} && chmod +x ${agentBin}`;
          const linuxVerify = `curl -fsSL ${downloadBase}/downloads/${binaryName}.sha256 | sha256sum -c`;
```

Also update the Windows download line (line ~318):

**Find:**
```typescript
          const winDownload = `Invoke-WebRequest -Uri "${downloadBase}/downloads/${binaryName}" -OutFile ${agentBin}`;
```

This line references `binaryName` which is already updated above, so no change needed there.

- [ ] **Step 3: Start dev server and verify in browser**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Open `http://localhost:3000/settings`. Generate or rotate an agent secret so the deploy panel appears. Verify:
1. Download URLs show versioned filenames: `nexplane-agent-linux-amd64-0.1.0`
2. Switch between Linux/ARM64/Windows tabs — all show correct versioned names
3. If the `/downloads/version` fetch fails (e.g., backend down), URLs show `nexplane-agent-linux-amd64-<VERSION>` as fallback

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Settings.tsx
git commit -m "feat(frontend): fetch agent version dynamically, use versioned binary names in deploy panel"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Section 1 (VERSION file + Dockerfile): Tasks 1 and 4
- ✅ Section 2 (updater package): Task 2
- ✅ Section 3 (main.go integration): Task 3
- ✅ Section 4 (frontend settings panel): Task 5

**Placeholder scan:** No TBDs, TODOs, or vague steps — all code is complete.

**Type consistency:**
- `FetchVersion`, `DownloadFile`, `VerifySHA256` exported in `updater.go` and referenced correctly in `updater_test.go`
- `updater.CheckAndUpdate` imported as `"nexplane-agent/updater"` in `main.go` — matches go.mod module name `nexplane-agent`
- `agentVersion` query result typed as `string | null` in Settings.tsx, used with `?? "<VERSION>"` fallback
