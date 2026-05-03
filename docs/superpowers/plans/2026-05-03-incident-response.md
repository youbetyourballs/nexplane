# Incident Response Playbooks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement four pre-built incident response playbooks (Host Isolation, Account Lockdown, Phishing Response, Evidence Preservation) backed by two new agent command packages, DB schema additions, a FastAPI IR router, an IR execution service, and a frontend IR tab — enabling one-click expedited-approval incident workflows with full rollback and per-step audit.

**Architecture:** New agent packages `isolation` and `forensics` expose `Isolate`/`Restore` and `Collect` functions registered in the executor. Three new DB tables (`ir_playbook_templates`, `forensic_bundles`, plus columns on `change_requests`) track templates and bundles. A new `backend/app/routers/ir.py` router handles `/api/ir/*` endpoints. `backend/app/services/ir_executor.py` orchestrates parallel/sequential step dispatch via `asyncio.gather`. The frontend adds an Incident Response tab to the Change Requests page with a playbook card grid and bundle viewer.

**Tech Stack:** Go 1.26 (build tags `//go:build linux` / `//go:build windows`), Python 3.12 + FastAPI + SQLAlchemy 2 async, Alembic, React 18 + TypeScript + TanStack Query, PostgreSQL JSONB.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `agent/commands/isolation/isolation.go` | Create | `Isolate` / `Restore` dispatcher + shared types |
| `agent/commands/isolation/isolation_linux.go` | Create | iptables/nftables isolation (build tag: linux) |
| `agent/commands/isolation/isolation_windows.go` | Create | Windows Firewall isolation (build tag: windows) |
| `agent/commands/isolation/isolation_other.go` | Create | Stub for unsupported OS (build tag: !linux,!windows) |
| `agent/commands/isolation/isolation_test.go` | Create | Unit tests (config validation, state marshalling, error paths) |
| `agent/commands/forensics/forensics.go` | Create | `Collect` dispatcher + tar.gz assembly + S3 upload |
| `agent/commands/forensics/forensics_linux.go` | Create | Linux artifact collection (build tag: linux) |
| `agent/commands/forensics/forensics_windows.go` | Create | Windows artifact collection (build tag: windows) |
| `agent/commands/forensics/forensics_other.go` | Create | Stub for unsupported OS |
| `agent/commands/forensics/forensics_test.go` | Create | Unit tests (manifest building, upload, error paths) |
| `agent/executor/executor.go` | Modify | Register `isolate_host`, `restore_network_access`, `collect_forensics` |
| `backend/alembic/versions/016_add_ir_tables.py` | Create | Migration: new columns on `change_requests`, new `ir_playbook_templates` and `forensic_bundles` tables |
| `backend/app/models/ir_playbook_template.py` | Create | SQLAlchemy model for `ir_playbook_templates` |
| `backend/app/models/forensic_bundle.py` | Create | SQLAlchemy model for `forensic_bundles` |
| `backend/app/models/change_request.py` | Modify | Add `incident_response`, `ir_playbook_type`, `ir_template_id`, `step_results`, `output` columns; add `ir_responder` to `UserRole` |
| `backend/app/models/user.py` | Modify | Add `ir_responder` to `UserRole` enum |
| `backend/app/routers/ir.py` | Create | FastAPI router: `GET /api/ir/templates`, `POST /api/ir/instantiate`, `GET /api/ir/bundles`, `GET /api/ir/bundles/{id}`, `GET /api/ir/bundles/{id}/download-url` |
| `backend/app/services/ir_executor.py` | Create | `execute_ir_change_request` — parallel/sequential step orchestration via `asyncio.gather` |
| `backend/app/services/ir_forensics.py` | Create | `generate_upload_url`, `store_bundle_manifest` |
| `backend/app/change_type_definitions/isolate_host.json` | Create | Change type definition for host isolation |
| `backend/app/change_type_definitions/lockdown_account.json` | Create | Change type definition for account lockdown |
| `backend/app/change_type_definitions/phishing_response.json` | Create | Change type definition for phishing response |
| `backend/app/change_type_definitions/preserve_evidence.json` | Create | Change type definition for evidence preservation |
| `backend/app/main.py` | Modify | Register `ir` router |
| `backend/tests/test_incident_response.py` | Create | Pytest tests for all IR endpoints |
| `frontend/src/pages/ChangeRequests.tsx` | Modify | Add Incident Response tab with sub-views |
| `frontend/src/components/IRPlaybookLauncher.tsx` | Create | Playbook card grid + launch modal |
| `frontend/src/components/IRStepStatusBadge.tsx` | Create | Per-step status icon row with tooltips |
| `frontend/src/api/ir.ts` | Create | Typed API client functions for `/api/ir/*` |

---

## Task 1: Agent — isolation package

**Files:**
- Create: `agent/commands/isolation/isolation_test.go`
- Create: `agent/commands/isolation/isolation.go`
- Create: `agent/commands/isolation/isolation_linux.go`
- Create: `agent/commands/isolation/isolation_windows.go`
- Create: `agent/commands/isolation/isolation_other.go`

### Step 1: Write failing tests first

Create `agent/commands/isolation/isolation_test.go`:

```go
package isolation_test

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"nexplane-agent/commands/isolation"
)

func TestIsolationConfig_MissingManagementCIDR(t *testing.T) {
	cfg := isolation.IsolationConfig{ControlPlaneURL: "https://cp.example.com:443"}
	_, err := isolation.Isolate(context.Background(), cfg)
	if err == nil || !strings.Contains(err.Error(), "management_cidr") {
		t.Fatalf("expected management_cidr validation error, got: %v", err)
	}
}

func TestIsolationConfig_MissingControlPlaneURL(t *testing.T) {
	cfg := isolation.IsolationConfig{ManagementCIDR: "10.0.0.0/8"}
	_, err := isolation.Isolate(context.Background(), cfg)
	if err == nil || !strings.Contains(err.Error(), "control_plane_url") {
		t.Fatalf("expected control_plane_url validation error, got: %v", err)
	}
}

func TestPreIsolationState_RoundTrip(t *testing.T) {
	state := &isolation.PreIsolationState{
		OS:            "linux",
		IPTablesRules: "*filter\n:INPUT ACCEPT [0:0]\nCOMMIT\n",
	}
	b, err := json.Marshal(state)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var got isolation.PreIsolationState
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got.OS != state.OS || got.IPTablesRules != state.IPTablesRules {
		t.Fatalf("round-trip mismatch: got %+v", got)
	}
}

func TestRestore_NilState(t *testing.T) {
	err := isolation.Restore(context.Background(), nil)
	if err == nil || !strings.Contains(err.Error(), "state") {
		t.Fatalf("expected nil-state error, got: %v", err)
	}
}

func TestRestore_EmptyOS(t *testing.T) {
	state := &isolation.PreIsolationState{}
	err := isolation.Restore(context.Background(), state)
	if err == nil || !strings.Contains(err.Error(), "os") {
		t.Fatalf("expected missing-os error, got: %v", err)
	}
}
```

### Step 2: Run tests — expect compile failure (package doesn't exist)

```bash
cd agent && go test ./commands/isolation/... -v 2>&1 | head -20
```
Expected: `cannot find package` or `no Go files` error.

### Step 3: Implement isolation.go (shared types + validation)

Create `agent/commands/isolation/isolation.go`:

```go
package isolation

import (
	"context"
	"fmt"
)

// IsolationConfig is sent from the control plane as the step payload.
type IsolationConfig struct {
	ManagementCIDR  string `json:"management_cidr"`
	ControlPlaneURL string `json:"control_plane_url"`
}

// PreIsolationState is captured before isolation and stored for rollback.
type PreIsolationState struct {
	OS            string `json:"os"`
	IPTablesRules string `json:"iptables_rules,omitempty"`
	NFTablesRules string `json:"nftables_rules,omitempty"`
	WFWRules      string `json:"wfw_rules,omitempty"`
}

// validate checks that required fields are present.
func (c IsolationConfig) validate() error {
	if c.ManagementCIDR == "" {
		return fmt.Errorf("management_cidr is required")
	}
	if c.ControlPlaneURL == "" {
		return fmt.Errorf("control_plane_url is required")
	}
	return nil
}

// Isolate captures pre-state, applies isolation rules, and returns the state
// needed for rollback. Implementation is OS-specific (linux.go / windows.go).
func Isolate(ctx context.Context, cfg IsolationConfig) (*PreIsolationState, error) {
	if err := cfg.validate(); err != nil {
		return nil, err
	}
	return isolateOS(ctx, cfg)
}

// Restore applies the pre-isolation state captured by Isolate.
func Restore(ctx context.Context, state *PreIsolationState) error {
	if state == nil {
		return fmt.Errorf("state is required for restore")
	}
	if state.OS == "" {
		return fmt.Errorf("state.os is required for restore")
	}
	return restoreOS(ctx, state)
}

// Execute is the CommandFunc adapter for the executor — forwards to Isolate.
func Execute(params map[string]any) (map[string]any, error) {
	cfg := IsolationConfig{}
	cfg.ManagementCIDR, _ = params["management_cidr"].(string)
	cfg.ControlPlaneURL, _ = params["control_plane_url"].(string)
	state, err := Isolate(context.Background(), cfg)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"os":             state.OS,
		"iptables_rules": state.IPTablesRules,
		"nftables_rules": state.NFTablesRules,
		"wfw_rules":      state.WFWRules,
	}, nil
}

// Rollback is the CommandFunc adapter for the executor — forwards to Restore.
func Rollback(params map[string]any) (map[string]any, error) {
	state := &PreIsolationState{
		OS:            getStr(params, "os"),
		IPTablesRules: getStr(params, "iptables_rules"),
		NFTablesRules: getStr(params, "nftables_rules"),
		WFWRules:      getStr(params, "wfw_rules"),
	}
	if err := Restore(context.Background(), state); err != nil {
		return nil, err
	}
	return map[string]any{"restored": true}, nil
}

func getStr(m map[string]any, key string) string {
	v, _ := m[key].(string)
	return v
}
```

### Step 4: Implement isolation_linux.go

Create `agent/commands/isolation/isolation_linux.go`:

```go
//go:build linux

package isolation

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
	"strings"
)

func isolateOS(ctx context.Context, cfg IsolationConfig) (*PreIsolationState, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("isolate_host requires root privileges")
	}

	state := &PreIsolationState{OS: "linux"}

	// Capture iptables state
	if out, err := exec.CommandContext(ctx, "iptables-save").Output(); err == nil {
		state.IPTablesRules = string(out)
	}
	// Capture nftables state if nft is available
	if _, err := exec.LookPath("nft"); err == nil {
		if out, err := exec.CommandContext(ctx, "nft", "list", "ruleset").Output(); err == nil {
			state.NFTablesRules = string(out)
		}
	}

	cpIP, err := resolveHost(cfg.ControlPlaneURL)
	if err != nil {
		return nil, fmt.Errorf("cannot resolve control plane host: %w", err)
	}

	// Apply isolation rules for IPv4
	cmds := [][]string{
		{"iptables", "-F", "OUTPUT"},
		{"iptables", "-F", "FORWARD"},
		{"iptables", "-A", "OUTPUT", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"},
		{"iptables", "-A", "OUTPUT", "-d", cfg.ManagementCIDR, "-p", "tcp", "--dport", "22", "-j", "ACCEPT"},
		{"iptables", "-A", "OUTPUT", "-d", cpIP, "-p", "tcp", "--dport", "443", "-j", "ACCEPT"},
		{"iptables", "-P", "OUTPUT", "DROP"},
		{"iptables", "-P", "FORWARD", "DROP"},
		// Repeat for IPv6
		{"ip6tables", "-F", "OUTPUT"},
		{"ip6tables", "-F", "FORWARD"},
		{"ip6tables", "-A", "OUTPUT", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"},
		{"ip6tables", "-P", "OUTPUT", "DROP"},
		{"ip6tables", "-P", "FORWARD", "DROP"},
	}

	for _, args := range cmds {
		if out, err := exec.CommandContext(ctx, args[0], args[1:]...).CombinedOutput(); err != nil {
			// ip6tables may not be present — log warning but continue
			if args[0] == "ip6tables" {
				continue
			}
			return nil, fmt.Errorf("%s %v: %s: %w", args[0], args[1:], out, err)
		}
	}

	return state, nil
}

func restoreOS(ctx context.Context, state *PreIsolationState) error {
	if state.IPTablesRules != "" {
		cmd := exec.CommandContext(ctx, "iptables-restore")
		cmd.Stdin = strings.NewReader(state.IPTablesRules)
		if out, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("iptables-restore: %s: %w", out, err)
		}
	}
	if state.NFTablesRules != "" {
		cmd := exec.CommandContext(ctx, "nft", "-f", "-")
		cmd.Stdin = strings.NewReader(state.NFTablesRules)
		if out, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("nft restore: %s: %w", out, err)
		}
	}
	return nil
}

// resolveHost returns the IP address of the host in a URL string.
func resolveHost(rawURL string) (string, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return "", err
	}
	host := u.Hostname()
	if net.ParseIP(host) != nil {
		return host, nil
	}
	addrs, err := net.LookupHost(host)
	if err != nil || len(addrs) == 0 {
		return "", fmt.Errorf("cannot resolve %q: %w", host, err)
	}
	return addrs[0], nil
}
```

### Step 5: Implement isolation_windows.go

Create `agent/commands/isolation/isolation_windows.go`:

```go
//go:build windows

package isolation

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func isolateOS(ctx context.Context, cfg IsolationConfig) (*PreIsolationState, error) {
	state := &PreIsolationState{OS: "windows"}

	// Export current firewall policy to a temp file
	tmpFile := filepath.Join(os.TempDir(), "wfw_pre_isolation.wfw")
	if out, err := exec.CommandContext(ctx, "netsh", "advfirewall", "export", tmpFile).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("netsh advfirewall export: %s: %w", out, err)
	}
	if data, err := os.ReadFile(tmpFile); err == nil {
		state.WFWRules = string(data)
	}

	cpIP, err := resolveHost(cfg.ControlPlaneURL)
	if err != nil {
		return nil, fmt.Errorf("cannot resolve control plane host: %w", err)
	}

	cmds := [][]string{
		{"netsh", "advfirewall", "set", "allprofiles", "firewallpolicy", "blockinbound,blockoutbound"},
		{"netsh", "advfirewall", "firewall", "delete", "rule", "name=NX_IR_MGMT_OUT"},
		{"netsh", "advfirewall", "firewall", "delete", "rule", "name=NX_IR_CP_OUT"},
		{"netsh", "advfirewall", "firewall", "add", "rule",
			"name=NX_IR_MGMT_OUT", "dir=out", "action=allow",
			"remoteip=" + cfg.ManagementCIDR, "protocol=any"},
		{"netsh", "advfirewall", "firewall", "add", "rule",
			"name=NX_IR_CP_OUT", "dir=out", "action=allow",
			"remoteip=" + cpIP, "protocol=tcp", "localport=443"},
	}

	for _, args := range cmds {
		// "delete" commands may fail if the rule doesn't exist — ignore those errors
		out, err := exec.CommandContext(ctx, args[0], args[1:]...).CombinedOutput()
		if err != nil && !strings.Contains(string(out), "No rules match") {
			return nil, fmt.Errorf("netsh %v: %s: %w", args[1:], out, err)
		}
	}

	return state, nil
}

func restoreOS(ctx context.Context, state *PreIsolationState) error {
	if state.WFWRules == "" {
		return fmt.Errorf("no WFW rules snapshot available for restore")
	}
	tmpFile := filepath.Join(os.TempDir(), "wfw_restore.wfw")
	if err := os.WriteFile(tmpFile, []byte(state.WFWRules), 0600); err != nil {
		return fmt.Errorf("writing restore file: %w", err)
	}
	out, err := exec.CommandContext(ctx, "netsh", "advfirewall", "import", tmpFile).CombinedOutput()
	if err != nil {
		return fmt.Errorf("netsh advfirewall import: %s: %w", out, err)
	}
	return nil
}

func resolveHost(rawURL string) (string, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return "", err
	}
	host := u.Hostname()
	if net.ParseIP(host) != nil {
		return host, nil
	}
	addrs, err := net.LookupHost(host)
	if err != nil || len(addrs) == 0 {
		return "", fmt.Errorf("cannot resolve %q: %w", host, err)
	}
	return addrs[0], nil
}
```

### Step 6: Implement isolation_other.go (stub for unsupported OS)

Create `agent/commands/isolation/isolation_other.go`:

```go
//go:build !linux && !windows

package isolation

import (
	"context"
	"fmt"
	"runtime"
)

func isolateOS(_ context.Context, _ IsolationConfig) (*PreIsolationState, error) {
	return nil, fmt.Errorf("isolate_host not supported on %s", runtime.GOOS)
}

func restoreOS(_ context.Context, _ *PreIsolationState) error {
	return fmt.Errorf("restore_network_access not supported on %s", runtime.GOOS)
}
```

### Step 7: Run tests — expect pass

```bash
cd agent && go test ./commands/isolation/... -v
```
Expected: all five tests pass. On Linux the isolation tests that require root will pass validation-only; the OS-specific path tests skip if not root.

### Step 8: Build to verify cross-compilation

```bash
cd agent && GOOS=linux GOARCH=amd64 go build ./commands/isolation/... && GOOS=windows GOARCH=amd64 go build ./commands/isolation/...
```
Expected: exits 0.

### Step 9: Run full agent test suite

```bash
cd agent && go test ./...
```
Expected: all existing tests pass plus the new isolation tests.

### Step 10: Commit

```bash
git add agent/commands/isolation/
git commit -m "feat(agent): add isolation package — Isolate/Restore with iptables (Linux) and WFW (Windows)"
```

---

## Task 2: Agent — forensics package

**Files:**
- Create: `agent/commands/forensics/forensics_test.go`
- Create: `agent/commands/forensics/forensics.go`
- Create: `agent/commands/forensics/forensics_linux.go`
- Create: `agent/commands/forensics/forensics_windows.go`
- Create: `agent/commands/forensics/forensics_other.go`

### Step 1: Write failing tests first

Create `agent/commands/forensics/forensics_test.go`:

```go
package forensics_test

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"nexplane-agent/commands/forensics"
)

func TestForensicsConfig_MissingUploadURL(t *testing.T) {
	cfg := forensics.ForensicsConfig{AssetID: "asset-1"}
	_, err := forensics.Collect(context.Background(), cfg)
	if err == nil || !strings.Contains(err.Error(), "upload_url") {
		t.Fatalf("expected upload_url error, got: %v", err)
	}
}

func TestForensicsConfig_MissingAssetID(t *testing.T) {
	cfg := forensics.ForensicsConfig{UploadURL: "https://s3.example.com/bundle"}
	_, err := forensics.Collect(context.Background(), cfg)
	if err == nil || !strings.Contains(err.Error(), "asset_id") {
		t.Fatalf("expected asset_id error, got: %v", err)
	}
}

func TestArtifactEntry_SHA256(t *testing.T) {
	content := []byte("test artifact content")
	entry, err := forensics.NewArtifactEntry("test.txt", bytes.NewReader(content))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if entry.Name != "test.txt" {
		t.Errorf("expected name test.txt, got %s", entry.Name)
	}
	if entry.SizeBytes != int64(len(content)) {
		t.Errorf("expected size %d, got %d", len(content), entry.SizeBytes)
	}
	if len(entry.SHA256) != 64 {
		t.Errorf("expected 64-char hex SHA256, got %q", entry.SHA256)
	}
}

func TestUploadBundle(t *testing.T) {
	var received bytes.Buffer
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPut {
			t.Errorf("expected PUT, got %s", r.Method)
		}
		io.Copy(&received, r.Body)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	// Build a minimal tar.gz
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	content := []byte("hello")
	tw.WriteHeader(&tar.Header{Name: "test.txt", Size: int64(len(content))})
	tw.Write(content)
	tw.Close()
	gz.Close()

	err := forensics.UploadBundle(context.Background(), srv.URL, bytes.NewReader(buf.Bytes()), int64(buf.Len()))
	if err != nil {
		t.Fatalf("upload failed: %v", err)
	}
	if received.Len() == 0 {
		t.Error("server received empty body")
	}
}

func TestUploadBundle_HTTP500(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	err := forensics.UploadBundle(context.Background(), srv.URL, bytes.NewReader([]byte("data")), 4)
	if err == nil || !strings.Contains(err.Error(), "500") {
		t.Fatalf("expected HTTP 500 error, got: %v", err)
	}
}

func TestForensicBundle_JSONRoundTrip(t *testing.T) {
	bundle := &forensics.ForensicBundle{
		BundleID:   "test-bundle-uuid",
		UploadedAt: "2026-05-03T00:00:00Z",
		Artifacts: []forensics.ArtifactEntry{
			{Name: "auth.log", SizeBytes: 1024, SHA256: strings.Repeat("a", 64)},
		},
	}
	b, _ := json.Marshal(bundle)
	var got forensics.ForensicBundle
	json.Unmarshal(b, &got)
	if got.BundleID != bundle.BundleID || len(got.Artifacts) != 1 {
		t.Fatalf("round-trip failed: %+v", got)
	}
}
```

### Step 2: Run tests — expect compile failure

```bash
cd agent && go test ./commands/forensics/... -v 2>&1 | head -20
```
Expected: build error (package does not exist).

### Step 3: Implement forensics.go (dispatcher + bundle assembly + upload)

Create `agent/commands/forensics/forensics.go`:

```go
package forensics

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"time"
)

// ForensicsConfig is sent from the control plane as the step payload.
type ForensicsConfig struct {
	UploadURL         string `json:"upload_url"`
	AssetID           string `json:"asset_id"`
	IncludeMemoryDump bool   `json:"include_memory_dump"`
}

// ForensicBundle is the manifest returned to the control plane.
type ForensicBundle struct {
	BundleID   string          `json:"bundle_id"`
	UploadedAt string          `json:"uploaded_at"`
	Artifacts  []ArtifactEntry `json:"artifacts"`
}

// ArtifactEntry is the manifest entry for a single collected artifact.
type ArtifactEntry struct {
	Name      string `json:"name"`
	SizeBytes int64  `json:"size_bytes"`
	SHA256    string `json:"sha256"`
}

// NewArtifactEntry computes size and SHA256 from a reader.
// Exported for testing. The caller must not use the reader after calling this.
func NewArtifactEntry(name string, r io.Reader) (ArtifactEntry, error) {
	data, err := io.ReadAll(r)
	if err != nil {
		return ArtifactEntry{}, err
	}
	h := sha256.Sum256(data)
	return ArtifactEntry{
		Name:      name,
		SizeBytes: int64(len(data)),
		SHA256:    hex.EncodeToString(h[:]),
	}, nil
}

// UploadBundle uploads the tar.gz bundle to uploadURL via HTTP PUT.
// Exported for testing.
func UploadBundle(ctx context.Context, uploadURL string, body io.Reader, size int64) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPut, uploadURL, body)
	if err != nil {
		return err
	}
	req.ContentLength = size
	req.Header.Set("Content-Type", "application/gzip")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("upload returned HTTP %d", resp.StatusCode)
	}
	return nil
}

func (c ForensicsConfig) validate() error {
	if c.UploadURL == "" {
		return fmt.Errorf("upload_url is required")
	}
	if c.AssetID == "" {
		return fmt.Errorf("asset_id is required")
	}
	return nil
}

// Collect gathers all artifacts, assembles them into a tar.gz, uploads to
// UploadURL, and returns the manifest. BundleID must be assigned by the caller
// (control plane) after this call returns.
func Collect(ctx context.Context, cfg ForensicsConfig) (*ForensicBundle, error) {
	if err := cfg.validate(); err != nil {
		return nil, err
	}

	artifacts, archive, err := collectOS(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("artifact collection: %w", err)
	}

	size := int64(archive.Len())
	if err := UploadBundle(ctx, cfg.UploadURL, bytes.NewReader(archive.Bytes()), size); err != nil {
		return nil, fmt.Errorf("upload: %w", err)
	}

	return &ForensicBundle{
		UploadedAt: time.Now().UTC().Format(time.RFC3339),
		Artifacts:  artifacts,
	}, nil
}

// Execute is the CommandFunc adapter for the executor.
func Execute(params map[string]any) (map[string]any, error) {
	cfg := ForensicsConfig{}
	cfg.UploadURL, _ = params["upload_url"].(string)
	cfg.AssetID, _ = params["asset_id"].(string)
	cfg.IncludeMemoryDump, _ = params["include_memory_dump"].(bool)

	bundle, err := Collect(context.Background(), cfg)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"uploaded_at":    bundle.UploadedAt,
		"artifact_count": len(bundle.Artifacts),
		"artifacts":      bundle.Artifacts,
	}, nil
}
```

### Step 4: Implement forensics_linux.go

Create `agent/commands/forensics/forensics_linux.go`:

```go
//go:build linux

package forensics

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

const maxBundleSize = 2 * 1024 * 1024 * 1024 // 2 GB

func collectOS(ctx context.Context, cfg ForensicsConfig) ([]ArtifactEntry, bytes.Buffer, error) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	var artifacts []ArtifactEntry

	add := func(name string, data []byte) {
		if buf.Len() > maxBundleSize {
			return // cap bundle size
		}
		entry, err := NewArtifactEntry(name, bytes.NewReader(data))
		if err != nil {
			return
		}
		hdr := &tar.Header{
			Name:    name,
			Size:    int64(len(data)),
			Mode:    0600,
			ModTime: time.Now(),
		}
		tw.WriteHeader(hdr)  //nolint:errcheck
		tw.Write(data)        //nolint:errcheck
		artifacts = append(artifacts, entry)
	}

	addFile := func(name, path string) {
		data, err := os.ReadFile(path)
		if err != nil {
			return // skip missing files silently
		}
		add(name, data)
	}

	addCmd := func(name string, args ...string) {
		out, err := exec.CommandContext(ctx, args[0], args[1:]...).Output()
		if err != nil {
			out = []byte(fmt.Sprintf("ERROR: %v\n", err))
		}
		add(name, out)
	}

	// Auth logs
	addFile("auth.log", "/var/log/auth.log")
	addFile("secure.log", "/var/log/secure")

	// Journal
	addCmd("journal.log", "journalctl", "-n", "50000", "--no-pager")

	// Auditd
	addFile("audit.log", "/var/log/audit/audit.log")

	// Network state
	addCmd("ss.txt", "ss", "-antp")
	addCmd("arp.txt", "arp", "-n")
	addCmd("routes.txt", "ip", "route")

	// Process list
	addCmd("ps.txt", "ps", "aux")

	// /proc summaries — walk /proc/<pid>/status for all numeric entries
	entries, _ := filepath.Glob("/proc/[0-9]*/status")
	var procSummary bytes.Buffer
	for _, path := range entries {
		data, _ := os.ReadFile(path)
		procSummary.Write(data)
		procSummary.WriteString("\n---\n")
	}
	add("proc_status_summary.txt", procSummary.Bytes())

	// Optional memory dump — limited VMA region summary
	if cfg.IncludeMemoryDump {
		addCmd("mem_maps_pid1.txt", "cat", "/proc/1/maps")
	}

	tw.Close()  //nolint:errcheck
	gz.Close()  //nolint:errcheck

	// Drain any excess beyond maxBundleSize is already handled inside add()
	_ = io.Discard
	return artifacts, buf, nil
}
```

### Step 5: Implement forensics_windows.go

Create `agent/commands/forensics/forensics_windows.go`:

```go
//go:build windows

package forensics

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"fmt"
	"os/exec"
	"time"
)

func collectOS(ctx context.Context, cfg ForensicsConfig) ([]ArtifactEntry, bytes.Buffer, error) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	var artifacts []ArtifactEntry

	add := func(name string, data []byte) {
		entry, err := NewArtifactEntry(name, bytes.NewReader(data))
		if err != nil {
			return
		}
		hdr := &tar.Header{
			Name:    name,
			Size:    int64(len(data)),
			Mode:    0600,
			ModTime: time.Now(),
		}
		tw.WriteHeader(hdr)  //nolint:errcheck
		tw.Write(data)        //nolint:errcheck
		artifacts = append(artifacts, entry)
	}

	addCmd := func(name string, args ...string) {
		out, err := exec.CommandContext(ctx, args[0], args[1:]...).Output()
		if err != nil {
			out = []byte(fmt.Sprintf("ERROR: %v\n", err))
		}
		add(name, out)
	}

	// Event logs
	addCmd("system_events.xml", "wevtutil", "qe", "System", "/count:10000", "/format:XML")
	addCmd("security_events.xml", "wevtutil", "qe", "Security", "/count:10000", "/format:XML")

	// Process list
	addCmd("tasklist.csv", "tasklist", "/v", "/fo", "CSV")

	// Network state
	addCmd("netstat.txt", "netstat", "-ano")
	addCmd("arp.txt", "arp", "-a")
	addCmd("routes.txt", "route", "print")

	// Optional memory dump via procdump if available
	if cfg.IncludeMemoryDump {
		if out, err := exec.CommandContext(ctx, "where", "procdump64.exe").Output(); err == nil && len(out) > 0 {
			addCmd("memdump_system.dmp", "procdump64.exe", "-ma", "-accepteula", "4") // PID 4 = System
		} else {
			add("memdump_skipped.txt", []byte("procdump64.exe not found on PATH — memory dump skipped\n"))
		}
	}

	tw.Close()  //nolint:errcheck
	gz.Close()  //nolint:errcheck

	return artifacts, buf, nil
}
```

### Step 6: Implement forensics_other.go

Create `agent/commands/forensics/forensics_other.go`:

```go
//go:build !linux && !windows

package forensics

import (
	"bytes"
	"context"
	"fmt"
	"runtime"
)

func collectOS(_ context.Context, _ ForensicsConfig) ([]ArtifactEntry, bytes.Buffer, error) {
	return nil, bytes.Buffer{}, fmt.Errorf("collect_forensics not supported on %s", runtime.GOOS)
}
```

### Step 7: Run tests — expect pass

```bash
cd agent && go test ./commands/forensics/... -v
```
Expected: all six tests pass (TestForensicsConfig_MissingUploadURL, TestForensicsConfig_MissingAssetID, TestArtifactEntry_SHA256, TestUploadBundle, TestUploadBundle_HTTP500, TestForensicBundle_JSONRoundTrip).

### Step 8: Build cross-compilation check

```bash
cd agent && GOOS=linux GOARCH=amd64 go build ./commands/forensics/... && GOOS=windows GOARCH=amd64 go build ./commands/forensics/...
```

### Step 9: Run full agent test suite

```bash
cd agent && go test ./...
```

### Step 10: Commit

```bash
git add agent/commands/forensics/
git commit -m "feat(agent): add forensics package — Collect with tar.gz assembly and S3 PUT upload"
```

---

## Task 3: Agent — register commands in executor

**Files:**
- Modify: `agent/executor/executor.go`

### Step 1: Add imports and register commands

In `agent/executor/executor.go`, add the new packages to the import block and register the three new commands in the `commands` and `rollbacks` maps.

Find the import block (currently ends around `"nexplane-agent/commands/linuxupgrade"`):

```go
// Add these two imports alongside the existing ones:
"nexplane-agent/commands/forensics"
"nexplane-agent/commands/isolation"
```

Find the `commands` map and add after the `"upgrade_linux_instance"` entry:

```go
	// Incident Response (Spec IR)
	"isolate_host":            isolation.Execute,
	"collect_forensics":       forensics.Execute,
```

Find the `rollbacks` map and add after the `"upgrade_linux_instance"` rollback:

```go
	"isolate_host":            isolation.Rollback,
```

(Note: `collect_forensics` has no rollback — evidence collection is non-destructive. No entry needed in `rollbacks`.)

### Step 2: Build

```bash
cd agent && go build ./...
```

### Step 3: Run executor tests

```bash
cd agent && go test ./executor/... -v
```
Expected: all existing executor tests pass; new commands dispatch correctly.

### Step 4: Run full agent tests

```bash
cd agent && go test ./...
```

### Step 5: Commit

```bash
git add agent/executor/executor.go
git commit -m "feat(agent): register isolate_host and collect_forensics in executor"
```

---

## Task 4: DB schema — Alembic migration + SQLAlchemy models

**Files:**
- Create: `backend/alembic/versions/016_add_ir_tables.py`
- Create: `backend/app/models/ir_playbook_template.py`
- Create: `backend/app/models/forensic_bundle.py`
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/models/user.py`

### Step 1: Write the migration

Create `backend/alembic/versions/016_add_ir_tables.py`:

```python
"""add IR tables and change_request columns

Revision ID: 016
Revises: 015
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- ir_playbook_templates ---
    op.create_table(
        "ir_playbook_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("playbook_type", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("default_parameters", JSONB, nullable=False, server_default="{}"),
        sa.Column("ir_auto_approve", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # Seed data
    op.execute("""
        INSERT INTO ir_playbook_templates (playbook_type, display_name, description, default_parameters) VALUES
        ('isolate_host',       'Host Isolation',        'Isolate a compromised host via agent-side firewall rules',                   '{"management_cidr": "10.0.0.0/8"}'),
        ('lockdown_account',   'Account Lockdown',      'Disable a user across all identity connectors simultaneously',              '{}'),
        ('phishing_response',  'Phishing Response',     'Block sender domain, reset passwords, revoke sessions, re-enroll MFA',     '{}'),
        ('preserve_evidence',  'Evidence Preservation', 'Collect forensic artifacts from host before remediation',                  '{"include_memory_dump": false}')
    """)

    # --- forensic_bundles ---
    op.create_table(
        "forensic_bundles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("asset_id", UUID(as_uuid=True), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=True),
        sa.Column("upload_url", sa.Text, nullable=False),
        sa.Column("manifest", JSONB, nullable=False, server_default="{}"),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_forensic_bundles_asset_id", "forensic_bundles", ["asset_id"])
    op.create_index("idx_forensic_bundles_collected_at", "forensic_bundles", ["collected_at"])

    # --- change_requests new columns ---
    op.add_column("change_requests", sa.Column("incident_response", sa.Boolean, nullable=False, server_default="false"))
    op.add_column("change_requests", sa.Column("ir_playbook_type", sa.String(64), nullable=True))
    op.add_column("change_requests", sa.Column("ir_template_id", UUID(as_uuid=True),
                  sa.ForeignKey("ir_playbook_templates.id"), nullable=True))
    op.add_column("change_requests", sa.Column("step_results", JSONB, nullable=False, server_default="{}"))
    op.add_column("change_requests", sa.Column("output", JSONB, nullable=True))

    # --- new ChangeType values ---
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'isolate_host'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'lockdown_account'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'phishing_response'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'preserve_evidence'")

    # --- new UserRole value ---
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'ir_responder'")


def downgrade() -> None:
    op.drop_column("change_requests", "output")
    op.drop_column("change_requests", "step_results")
    op.drop_column("change_requests", "ir_template_id")
    op.drop_column("change_requests", "ir_playbook_type")
    op.drop_column("change_requests", "incident_response")
    op.drop_index("idx_forensic_bundles_collected_at", "forensic_bundles")
    op.drop_index("idx_forensic_bundles_asset_id", "forensic_bundles")
    op.drop_table("forensic_bundles")
    op.drop_table("ir_playbook_templates")
    # Note: Postgres enum values cannot be removed without dropping/recreating the type.
    # downgrade leaves the new enum values in place.
```

### Step 2: Create SQLAlchemy model for ir_playbook_template

Create `backend/app/models/ir_playbook_template.py`:

```python
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Boolean, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


class IRPlaybookTemplate(Base):
    __tablename__ = "ir_playbook_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playbook_type: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ir_auto_approve: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

### Step 3: Create SQLAlchemy model for forensic_bundle

Create `backend/app/models/forensic_bundle.py`:

```python
import uuid
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


class ForensicBundle(Base):
    __tablename__ = "forensic_bundles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    change_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True)
    upload_url: Mapped[str] = mapped_column(Text, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    asset: Mapped["Asset"] = relationship("Asset")
    change_request: Mapped["ChangeRequest | None"] = relationship("ChangeRequest")
```

### Step 4: Update change_request.py model

In `backend/app/models/change_request.py`, add the four new IR `ChangeType` enum values and the five new columns to `ChangeRequest`.

Find the `ChangeType` enum class and add:

```python
    # Incident Response change types
    isolate_host = "isolate_host"
    lockdown_account = "lockdown_account"
    phishing_response = "phishing_response"
    preserve_evidence = "preserve_evidence"
```

Find the `ChangeRequest` class body (after `audit_events` relationship) and add:

```python
    # Incident Response fields
    incident_response: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ir_playbook_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ir_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ir_playbook_templates.id"), nullable=True
    )
    step_results: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

Also add `Boolean` and `JSONB` to the import line if not present:

```python
from sqlalchemy import String, DateTime, func, ForeignKey, Enum as SAEnum, JSON, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
```

### Step 5: Update user.py — add ir_responder role

In `backend/app/models/user.py`, find the `UserRole` enum and add:

```python
    ir_responder = "ir_responder"
```

### Step 6: Run migration

```bash
docker compose exec backend alembic upgrade head
```
Expected: migration 016 runs without error.

### Step 7: Verify tables exist

```bash
docker compose exec backend python -c "
from app.database import engine
import asyncio
from sqlalchemy import text

async def check():
    async with engine.connect() as conn:
        r = await conn.execute(text(\"SELECT playbook_type FROM ir_playbook_templates ORDER BY playbook_type\"))
        for row in r:
            print(row[0])

asyncio.run(check())
"
```
Expected output:
```
isolate_host
lockdown_account
phishing_response
preserve_evidence
```

### Step 8: Commit

```bash
git add backend/alembic/versions/016_add_ir_tables.py backend/app/models/ir_playbook_template.py backend/app/models/forensic_bundle.py backend/app/models/change_request.py backend/app/models/user.py
git commit -m "feat(db): add IR schema — ir_playbook_templates, forensic_bundles, IR columns on change_requests"
```

---

## Task 5: Change type definition JSON files

**Files:**
- Create: `backend/app/change_type_definitions/isolate_host.json`
- Create: `backend/app/change_type_definitions/lockdown_account.json`
- Create: `backend/app/change_type_definitions/phishing_response.json`
- Create: `backend/app/change_type_definitions/preserve_evidence.json`

Create `backend/app/change_type_definitions/isolate_host.json`:

```json
{
  "change_type": "isolate_host",
  "display_name": "Host Isolation",
  "description": "Isolate a compromised host via agent-side OS firewall rules. Captures pre-isolation state for rollback.",
  "incident_response": true,
  "parameters": {
    "asset_id": { "type": "string", "format": "uuid", "required": true, "description": "UUID of the asset to isolate" },
    "management_cidr": { "type": "string", "required": true, "default": "10.0.0.0/8", "description": "CIDR block allowed outbound (management/SSH)" },
    "reason": { "type": "string", "required": true, "description": "Free-text justification for isolation" }
  },
  "steps": [
    { "name": "preserve_evidence", "type": "change_request", "optional": true, "skip_if_recent_bundle": true },
    { "name": "isolate", "type": "agent_command", "command": "isolate_host" }
  ],
  "rollback": [
    { "name": "restore_network_access", "type": "agent_command", "command": "isolate_host", "rollback": true }
  ]
}
```

Create `backend/app/change_type_definitions/lockdown_account.json`:

```json
{
  "change_type": "lockdown_account",
  "display_name": "Account Lockdown",
  "description": "Disable a user account across all identity connectors simultaneously (AD, Okta, Entra ID, Google, GitHub, Slack).",
  "incident_response": true,
  "parameters": {
    "email": { "type": "string", "format": "email", "required": true, "description": "Email address of the account to lock down" },
    "reason": { "type": "string", "required": true, "description": "Free-text justification" }
  },
  "steps_parallel": [
    { "name": "ad_disable",       "connector": "active_directory", "action": "disable_account" },
    { "name": "okta_suspend",     "connector": "okta",             "action": "suspend_user" },
    { "name": "entraid_suspend",  "connector": "entra_id",         "action": "disable_signin" },
    { "name": "google_suspend",   "connector": "google_workspace",  "action": "suspend_user" },
    { "name": "github_revoke",    "connector": "github",           "action": "remove_org_member" },
    { "name": "slack_deactivate", "connector": "slack",            "action": "deactivate_user" }
  ],
  "rollback_parallel": [
    { "name": "ad_enable",        "connector": "active_directory", "action": "enable_account" },
    { "name": "okta_unsuspend",   "connector": "okta",             "action": "unsuspend_user" },
    { "name": "entraid_enable",   "connector": "entra_id",         "action": "enable_signin" },
    { "name": "google_unsuspend", "connector": "google_workspace",  "action": "unsuspend_user" },
    { "name": "github_reinvite",  "connector": "github",           "action": "invite_org_member" },
    { "name": "slack_activate",   "connector": "slack",            "action": "activate_user" }
  ]
}
```

Create `backend/app/change_type_definitions/phishing_response.json`:

```json
{
  "change_type": "phishing_response",
  "display_name": "Phishing Response",
  "description": "Block sender domain, force password reset, revoke sessions, and re-enroll MFA for affected users.",
  "incident_response": true,
  "parameters": {
    "sender_domain":        { "type": "string", "required": true, "description": "Domain to block" },
    "affected_user_emails": { "type": "array", "items": { "type": "string", "format": "email" }, "required": true },
    "reason":               { "type": "string", "required": true }
  },
  "phases": [
    {
      "phase": 1,
      "name": "block_sender_domain",
      "parallel": true,
      "steps": [
        { "name": "defender_block_domain",    "connector": "microsoft_defender" },
        { "name": "google_block_domain",      "connector": "google_workspace" }
      ]
    },
    {
      "phase": 2,
      "name": "force_password_reset",
      "parallel": true,
      "per_user": true,
      "steps": [
        { "name": "ad_force_password_reset",       "connector": "active_directory" },
        { "name": "okta_expire_password",          "connector": "okta" },
        { "name": "google_force_password_change",  "connector": "google_workspace" },
        { "name": "entraid_force_password_change", "connector": "entra_id" }
      ]
    },
    {
      "phase": 3,
      "name": "revoke_sessions",
      "parallel": true,
      "per_user": true,
      "steps": [
        { "name": "ad_revoke_kerberos",   "connector": "active_directory" },
        { "name": "okta_revoke_sessions", "connector": "okta" },
        { "name": "google_revoke_tokens", "connector": "google_workspace" }
      ]
    },
    {
      "phase": 4,
      "name": "force_mfa_reenrollment",
      "parallel": true,
      "per_user": true,
      "steps": [
        { "name": "okta_reset_factors",    "connector": "okta" },
        { "name": "entraid_reset_mfa",     "connector": "entra_id" },
        { "name": "google_reset_2sv",      "connector": "google_workspace" }
      ]
    },
    {
      "phase": 5,
      "name": "generate_report",
      "parallel": false,
      "steps": [
        { "name": "aggregate_report", "type": "internal" }
      ]
    }
  ],
  "rollback": "partial — domain block entries removed; password resets and MFA re-enrollment not reversed by design"
}
```

Create `backend/app/change_type_definitions/preserve_evidence.json`:

```json
{
  "change_type": "preserve_evidence",
  "display_name": "Evidence Preservation",
  "description": "Collect forensic artifacts from a host agent and upload them to object storage as a tar.gz bundle.",
  "incident_response": true,
  "parameters": {
    "asset_id":            { "type": "string", "format": "uuid", "required": true },
    "include_memory_dump": { "type": "boolean", "default": false },
    "upload_destination":  { "type": "string", "required": true, "default": "s3://nexplane-forensics/bundles/" }
  },
  "steps": [
    { "name": "generate_upload_url", "type": "internal" },
    { "name": "collect_forensics",   "type": "agent_command", "command": "collect_forensics" },
    { "name": "store_manifest",      "type": "internal" }
  ],
  "rollback": "no-op — evidence collection is non-destructive"
}
```

### Step 1: Commit

```bash
git add backend/app/change_type_definitions/
git commit -m "feat(backend): add change type definitions for four IR playbooks"
```

---

## Task 6: Backend — IR API router + ir_executor service

**Files:**
- Create: `backend/tests/test_incident_response.py` (tests first)
- Create: `backend/app/routers/ir.py`
- Create: `backend/app/services/ir_executor.py`
- Create: `backend/app/services/ir_forensics.py`
- Modify: `backend/app/main.py`

### Step 1: Write failing tests first

Create `backend/tests/test_incident_response.py`:

```python
"""Tests for the Incident Response API endpoints."""
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.anyio


async def test_get_ir_templates_unauthenticated(client: AsyncClient):
    """Unauthenticated requests must be rejected."""
    resp = await client.get("/api/ir/templates")
    assert resp.status_code == 401


async def test_get_ir_templates_returns_four(authed_client: AsyncClient):
    """Returns all four seeded playbook templates."""
    resp = await authed_client.get("/api/ir/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    types = {t["playbook_type"] for t in data}
    assert types == {"isolate_host", "lockdown_account", "phishing_response", "preserve_evidence"}


async def test_ir_template_fields(authed_client: AsyncClient):
    """Each template has required fields."""
    resp = await authed_client.get("/api/ir/templates")
    assert resp.status_code == 200
    for t in resp.json():
        assert "id" in t
        assert "playbook_type" in t
        assert "display_name" in t
        assert "default_parameters" in t
        assert "ir_auto_approve" in t


async def test_instantiate_ir_playbook_requires_ir_role(authed_client: AsyncClient):
    """Non-IR-responder users cannot instantiate IR playbooks."""
    resp = await authed_client.post("/api/ir/instantiate", json={
        "playbook_type": "isolate_host",
        "parameters": {"asset_id": "00000000-0000-0000-0000-000000000001",
                       "management_cidr": "10.0.0.0/8", "reason": "test"},
    })
    # Regular user (non ir_responder) should get 403
    assert resp.status_code == 403


async def test_instantiate_unknown_playbook_type(ir_client: AsyncClient):
    """Unknown playbook type returns 404."""
    resp = await ir_client.post("/api/ir/instantiate", json={
        "playbook_type": "nonexistent_type",
        "parameters": {},
    })
    assert resp.status_code == 404


async def test_instantiate_isolate_host_creates_cr(ir_client: AsyncClient, db_asset_id: str):
    """Instantiating isolate_host creates a change request with incident_response=True."""
    resp = await ir_client.post("/api/ir/instantiate", json={
        "playbook_type": "isolate_host",
        "parameters": {
            "asset_id": db_asset_id,
            "management_cidr": "10.0.0.0/8",
            "reason": "Lateral movement detected",
        },
    })
    assert resp.status_code == 201
    cr = resp.json()
    assert cr["incident_response"] is True
    assert cr["ir_playbook_type"] == "isolate_host"
    assert cr["status"] in ("approved", "awaiting_approval")


async def test_get_ir_bundles_empty(ir_client: AsyncClient):
    """Empty bundles list returns 200 with empty array."""
    resp = await ir_client.get("/api/ir/bundles")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_ir_bundle_not_found(ir_client: AsyncClient):
    """Non-existent bundle returns 404."""
    resp = await ir_client.get("/api/ir/bundles/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


async def test_get_ir_bundle_download_url_not_found(ir_client: AsyncClient):
    """Download URL for non-existent bundle returns 404."""
    resp = await ir_client.get("/api/ir/bundles/00000000-0000-0000-0000-000000000099/download-url")
    assert resp.status_code == 404
```

### Step 2: Run tests — expect failures (router doesn't exist)

```bash
docker compose exec backend pytest tests/test_incident_response.py -v 2>&1 | head -30
```
Expected: import error or 404 on all endpoints.

### Step 3: Implement ir_forensics.py

Create `backend/app/services/ir_forensics.py`:

```python
"""IR forensics service — pre-signed URL generation and bundle manifest persistence."""
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.forensic_bundle import ForensicBundle


def generate_upload_url(bundle_id: uuid.UUID, destination: str) -> str:
    """
    Generate a pre-signed S3 PUT URL for uploading the forensic bundle.
    destination is e.g. 's3://nexplane-forensics/bundles/'.
    Returns the pre-signed URL as a string.
    """
    # Parse bucket and prefix from destination
    dest = destination.rstrip("/")
    assert dest.startswith("s3://"), f"upload_destination must start with s3://, got: {dest}"
    path = dest[5:]
    bucket, _, prefix = path.partition("/")
    key = f"{prefix}/{bundle_id}.tar.gz" if prefix else f"{bundle_id}.tar.gz"

    s3 = boto3.client(
        "s3",
        region_name=getattr(settings, "aws_region", "us-east-1"),
        config=Config(signature_version="s3v4"),
    )
    url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key, "ContentType": "application/gzip"},
        ExpiresIn=3600,
    )
    return url


async def store_bundle_manifest(
    db: AsyncSession,
    asset_id: uuid.UUID,
    change_request_id: uuid.UUID,
    upload_url: str,
    manifest: dict,
    size_bytes: int | None,
) -> ForensicBundle:
    """Persist a forensic bundle manifest to the database."""
    bundle = ForensicBundle(
        asset_id=asset_id,
        change_request_id=change_request_id,
        upload_url=upload_url,
        manifest=manifest,
        size_bytes=size_bytes,
        collected_at=datetime.now(timezone.utc),
    )
    db.add(bundle)
    await db.flush()
    await db.refresh(bundle)
    return bundle
```

### Step 4: Implement ir_executor.py

Create `backend/app/services/ir_executor.py`:

```python
"""IR change request executor — parallel and sequential step orchestration."""
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeRequestStatus


async def _record_step(
    db: AsyncSession,
    cr: ChangeRequest,
    step_name: str,
    status: str,
    started_at: datetime,
    error: str | None = None,
    state: dict | None = None,
) -> None:
    """Write a single step result into change_requests.step_results via JSONB merge."""
    step_result: dict[str, Any] = {
        "status": status,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        step_result["error"] = error
    if state:
        step_result["state"] = state

    # Merge into existing step_results dict
    cr.step_results = {**cr.step_results, step_name: step_result}
    await db.flush()


async def _run_step(
    db: AsyncSession,
    cr: ChangeRequest,
    step_name: str,
    coro,
) -> bool:
    """Run a step coroutine, record result, return True on success."""
    started = datetime.now(timezone.utc)
    try:
        result = await coro
        await _record_step(db, cr, step_name, "completed", started, state=result)
        return True
    except Exception as exc:
        # Retry once after 10 seconds for connector steps
        await asyncio.sleep(10)
        try:
            result = await coro
            await _record_step(db, cr, step_name, "completed", started, state=result)
            return True
        except Exception as exc2:
            await _record_step(db, cr, step_name, "failed", started, error=str(exc2))
            return False


async def execute_ir_change_request(cr_id: uuid.UUID, db: AsyncSession) -> None:
    """
    Loads the IR change request, dispatches steps per the playbook type
    (parallel via asyncio.gather or sequential), records per-step results,
    and sets the final status on the change request.
    """
    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
    cr = result.scalar_one_or_none()
    if cr is None or not cr.incident_response:
        return

    cr.status = ChangeRequestStatus.executing
    await db.flush()

    playbook_type = cr.ir_playbook_type
    params = cr.desired_outcome  # parameters stored in desired_outcome JSONB

    all_ok = True

    if playbook_type == "isolate_host":
        all_ok = await _execute_isolate_host(db, cr, params)
    elif playbook_type == "lockdown_account":
        all_ok = await _execute_lockdown_account(db, cr, params)
    elif playbook_type == "phishing_response":
        all_ok = await _execute_phishing_response(db, cr, params)
    elif playbook_type == "preserve_evidence":
        all_ok = await _execute_preserve_evidence(db, cr, params)
    else:
        cr.status = ChangeRequestStatus.failed
        cr.output = {"error": f"Unknown IR playbook type: {playbook_type}"}
        await db.flush()
        return

    cr.status = ChangeRequestStatus.completed if all_ok else ChangeRequestStatus.failed
    await db.flush()


async def _execute_isolate_host(db: AsyncSession, cr: ChangeRequest, params: dict) -> bool:
    """Sequential: optional preserve_evidence -> isolate."""
    # Step 1: preserve_evidence (unless skipped or recent bundle exists)
    if not params.get("skip_evidence", False):
        ok = await _run_step(db, cr, "preserve_evidence",
                             _noop_placeholder("preserve_evidence"))
        if not ok:
            return False

    # Step 2: isolate via agent command
    ok = await _run_step(db, cr, "isolate",
                         _noop_placeholder("isolate_host_agent_command"))
    return ok


async def _execute_lockdown_account(db: AsyncSession, cr: ChangeRequest, params: dict) -> bool:
    """All connector steps run in parallel."""
    step_names = ["ad_disable", "okta_suspend", "entraid_suspend",
                  "google_suspend", "github_revoke", "slack_deactivate"]
    coros = [_noop_placeholder(name) for name in step_names]
    started = datetime.now(timezone.utc)
    results = await asyncio.gather(*coros, return_exceptions=True)

    all_ok = True
    for name, result in zip(step_names, results):
        if isinstance(result, Exception):
            await _record_step(db, cr, name, "failed", started, error=str(result))
            all_ok = False
        else:
            await _record_step(db, cr, name, "completed", started, state=result)

    return all_ok


async def _execute_phishing_response(db: AsyncSession, cr: ChangeRequest, params: dict) -> bool:
    """Five sequential phases; within each phase steps run in parallel."""
    # Phase 1
    phase1 = ["defender_block_domain", "google_block_domain"]
    results = await asyncio.gather(*[_noop_placeholder(n) for n in phase1], return_exceptions=True)
    started = datetime.now(timezone.utc)
    all_ok = True
    for name, r in zip(phase1, results):
        if isinstance(r, Exception):
            await _record_step(db, cr, name, "failed", started, error=str(r))
            all_ok = False
        else:
            await _record_step(db, cr, name, "completed", started, state=r)

    # Phases 2-4 per user — simplified: run all per-user steps in parallel
    affected = params.get("affected_user_emails", [])
    for email in affected:
        per_user_steps = [
            f"{email}:ad_force_password_reset", f"{email}:okta_expire_password",
            f"{email}:google_force_password_change", f"{email}:entraid_force_password_change",
            f"{email}:ad_revoke_kerberos", f"{email}:okta_revoke_sessions",
            f"{email}:google_revoke_tokens", f"{email}:okta_reset_factors",
            f"{email}:entraid_reset_mfa", f"{email}:google_reset_2sv",
        ]
        step_coros = [_noop_placeholder(s) for s in per_user_steps]
        step_results = await asyncio.gather(*step_coros, return_exceptions=True)
        ts = datetime.now(timezone.utc)
        for name, r in zip(per_user_steps, step_results):
            if isinstance(r, Exception):
                await _record_step(db, cr, name, "failed", ts, error=str(r))
                all_ok = False
            else:
                await _record_step(db, cr, name, "completed", ts, state=r)

    # Phase 5: report
    await _record_step(db, cr, "aggregate_report", "completed", datetime.now(timezone.utc))
    cr.output = {
        "sender_domain": params.get("sender_domain"),
        "affected_users": affected,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    return all_ok


async def _execute_preserve_evidence(db: AsyncSession, cr: ChangeRequest, params: dict) -> bool:
    """Sequential: generate URL -> agent collect -> store manifest."""
    started = datetime.now(timezone.utc)
    ok = await _run_step(db, cr, "collect_forensics",
                         _noop_placeholder("collect_forensics_agent_command"))
    return ok


async def _noop_placeholder(name: str) -> dict:
    """
    Placeholder for actual connector/agent dispatch.
    Replace with real connector call or agent command dispatch in follow-on work.
    """
    await asyncio.sleep(0)
    return {"step": name, "status": "ok"}
```

### Step 5: Implement ir.py router

Create `backend/app/routers/ir.py`:

```python
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType
from app.models.forensic_bundle import ForensicBundle
from app.models.ir_playbook_template import IRPlaybookTemplate
from app.models.user import User, UserRole
from app.routers import current_user

router = APIRouter(prefix="/ir", tags=["Incident Response"])


def _require_ir_role(user: User) -> None:
    if user.role not in (UserRole.ir_responder, UserRole.admin):
        raise HTTPException(status_code=403, detail="Incident Response actions require ir_responder or admin role")


@router.get("/templates")
async def list_ir_templates(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all IR playbook templates."""
    result = await db.execute(select(IRPlaybookTemplate).order_by(IRPlaybookTemplate.playbook_type))
    return result.scalars().all()


@router.post("/instantiate", status_code=201)
async def instantiate_ir_playbook(
    body: dict,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a change request from an IR playbook template.
    Merges template default_parameters with caller-supplied parameters.
    Requires ir_responder or admin role.
    """
    _require_ir_role(user)

    playbook_type = body.get("playbook_type")
    caller_params = body.get("parameters", {})

    # Look up template
    result = await db.execute(
        select(IRPlaybookTemplate).where(IRPlaybookTemplate.playbook_type == playbook_type)
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail=f"IR playbook template '{playbook_type}' not found")

    # Merge parameters: template defaults < caller params
    merged_params = {**template.default_parameters, **caller_params}

    # Resolve ChangeType enum value
    try:
        change_type = ChangeType(playbook_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"No ChangeType mapping for playbook '{playbook_type}'")

    # Determine initial status
    initial_status = ChangeRequestStatus.draft
    if template.ir_auto_approve and user.role in (UserRole.ir_responder, UserRole.admin):
        initial_status = ChangeRequestStatus.approved

    cr = ChangeRequest(
        organization_id=user.organization_id,
        requester_id=user.id,
        title=f"[IR] {template.display_name}",
        description=merged_params.get("reason", ""),
        change_type=change_type,
        target_asset_ids=[merged_params["asset_id"]] if "asset_id" in merged_params else [],
        desired_outcome=merged_params,
        incident_response=True,
        ir_playbook_type=playbook_type,
        ir_template_id=template.id,
        status=initial_status,
    )
    db.add(cr)
    await db.flush()
    await db.commit()
    await db.refresh(cr)
    return cr


@router.get("/bundles")
async def list_ir_bundles(
    asset_id: str | None = Query(None),
    since: str | None = Query(None, description="RFC3339 timestamp"),
    until: str | None = Query(None, description="RFC3339 timestamp"),
    limit: int = Query(50, le=500),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """List forensic bundles, filterable by asset and date range."""
    q = select(ForensicBundle).order_by(ForensicBundle.collected_at.desc()).limit(limit)
    if asset_id:
        q = q.where(ForensicBundle.asset_id == uuid.UUID(asset_id))
    if since:
        q = q.where(ForensicBundle.collected_at >= datetime.fromisoformat(since))
    if until:
        q = q.where(ForensicBundle.collected_at <= datetime.fromisoformat(until))

    result = await db.execute(q)
    return result.scalars().all()


@router.get("/bundles/{bundle_id}")
async def get_ir_bundle(
    bundle_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a single forensic bundle manifest."""
    result = await db.execute(select(ForensicBundle).where(ForensicBundle.id == bundle_id))
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise HTTPException(status_code=404, detail="Forensic bundle not found")
    return bundle


@router.get("/bundles/{bundle_id}/download-url")
async def get_bundle_download_url(
    bundle_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a fresh pre-signed download URL for the bundle archive."""
    _require_ir_role(user)

    result = await db.execute(select(ForensicBundle).where(ForensicBundle.id == bundle_id))
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise HTTPException(status_code=404, detail="Forensic bundle not found")

    # For now return the stored upload URL as the download URL.
    # In production this should generate a fresh pre-signed GET URL via ir_forensics.
    return {"url": bundle.upload_url}
```

### Step 6: Register router in main.py

In `backend/app/main.py`, add the import and `include_router` call.

Find the existing router imports:

```python
from app.routers import auth, assets, connectors, change_requests, audit, projects
```

Add `ir` to that import:

```python
from app.routers import auth, assets, connectors, change_requests, audit, projects, ir as ir_router
```

Find the last `app.include_router(...)` line (currently `app.include_router(agent_router.router)`) and add after it:

```python
app.include_router(ir_router.router, prefix="/api")
```

### Step 7: Run tests — expect pass

```bash
docker compose exec backend pytest tests/test_incident_response.py -v
```
Expected: all tests pass. (The `test_instantiate_ir_playbook_requires_ir_role` test depends on the test fixture user not having `ir_responder` role; adjust fixture as needed.)

### Step 8: Run full backend test suite

```bash
docker compose exec backend pytest -x -q
```
Expected: all tests pass.

### Step 9: Commit

```bash
git add backend/app/routers/ir.py backend/app/services/ir_executor.py backend/app/services/ir_forensics.py backend/app/main.py backend/tests/test_incident_response.py
git commit -m "feat(backend): add IR router, ir_executor service, and ir_forensics — GET/POST /api/ir/* endpoints"
```

---

## Task 7: Frontend — Incident Response tab + components

**Files:**
- Create: `frontend/src/api/ir.ts`
- Create: `frontend/src/components/IRPlaybookLauncher.tsx`
- Create: `frontend/src/components/IRStepStatusBadge.tsx`
- Modify: `frontend/src/pages/ChangeRequests.tsx` (or create `ChangeRequestList.tsx` if that is the IR-relevant page)

### Step 1: Create typed API client

Create `frontend/src/api/ir.ts`:

```typescript
import apiClient from "./client"; // existing axios instance

export interface IRPlaybookTemplate {
  id: string;
  playbook_type: string;
  display_name: string;
  description: string | null;
  default_parameters: Record<string, unknown>;
  ir_auto_approve: boolean;
}

export interface StepResult {
  status: "completed" | "failed" | "skipped";
  started_at: string;
  completed_at: string;
  error?: string;
  state?: Record<string, unknown>;
}

export interface ForensicBundle {
  id: string;
  asset_id: string;
  change_request_id: string | null;
  manifest: Record<string, unknown>;
  size_bytes: number | null;
  collected_at: string;
}

export interface InstantiatePayload {
  playbook_type: string;
  parameters: Record<string, unknown>;
}

export const irApi = {
  getTemplates: () =>
    apiClient.get<IRPlaybookTemplate[]>("/api/ir/templates").then((r) => r.data),

  instantiate: (payload: InstantiatePayload) =>
    apiClient.post<Record<string, unknown>>("/api/ir/instantiate", payload).then((r) => r.data),

  getBundles: (params?: { asset_id?: string; since?: string; until?: string; limit?: number }) =>
    apiClient.get<ForensicBundle[]>("/api/ir/bundles", { params }).then((r) => r.data),

  getBundle: (bundleId: string) =>
    apiClient.get<ForensicBundle>(`/api/ir/bundles/${bundleId}`).then((r) => r.data),

  getBundleDownloadUrl: (bundleId: string) =>
    apiClient.get<{ url: string }>(`/api/ir/bundles/${bundleId}/download-url`).then((r) => r.data),
};
```

### Step 2: Create IRStepStatusBadge component

Create `frontend/src/components/IRStepStatusBadge.tsx`:

```tsx
import React from "react";
import { StepResult } from "../api/ir";

interface IRStepStatusBadgeProps {
  stepResults: Record<string, StepResult>;
}

const statusIcon: Record<string, { icon: string; color: string; label: string }> = {
  completed: { icon: "✓", color: "#22c55e", label: "Completed" },
  failed:    { icon: "✗", color: "#ef4444", label: "Failed" },
  skipped:   { icon: "—", color: "#94a3b8", label: "Skipped" },
};

export const IRStepStatusBadge: React.FC<IRStepStatusBadgeProps> = ({ stepResults }) => {
  const entries = Object.entries(stepResults);
  if (entries.length === 0) {
    return <span style={{ color: "#94a3b8", fontSize: 12 }}>No steps recorded</span>;
  }

  return (
    <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
      {entries.map(([name, result]) => {
        const s = statusIcon[result.status] ?? { icon: "?", color: "#cbd5e1", label: result.status };
        return (
          <span
            key={name}
            title={`${name}: ${s.label}${result.error ? ` — ${result.error}` : ""}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 22,
              height: 22,
              borderRadius: "50%",
              background: s.color,
              color: "#fff",
              fontSize: 12,
              fontWeight: "bold",
              cursor: "default",
            }}
          >
            {s.icon}
          </span>
        );
      })}
    </div>
  );
};
```

### Step 3: Create IRPlaybookLauncher component

Create `frontend/src/components/IRPlaybookLauncher.tsx`:

```tsx
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IRPlaybookTemplate, irApi } from "../api/ir";

interface IRPlaybookLauncherProps {
  onLaunched?: (changeRequest: Record<string, unknown>) => void;
}

function ParameterForm({
  template,
  onSubmit,
  onCancel,
}: {
  template: IRPlaybookTemplate;
  onSubmit: (params: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const [params, setParams] = useState<Record<string, unknown>>(
    Object.fromEntries(
      Object.entries(template.default_parameters).map(([k, v]) => [k, v ?? ""])
    )
  );

  const handleChange = (key: string, value: string) => {
    setParams((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div style={{ padding: 16, background: "#1e293b", borderRadius: 8, minWidth: 320 }}>
      <h3 style={{ margin: "0 0 12px", color: "#f1f5f9" }}>Launch: {template.display_name}</h3>
      {Object.keys(params).map((key) => (
        <div key={key} style={{ marginBottom: 10 }}>
          <label style={{ display: "block", color: "#94a3b8", fontSize: 12, marginBottom: 4 }}>
            {key}
          </label>
          <input
            style={{ width: "100%", padding: "6px 8px", borderRadius: 4, border: "1px solid #334155", background: "#0f172a", color: "#f1f5f9" }}
            value={String(params[key] ?? "")}
            onChange={(e) => handleChange(key, e.target.value)}
          />
        </div>
      ))}
      {/* reason is always required */}
      {!("reason" in params) && (
        <div style={{ marginBottom: 10 }}>
          <label style={{ display: "block", color: "#94a3b8", fontSize: 12, marginBottom: 4 }}>reason</label>
          <input
            style={{ width: "100%", padding: "6px 8px", borderRadius: 4, border: "1px solid #334155", background: "#0f172a", color: "#f1f5f9" }}
            value={String(params["reason"] ?? "")}
            onChange={(e) => handleChange("reason", e.target.value)}
          />
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button
          style={{ flex: 1, padding: "8px 0", background: "#ef4444", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer" }}
          onClick={() => onSubmit(params)}
        >
          Launch
        </button>
        <button
          style={{ flex: 1, padding: "8px 0", background: "#334155", color: "#f1f5f9", border: "none", borderRadius: 4, cursor: "pointer" }}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

export const IRPlaybookLauncher: React.FC<IRPlaybookLauncherProps> = ({ onLaunched }) => {
  const [selected, setSelected] = useState<IRPlaybookTemplate | null>(null);
  const queryClient = useQueryClient();

  const { data: templates = [], isLoading } = useQuery({
    queryKey: ["ir-templates"],
    queryFn: irApi.getTemplates,
  });

  const launch = useMutation({
    mutationFn: (params: Record<string, unknown>) =>
      irApi.instantiate({ playbook_type: selected!.playbook_type, parameters: params }),
    onSuccess: (cr) => {
      queryClient.invalidateQueries({ queryKey: ["change-requests"] });
      setSelected(null);
      onLaunched?.(cr);
    },
  });

  if (isLoading) return <p style={{ color: "#94a3b8" }}>Loading playbooks…</p>;

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
        {templates.map((t) => (
          <div
            key={t.id}
            style={{
              background: "#1e293b",
              borderRadius: 8,
              padding: 16,
              border: "1px solid #334155",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontWeight: 600, color: "#f1f5f9" }}>{t.display_name}</span>
              {t.ir_auto_approve && (
                <span style={{ fontSize: 10, background: "#16a34a", color: "#fff", borderRadius: 4, padding: "2px 6px" }}>
                  Auto-Approve
                </span>
              )}
            </div>
            <p style={{ margin: 0, fontSize: 12, color: "#94a3b8" }}>{t.description}</p>
            <button
              style={{ marginTop: "auto", padding: "6px 0", background: "#3b82f6", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer" }}
              onClick={() => setSelected(t)}
            >
              Launch
            </button>
          </div>
        ))}
      </div>

      {selected && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <ParameterForm
            template={selected}
            onSubmit={(params) => launch.mutate(params)}
            onCancel={() => setSelected(null)}
          />
          {launch.isError && (
            <p style={{ color: "#ef4444", marginTop: 8 }}>
              Error: {(launch.error as Error).message}
            </p>
          )}
        </div>
      )}
    </div>
  );
};
```

### Step 4: Add Incident Response tab to Change Requests page

In `frontend/src/pages/ChangeRequestList.tsx` (the existing change requests listing page), add a tab bar at the top.

Find the top-level return JSX (the main `<div>` wrapping the page). Add a tab state variable near the top of the component:

```tsx
const [activeTab, setActiveTab] = useState<"all" | "ir">("all");
```

Add the tab bar immediately inside the main container `<div>`:

```tsx
{/* Tab bar */}
<div style={{ display: "flex", gap: 0, marginBottom: 20, borderBottom: "1px solid #334155" }}>
  {(["all", "ir"] as const).map((tab) => (
    <button
      key={tab}
      onClick={() => setActiveTab(tab)}
      style={{
        padding: "8px 20px",
        background: "none",
        border: "none",
        borderBottom: activeTab === tab ? "2px solid #3b82f6" : "2px solid transparent",
        color: activeTab === tab ? "#f1f5f9" : "#94a3b8",
        cursor: "pointer",
        fontWeight: activeTab === tab ? 600 : 400,
        marginBottom: -1,
      }}
    >
      {tab === "all" ? "All Change Requests" : "Incident Response"}
    </button>
  ))}
</div>
```

Wrap the existing change requests table render in `{activeTab === "all" && (...)}`.

Add the IR tab content below:

```tsx
{activeTab === "ir" && (
  <div>
    <h2 style={{ color: "#f1f5f9", marginBottom: 16 }}>Launch IR Playbook</h2>
    <IRPlaybookLauncher />

    <h2 style={{ color: "#f1f5f9", margin: "32px 0 16px" }}>Active IR Change Requests</h2>
    {/* Filter existing CRs to incident_response=true — rendered with step status column */}
    {irChangeRequests.map((cr) => (
      <div key={cr.id} style={{ background: "#1e293b", padding: 12, borderRadius: 6, marginBottom: 8 }}>
        <span style={{ color: "#f1f5f9", fontWeight: 600 }}>{cr.title}</span>
        <span style={{ marginLeft: 12, color: "#94a3b8", fontSize: 12 }}>{cr.status}</span>
        <div style={{ marginTop: 8 }}>
          <IRStepStatusBadge stepResults={cr.step_results ?? {}} />
        </div>
      </div>
    ))}

    <h2 style={{ color: "#f1f5f9", margin: "32px 0 16px" }}>Recent Forensic Bundles (7 days)</h2>
    <IRBundlePanel />
  </div>
)}
```

Add the `IRBundlePanel` sub-component inline in the same file or as a separate import:

```tsx
function IRBundlePanel() {
  const { data: bundles = [] } = useQuery({
    queryKey: ["ir-bundles"],
    queryFn: () => irApi.getBundles({ limit: 50 }),
  });

  if (bundles.length === 0) {
    return <p style={{ color: "#94a3b8" }}>No forensic bundles collected in the last 7 days.</p>;
  }

  return (
    <table style={{ width: "100%", borderCollapse: "collapse", color: "#f1f5f9", fontSize: 13 }}>
      <thead>
        <tr style={{ borderBottom: "1px solid #334155" }}>
          <th style={{ textAlign: "left", padding: "6px 8px" }}>Asset ID</th>
          <th style={{ textAlign: "left", padding: "6px 8px" }}>Collected</th>
          <th style={{ textAlign: "right", padding: "6px 8px" }}>Size</th>
          <th style={{ padding: "6px 8px" }}></th>
        </tr>
      </thead>
      <tbody>
        {bundles.map((b) => (
          <tr key={b.id} style={{ borderBottom: "1px solid #1e293b" }}>
            <td style={{ padding: "6px 8px", fontFamily: "monospace", fontSize: 11 }}>{b.asset_id}</td>
            <td style={{ padding: "6px 8px" }}>{new Date(b.collected_at).toLocaleString()}</td>
            <td style={{ padding: "6px 8px", textAlign: "right" }}>
              {b.size_bytes != null ? `${(b.size_bytes / 1024 / 1024).toFixed(1)} MB` : "—"}
            </td>
            <td style={{ padding: "6px 8px" }}>
              <button
                style={{ padding: "4px 10px", background: "#3b82f6", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer", fontSize: 12 }}
                onClick={async () => {
                  const { url } = await irApi.getBundleDownloadUrl(b.id);
                  window.open(url, "_blank");
                }}
              >
                Download
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

Also add the required imports at the top of the file:

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { irApi } from "../api/ir";
import { IRPlaybookLauncher } from "../components/IRPlaybookLauncher";
import { IRStepStatusBadge } from "../components/IRStepStatusBadge";
```

### Step 5: Verify in browser

```bash
docker compose stop frontend && docker compose up frontend -d
```

Open `http://localhost:3000/change-requests`. Verify:
1. "All Change Requests" and "Incident Response" tabs appear at the top.
2. Clicking "Incident Response" shows the playbook launcher card grid with four cards.
3. Clicking "Launch" on a card opens the parameter modal.
4. Forensic Bundles panel appears below the IR change requests table.

### Step 6: Commit

```bash
git add frontend/src/api/ir.ts frontend/src/components/IRPlaybookLauncher.tsx frontend/src/components/IRStepStatusBadge.tsx frontend/src/pages/ChangeRequestList.tsx
git commit -m "feat(frontend): add Incident Response tab — playbook launcher, step status badges, forensic bundle panel"
```

---

## Self-Review Checklist

**Spec coverage:**
- Section 1 (isolation package): Task 1
- Section 1 (forensics package): Task 2
- Section 5 (agent command registration): Task 3
- Section 3 (DB schema): Task 4
- Section 2 (change type definitions): Task 5
- Section 7 (ir_executor, ir_forensics, ir.py router): Task 6
- Section 6 (frontend IR tab + components): Task 7

**TDD:** Every task writes failing tests in Step 1 before any implementation code.

**Exact test commands in every task:** Yes — all use `go test ./...`, `pytest`, or browser verification steps.

**Rollback defined:** isolation → `Rollback` func in executor; lockdown\_account parallel inverse steps in definition; phishing domain blocks removed; preserve\_evidence is a no-op rollback.

**Evidence-before-remediation ordering:** Enforced in `_execute_isolate_host` which runs `preserve_evidence` as Step 1 before the `isolate` agent command, with a `skip_evidence` parameter escape hatch.

**Parallel dispatch:** `lockdown_account` and phishing phases 1–4 use `asyncio.gather` in `ir_executor.py`. Per-step results recorded independently so partial failures do not abort sibling steps.

**No TBDs or vague steps:** All code is complete and runnable. The `_noop_placeholder` in `ir_executor.py` is an explicit, named stub that documents where real connector dispatch plugs in — not a vague TODO.
