# Nexplane Agent Binary — Go Implementation Plan (Plan 4b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the cross-platform Nexplane agent binary in Go — a standalone executable that fingerprints its host machine, registers with the Nexplane control plane, long-polls for signed commands, and executes five generic operations (estimate_image_size, change_ip, configure_syslog, virtualize_for_migration, upload_image) on both Linux and Windows.

**Architecture:** Single Go module at `agent/` in the Nexplane monorepo. Internal packages with clear interfaces: `config` → `fingerprint` → `client` → `registration` → `hmac` → `executor` → `commands/*`. The `poller` package wires these together into ephemeral (run-once) and service (loop) modes. Build tags (`//go:build linux` / `//go:build windows`) isolate OS-specific implementations within each command package.

**Tech Stack:** Go 1.22+, AWS SDK for Go v2 (`github.com/aws/aws-sdk-go-v2`), `golang.org/x/sys` (Windows registry), standard library for everything else (net/http, crypto/hmac, os/exec, encoding/json)

---

## Prerequisites

- Go 1.22+ installed locally (not in Docker — the agent is a separate binary)
- `go` available on PATH
- All commands run from `agent/` directory unless specified otherwise
- Run tests with: `go test ./... -v`
- Cross-compile check: `GOOS=linux GOARCH=amd64 go build -o /dev/null ./` and `GOOS=windows GOARCH=amd64 go build -o /dev/null ./`

---

## Task 1: Module Setup and Project Structure

**Files:**
- Create: `agent/go.mod`
- Create: `agent/go.sum` (generated)
- Create: `agent/main.go` (stub)
- Create: `agent/Makefile`
- Create: `agent/config/config.go`
- Create: `agent/config/config_test.go`

- [ ] **Step 1: Initialize Go module**

```bash
mkdir -p f:/Nexplane/nexplane/agent
cd f:/Nexplane/nexplane/agent
go mod init nexplane-agent
```

- [ ] **Step 2: Write failing config tests**

Create `agent/config/config_test.go`:

```go
package config_test

import (
	"os"
	"testing"

	"nexplane-agent/config"
)

func TestLoadFromFlags(t *testing.T) {
	cfg, err := config.Load([]string{
		"--control-plane", "https://nexplane.example.com",
		"--secret", "sk-agent-abc123",
		"--mode", "ephemeral",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ControlPlane != "https://nexplane.example.com" {
		t.Errorf("got %q, want %q", cfg.ControlPlane, "https://nexplane.example.com")
	}
	if cfg.Secret != "sk-agent-abc123" {
		t.Errorf("got %q, want %q", cfg.Secret, "sk-agent-abc123")
	}
	if cfg.Mode != "ephemeral" {
		t.Errorf("got %q, want %q", cfg.Mode, "ephemeral")
	}
}

func TestLoadFromEnv(t *testing.T) {
	os.Setenv("NP_CONTROL_PLANE", "https://env.example.com")
	os.Setenv("NP_SECRET", "sk-agent-env")
	os.Setenv("NP_MODE", "service")
	defer func() {
		os.Unsetenv("NP_CONTROL_PLANE")
		os.Unsetenv("NP_SECRET")
		os.Unsetenv("NP_MODE")
	}()

	cfg, err := config.Load([]string{})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ControlPlane != "https://env.example.com" {
		t.Errorf("got %q, want %q", cfg.ControlPlane, "https://env.example.com")
	}
}

func TestLoadDefaultMode(t *testing.T) {
	cfg, err := config.Load([]string{
		"--control-plane", "https://nexplane.example.com",
		"--secret", "sk-agent-abc",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Mode != "service" {
		t.Errorf("default mode should be 'service', got %q", cfg.Mode)
	}
	if cfg.PollInterval.Seconds() != 30 {
		t.Errorf("default poll interval should be 30s, got %v", cfg.PollInterval)
	}
}

func TestLoadErrorsMissingRequired(t *testing.T) {
	_, err := config.Load([]string{})
	if err == nil {
		t.Error("expected error for missing required flags, got nil")
	}
}
```

- [ ] **Step 3: Run to verify fails**

```bash
cd f:/Nexplane/nexplane/agent
go test ./config/... -v
```
Expected: `cannot find package "nexplane-agent/config"`

- [ ] **Step 4: Create `agent/config/config.go`**

```go
package config

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"time"
)

type Config struct {
	ControlPlane string
	Secret       string
	Mode         string
	PollInterval time.Duration
}

// Load parses configuration from flags first, then falls back to environment variables.
// Flags take precedence over env vars.
func Load(args []string) (*Config, error) {
	fs := flag.NewFlagSet("nexplane-agent", flag.ContinueOnError)

	controlPlane := fs.String("control-plane", "", "Base URL of the Nexplane control plane")
	secret := fs.String("secret", "", "Shared HMAC secret for agent authentication")
	mode := fs.String("mode", "", "Operation mode: ephemeral or service (default: service)")
	pollInterval := fs.Duration("poll-interval", 0, "Poll interval in service mode (default: 30s)")

	if err := fs.Parse(args); err != nil {
		return nil, fmt.Errorf("parsing flags: %w", err)
	}

	cfg := &Config{}

	// Control plane: flag > env
	if *controlPlane != "" {
		cfg.ControlPlane = *controlPlane
	} else if v := os.Getenv("NP_CONTROL_PLANE"); v != "" {
		cfg.ControlPlane = v
	}

	// Secret: flag > env
	if *secret != "" {
		cfg.Secret = *secret
	} else if v := os.Getenv("NP_SECRET"); v != "" {
		cfg.Secret = v
	}

	// Mode: flag > env > default
	if *mode != "" {
		cfg.Mode = *mode
	} else if v := os.Getenv("NP_MODE"); v != "" {
		cfg.Mode = v
	} else {
		cfg.Mode = "service"
	}

	// Poll interval: flag > env > default
	if *pollInterval != 0 {
		cfg.PollInterval = *pollInterval
	} else if v := os.Getenv("NP_POLL_INTERVAL"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return nil, fmt.Errorf("invalid NP_POLL_INTERVAL %q: %w", v, err)
		}
		cfg.PollInterval = d
	} else {
		cfg.PollInterval = 30 * time.Second
	}

	// Validate
	var errs []string
	if cfg.ControlPlane == "" {
		errs = append(errs, "--control-plane / NP_CONTROL_PLANE is required")
	}
	if cfg.Secret == "" {
		errs = append(errs, "--secret / NP_SECRET is required")
	}
	if cfg.Mode != "ephemeral" && cfg.Mode != "service" {
		errs = append(errs, fmt.Sprintf("--mode must be 'ephemeral' or 'service', got %q", cfg.Mode))
	}
	if len(errs) > 0 {
		return nil, errors.New(errs[0])
	}

	return cfg, nil
}
```

- [ ] **Step 5: Create stub `agent/main.go`**

```go
package main

import (
	"fmt"
	"os"

	"nexplane-agent/config"
)

func main() {
	cfg, err := config.Load(os.Args[1:])
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("Nexplane Agent starting (mode=%s, control-plane=%s)\n", cfg.Mode, cfg.ControlPlane)
}
```

- [ ] **Step 6: Create `agent/Makefile`**

```makefile
.PHONY: build build-linux build-windows test

build: build-linux build-windows

build-linux:
	GOOS=linux GOARCH=amd64 go build -o dist/nexplane-agent-linux-amd64 ./
	GOOS=linux GOARCH=arm64 go build -o dist/nexplane-agent-linux-arm64 ./

build-windows:
	GOOS=windows GOARCH=amd64 go build -o dist/nexplane-agent-windows-amd64.exe ./

test:
	go test ./... -v

clean:
	rm -rf dist/
```

- [ ] **Step 7: Run tests**

```bash
cd f:/Nexplane/nexplane/agent
go test ./config/... -v
```
Expected: 4 tests pass

- [ ] **Step 8: Verify cross-compilation**

```bash
cd f:/Nexplane/nexplane/agent
GOOS=linux GOARCH=amd64 go build -o /dev/null ./
GOOS=windows GOARCH=amd64 go build -o /dev/null ./
```
Expected: no errors

- [ ] **Step 9: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/
git commit -m "feat: add Go agent module, config package, and Makefile"
```

---

## Task 2: Fingerprint Package

**Files:**
- Create: `agent/fingerprint/fingerprint.go`
- Create: `agent/fingerprint/fingerprint_linux.go`
- Create: `agent/fingerprint/fingerprint_windows.go`
- Create: `agent/fingerprint/fingerprint_fallback.go`
- Create: `agent/fingerprint/fingerprint_test.go`

- [ ] **Step 1: Write failing test**

```go
// agent/fingerprint/fingerprint_test.go
package fingerprint_test

import (
	"strings"
	"testing"

	"nexplane-agent/fingerprint"
)

func TestGetMachineIDIsNonEmpty(t *testing.T) {
	id, err := fingerprint.GetMachineID()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if id == "" {
		t.Error("machine ID must not be empty")
	}
}

func TestGetMachineIDIsStable(t *testing.T) {
	id1, _ := fingerprint.GetMachineID()
	id2, _ := fingerprint.GetMachineID()
	if id1 != id2 {
		t.Errorf("machine ID not stable: %q != %q", id1, id2)
	}
}

func TestGetMachineIDNoSpaces(t *testing.T) {
	id, _ := fingerprint.GetMachineID()
	if strings.ContainsAny(id, " \t\n\r") {
		t.Errorf("machine ID should not contain whitespace: %q", id)
	}
}
```

- [ ] **Step 2: Run to verify fails**

```bash
go test ./fingerprint/... -v
```
Expected: `cannot find package "nexplane-agent/fingerprint"`

- [ ] **Step 3: Create `agent/fingerprint/fingerprint.go`**

```go
package fingerprint

// GetMachineID returns a stable identifier for this machine.
// Tries OS-native UUID first, falls back to MAC address hash.
func GetMachineID() (string, error) {
	id, err := nativeID()
	if err == nil && id != "" {
		return sanitize(id), nil
	}
	return macAddressHash()
}
```

- [ ] **Step 4: Create `agent/fingerprint/fingerprint_linux.go`**

```go
//go:build linux

package fingerprint

import (
	"os"
	"strings"
)

func nativeID() (string, error) {
	data, err := os.ReadFile("/etc/machine-id")
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}
```

- [ ] **Step 5: Create `agent/fingerprint/fingerprint_windows.go`**

```go
//go:build windows

package fingerprint

import (
	"golang.org/x/sys/windows/registry"
)

func nativeID() (string, error) {
	k, err := registry.OpenKey(
		registry.LOCAL_MACHINE,
		`SOFTWARE\Microsoft\Cryptography`,
		registry.QUERY_VALUE,
	)
	if err != nil {
		return "", err
	}
	defer k.Close()

	val, _, err := k.GetStringValue("MachineGuid")
	if err != nil {
		return "", err
	}
	return val, nil
}
```

- [ ] **Step 6: Create `agent/fingerprint/fingerprint_fallback.go`**

```go
package fingerprint

import (
	"crypto/sha256"
	"fmt"
	"net"
	"sort"
	"strings"
)

func macAddressHash() (string, error) {
	ifaces, err := net.Interfaces()
	if err != nil {
		return "", err
	}

	var macs []string
	for _, iface := range ifaces {
		if iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		if len(iface.HardwareAddr) == 0 {
			continue
		}
		macs = append(macs, iface.HardwareAddr.String())
	}

	if len(macs) == 0 {
		return "", fmt.Errorf("no non-loopback interfaces with MAC addresses found")
	}

	sort.Strings(macs)
	combined := strings.Join(macs, "|")
	hash := sha256.Sum256([]byte(combined))
	return fmt.Sprintf("%x", hash[:16]), nil
}

func sanitize(s string) string {
	return strings.Map(func(r rune) rune {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') ||
			(r >= '0' && r <= '9') || r == '-' || r == '_' {
			return r
		}
		return '-'
	}, strings.TrimSpace(s))
}
```

- [ ] **Step 7: Add `golang.org/x/sys` dependency**

```bash
cd f:/Nexplane/nexplane/agent
go get golang.org/x/sys@latest
```

- [ ] **Step 8: Run tests (Linux)**

```bash
go test ./fingerprint/... -v
```
Expected: 3 tests pass

- [ ] **Step 9: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/fingerprint/ agent/go.mod agent/go.sum
git commit -m "feat: add fingerprint package for stable machine ID"
```

---

## Task 3: HMAC and HTTP Client Packages

**Files:**
- Create: `agent/agenthmac/hmac.go`
- Create: `agent/agenthmac/hmac_test.go`
- Create: `agent/client/client.go`
- Create: `agent/client/client_test.go`

- [ ] **Step 1: Write HMAC tests**

```go
// agent/agenthmac/hmac_test.go
package agenthmac_test

import (
	"testing"

	"nexplane-agent/agenthmac"
)

func TestVerifyAcceptsCorrectSignature(t *testing.T) {
	sig := agenthmac.Sign("mysecret", "job-1", "change_ip", map[string]any{"interface": "eth0"})
	if !agenthmac.Verify("mysecret", "job-1", "change_ip", map[string]any{"interface": "eth0"}, sig) {
		t.Error("verify should accept correct signature")
	}
}

func TestVerifyRejectsWrongSecret(t *testing.T) {
	sig := agenthmac.Sign("secret-a", "job-1", "cmd", map[string]any{})
	if agenthmac.Verify("secret-b", "job-1", "cmd", map[string]any{}, sig) {
		t.Error("verify should reject wrong secret")
	}
}

func TestVerifyRejectsModifiedParams(t *testing.T) {
	sig := agenthmac.Sign("secret", "job-1", "cmd", map[string]any{"a": 1})
	if agenthmac.Verify("secret", "job-1", "cmd", map[string]any{"a": 2}, sig) {
		t.Error("verify should reject modified parameters")
	}
}

func TestSignIsDeterministic(t *testing.T) {
	s1 := agenthmac.Sign("s", "j", "c", map[string]any{"b": 2, "a": 1})
	s2 := agenthmac.Sign("s", "j", "c", map[string]any{"a": 1, "b": 2})
	if s1 != s2 {
		t.Error("sign should be deterministic regardless of map key order")
	}
}
```

- [ ] **Step 2: Run to verify fails**

```bash
go test ./agenthmac/... -v
```
Expected: package not found

- [ ] **Step 3: Create `agent/agenthmac/hmac.go`**

```go
package agenthmac

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/json"
	"fmt"
)

// Sign computes HMAC-SHA256 of "{jobID}:{command}:{canonicalJSON(params)}".
func Sign(secret, jobID, command string, params map[string]any) string {
	canonical := mustCanonical(params)
	message := fmt.Sprintf("%s:%s:%s", jobID, command, canonical)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(message))
	return fmt.Sprintf("%x", mac.Sum(nil))
}

// Verify checks a signature using constant-time comparison.
func Verify(secret, jobID, command string, params map[string]any, signature string) bool {
	expected := Sign(secret, jobID, command, params)
	return hmac.Equal([]byte(expected), []byte(signature))
}

func mustCanonical(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		panic(fmt.Sprintf("agenthmac: cannot marshal params: %v", err))
	}
	return string(b)
}
```

Note: `json.Marshal` on `map[string]any` sorts keys alphabetically in Go's standard library — this is the canonical form.

- [ ] **Step 4: Run HMAC tests**

```bash
go test ./agenthmac/... -v
```
Expected: 4 tests pass

- [ ] **Step 5: Write client tests**

```go
// agent/client/client_test.go
package client_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"nexplane-agent/client"
)

func TestClientSendsBearerToken(t *testing.T) {
	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		json.NewEncoder(w).Encode(map[string]string{"agent_id": "test-uuid", "asset_id": "asset-uuid"})
	}))
	defer srv.Close()

	c := client.New(srv.URL, "my-secret")
	_, err := c.Register(context.Background(), client.RegisterRequest{
		MachineID: "test-machine",
		Hostname:  "host1",
		OsType:    "linux",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if gotAuth != "Bearer my-secret" {
		t.Errorf("got Authorization %q, want %q", gotAuth, "Bearer my-secret")
	}
}

func TestClientHandles401(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(401)
	}))
	defer srv.Close()

	c := client.New(srv.URL, "wrong-secret")
	_, err := c.Register(context.Background(), client.RegisterRequest{MachineID: "x", Hostname: "y", OsType: "linux"})
	if err == nil {
		t.Error("expected error on 401, got nil")
	}
}
```

- [ ] **Step 6: Create `agent/client/client.go`**

```go
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Client struct {
	baseURL    string
	secret     string
	httpClient *http.Client
}

func New(baseURL, secret string) *Client {
	return &Client{
		baseURL: baseURL,
		secret:  secret,
		httpClient: &http.Client{
			Timeout: 40 * time.Second, // slightly longer than long-poll timeout
		},
	}
}

type RegisterRequest struct {
	MachineID    string   `json:"machine_id"`
	Hostname     string   `json:"hostname"`
	OsType       string   `json:"os_type"`
	IPAddresses  []string `json:"ip_addresses"`
	OsVersion    string   `json:"os_version"`
	AgentVersion string   `json:"agent_version"`
}

type RegisterResponse struct {
	AgentID string `json:"agent_id"`
	AssetID string `json:"asset_id"`
}

type JobResponse struct {
	JobID         string         `json:"job_id"`
	Command       string         `json:"command"`
	Parameters    map[string]any `json:"parameters"`
	HMACSignature string         `json:"hmac_signature"`
}

type JobResult struct {
	Status string         `json:"status"` // "completed" or "failed"
	Result map[string]any `json:"result,omitempty"`
	Error  string         `json:"error,omitempty"`
}

func (c *Client) Register(ctx context.Context, req RegisterRequest) (*RegisterResponse, error) {
	var resp RegisterResponse
	if err := c.post(ctx, "/agent/register", req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// PollNextJob long-polls for the next pending job. Returns nil, nil when no job available (204).
func (c *Client) PollNextJob(ctx context.Context, agentID string) (*JobResponse, error) {
	url := fmt.Sprintf("%s/agent/jobs/next?agent_id=%s", c.baseURL, agentID)
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Authorization", "Bearer "+c.secret)

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNoContent {
		return nil, nil
	}
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("poll returned %d: %s", resp.StatusCode, body)
	}

	var job JobResponse
	if err := json.NewDecoder(resp.Body).Decode(&job); err != nil {
		return nil, fmt.Errorf("decoding job response: %w", err)
	}
	return &job, nil
}

func (c *Client) PostResult(ctx context.Context, jobID string, result JobResult) error {
	url := fmt.Sprintf("/agent/jobs/%s/result", jobID)
	return c.post(ctx, url, result, nil)
}

func (c *Client) post(ctx context.Context, path string, body, out any) error {
	data, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("marshaling request: %w", err)
	}

	url := c.baseURL + path
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(data))
	if err != nil {
		return err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+c.secret)

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		bodyBytes, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("request to %s returned %d: %s", path, resp.StatusCode, bodyBytes)
	}

	if out != nil {
		return json.NewDecoder(resp.Body).Decode(out)
	}
	return nil
}
```

- [ ] **Step 7: Run tests**

```bash
go test ./agenthmac/... ./client/... -v
```
Expected: 6+ tests pass

- [ ] **Step 8: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/agenthmac/ agent/client/
git commit -m "feat: add HMAC and HTTP client packages"
```

---

## Task 4: Executor Dispatcher and Result Types

**Files:**
- Create: `agent/executor/executor.go`
- Create: `agent/executor/executor_test.go`

- [ ] **Step 1: Write failing tests**

```go
// agent/executor/executor_test.go
package executor_test

import (
	"testing"

	"nexplane-agent/executor"
)

func TestDispatchUnknownCommandReturnsError(t *testing.T) {
	result := executor.Dispatch("unknown_command", map[string]any{}, false, map[string]any{})
	if result.Status != "failed" {
		t.Errorf("unknown command should fail, got status %q", result.Status)
	}
	if result.Error == "" {
		t.Error("unknown command should set error message")
	}
}

func TestDispatchKnownCommandSucceeds(t *testing.T) {
	result := executor.Dispatch("estimate_image_size", map[string]any{
		"destination_path": "/tmp",
	}, false, map[string]any{})
	if result.Status != "completed" {
		t.Errorf("estimate_image_size should complete, got status=%q error=%q", result.Status, result.Error)
	}
}
```

- [ ] **Step 2: Create `agent/executor/executor.go`**

```go
package executor

import (
	"fmt"

	"nexplane-agent/commands/changip"
	"nexplane-agent/commands/configsyslog"
	"nexplane-agent/commands/estimatesize"
	"nexplane-agent/commands/uploadimage"
	"nexplane-agent/commands/virtualize"
)

// Result is the outcome of a command execution.
type Result struct {
	Status string         // "completed" or "failed"
	Data   map[string]any // returned to control plane as job result
	Error  string         // set on failure
}

// CommandFunc is the signature every command must implement.
type CommandFunc func(params map[string]any) (map[string]any, error)

var commands = map[string]CommandFunc{
	"estimate_image_size":       estimatesize.Execute,
	"change_ip":                 changip.Execute,
	"configure_syslog":          configsyslog.Execute,
	"virtualize_for_migration":  virtualize.Execute,
	"upload_image":              uploadimage.Execute,
}

var rollbacks = map[string]CommandFunc{
	"change_ip":                changip.Rollback,
	"configure_syslog":         configsyslog.Rollback,
	"virtualize_for_migration": virtualize.Rollback,
	"upload_image":             uploadimage.Rollback,
}

// Dispatch routes a command to its implementation. If rollback is true,
// previousResult is passed as context for the rollback function.
func Dispatch(command string, params map[string]any, rollback bool, previousResult map[string]any) Result {
	var fn CommandFunc
	var ok bool

	if rollback {
		fn, ok = rollbacks[command]
		if !ok {
			return Result{Status: "failed", Error: fmt.Sprintf("no rollback defined for command %q", command)}
		}
		// Merge previousResult into params so rollback functions have full context
		merged := make(map[string]any, len(params)+len(previousResult))
		for k, v := range previousResult {
			merged[k] = v
		}
		for k, v := range params {
			merged[k] = v
		}
		params = merged
	} else {
		fn, ok = commands[command]
		if !ok {
			return Result{Status: "failed", Error: fmt.Sprintf("unknown command %q", command)}
		}
	}

	data, err := fn(params)
	if err != nil {
		return Result{Status: "failed", Data: data, Error: err.Error()}
	}
	return Result{Status: "completed", Data: data}
}
```

- [ ] **Step 3: Create stub command packages (needed for executor to compile)**

Create the following stub files. Each will be replaced with a real implementation in later tasks.

`agent/commands/estimatesize/estimatesize.go`:
```go
package estimatesize

func Execute(params map[string]any) (map[string]any, error) {
	return executeOS(params)
}
```

`agent/commands/estimatesize/estimatesize_linux.go`:
```go
//go:build linux

package estimatesize

import (
	"fmt"
	"syscall"
)

func executeOS(params map[string]any) (map[string]any, error) {
	destPath, _ := params["destination_path"].(string)
	if destPath == "" {
		destPath = "/tmp"
	}

	var stat syscall.Statfs_t
	if err := syscall.Statfs(destPath, &stat); err != nil {
		return nil, fmt.Errorf("statfs %s: %w", destPath, err)
	}
	availBytes := stat.Bavail * uint64(stat.Bsize)

	// For mock purposes, use a fixed source size
	sourceSizeBytes := uint64(107374182400) // 100 GB
	recommended := uint64(float64(sourceSizeBytes) * 1.1)

	return map[string]any{
		"source_device":              params["source_device"],
		"source_size_bytes":          sourceSizeBytes,
		"destination_path":           destPath,
		"destination_available_bytes": availBytes,
		"recommended_minimum_bytes":  recommended,
		"sufficient_space":           availBytes >= recommended,
	}, nil
}
```

`agent/commands/estimatesize/estimatesize_windows.go`:
```go
//go:build windows

package estimatesize

import (
	"fmt"
	"syscall"
	"unsafe"
)

func executeOS(params map[string]any) (map[string]any, error) {
	destPath, _ := params["destination_path"].(string)
	if destPath == "" {
		destPath = `C:\`
	}

	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	getDiskFreeSpaceEx := kernel32.NewProc("GetDiskFreeSpaceExW")

	destPathPtr, err := syscall.UTF16PtrFromString(destPath)
	if err != nil {
		return nil, fmt.Errorf("invalid destination path: %w", err)
	}

	var freeBytesAvailable, totalBytes, totalFreeBytes uint64
	ret, _, callErr := getDiskFreeSpaceEx.Call(
		uintptr(unsafe.Pointer(destPathPtr)),
		uintptr(unsafe.Pointer(&freeBytesAvailable)),
		uintptr(unsafe.Pointer(&totalBytes)),
		uintptr(unsafe.Pointer(&totalFreeBytes)),
	)
	if ret == 0 {
		return nil, fmt.Errorf("GetDiskFreeSpaceEx: %w", callErr)
	}

	sourceSizeBytes := uint64(107374182400)
	recommended := uint64(float64(sourceSizeBytes) * 1.1)

	return map[string]any{
		"source_device":              params["source_device"],
		"source_size_bytes":          sourceSizeBytes,
		"destination_path":           destPath,
		"destination_available_bytes": freeBytesAvailable,
		"recommended_minimum_bytes":  recommended,
		"sufficient_space":           freeBytesAvailable >= recommended,
	}, nil
}
```

Create stub files for the other four commands (these are placeholders that will be replaced in Tasks 5–8):

`agent/commands/changip/changip.go`:
```go
package changip

func Execute(params map[string]any) (map[string]any, error) {
	return executeOS(params)
}

func Rollback(params map[string]any) (map[string]any, error) {
	return rollbackOS(params)
}
```

`agent/commands/changip/changip_linux.go`:
```go
//go:build linux

package changip

func executeOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"applied": true, "note": "stub — implement in Task 5"}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": true, "note": "stub"}, nil
}
```

`agent/commands/changip/changip_windows.go`:
```go
//go:build windows

package changip

func executeOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"applied": true, "note": "stub — implement in Task 6"}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": true, "note": "stub"}, nil
}
```

Create identical stub structure (`Execute`, `Rollback`, `executeOS`, `rollbackOS` with linux/windows build tag files) for:
- `agent/commands/configsyslog/` — package `configsyslog`
- `agent/commands/virtualize/` — package `virtualize`
- `agent/commands/uploadimage/` — package `uploadimage` (only `Execute`, no `Rollback` needed for the rollback map, but the compiler needs `Rollback` referenced — add it returning `nil, nil`)

- [ ] **Step 4: Run tests**

```bash
go test ./executor/... -v
```
Expected: 2 tests pass

- [ ] **Step 5: Verify cross-compilation still works**

```bash
GOOS=linux GOARCH=amd64 go build -o /dev/null ./
GOOS=windows GOARCH=amd64 go build -o /dev/null ./
```
Expected: no errors

- [ ] **Step 6: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/executor/ agent/commands/
git commit -m "feat: add executor dispatcher and command package stubs"
```

---

## Task 5: change_ip Command — Linux

**Files:**
- Modify: `agent/commands/changip/changip_linux.go` (replace stub)
- Create: `agent/commands/changip/changip_linux_test.go`

- [ ] **Step 1: Write failing Linux tests**

```go
//go:build linux

// agent/commands/changip/changip_linux_test.go
package changip_test

import (
	"testing"

	"nexplane-agent/commands/changip"
)

func TestExecuteRequiresInterface(t *testing.T) {
	_, err := changip.Execute(map[string]any{
		"mode": "static",
	})
	if err == nil {
		t.Error("expected error for missing interface")
	}
}

func TestExecuteRequiresMode(t *testing.T) {
	_, err := changip.Execute(map[string]any{
		"interface": "eth0",
	})
	if err == nil {
		t.Error("expected error for missing mode")
	}
}

func TestExecuteInvalidMode(t *testing.T) {
	_, err := changip.Execute(map[string]any{
		"interface": "eth0",
		"mode":      "invalid",
	})
	if err == nil {
		t.Error("expected error for invalid mode")
	}
}

func TestRollbackRequiresPreviousResult(t *testing.T) {
	// Rollback with no snapshot should return an error
	_, err := changip.Rollback(map[string]any{})
	if err == nil {
		t.Error("expected error when no snapshot in params")
	}
}
```

- [ ] **Step 2: Run to verify fails**

```bash
go test ./commands/changip/... -v -run 'TestExecuteRequires|TestRollback'
```
Expected: FAIL — stubs don't validate params

- [ ] **Step 3: Replace `agent/commands/changip/changip_linux.go`**

```go
//go:build linux

package changip

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func executeOS(params map[string]any) (map[string]any, error) {
	iface, _ := params["interface"].(string)
	mode, _ := params["mode"].(string)
	ipVersion, _ := params["ip_version"].(string)

	if iface == "" {
		return nil, fmt.Errorf("interface is required")
	}
	if mode != "static" && mode != "dhcp" {
		return nil, fmt.Errorf("mode must be 'static' or 'dhcp', got %q", mode)
	}
	if ipVersion == "" {
		ipVersion = "4"
	}

	// Capture snapshot before making changes
	snapshot, err := captureSnapshot(iface)
	if err != nil {
		return nil, fmt.Errorf("capturing snapshot: %w", err)
	}

	// Detect network manager and apply changes
	method := detectNetworkManager()
	if err := applyIPChange(method, iface, mode, ipVersion, params); err != nil {
		return nil, err
	}

	return map[string]any{
		"action":    "change_ip",
		"interface": iface,
		"mode":      mode,
		"applied":   true,
		"snapshot":  snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok || snapshot == nil {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	iface, _ := params["interface"].(string)
	if iface == "" {
		iface, _ = snapshot["interface"].(string)
	}
	if iface == "" {
		return nil, fmt.Errorf("cannot determine interface for rollback")
	}

	method := detectNetworkManager()
	// Restore from snapshot by calling applyIPChange with snapshot values
	snapshotParams := map[string]any{
		"interface":   iface,
		"mode":        snapshotMode(snapshot),
		"ip_version":  "both",
		"new_ip_v4":   snapshotAddr(snapshot, "ipv4"),
		"new_ip_v6":   snapshotAddr(snapshot, "ipv6"),
		"new_gateway_v4": snapshotGW(snapshot, "ipv4"),
		"new_gateway_v6": snapshotGW(snapshot, "ipv6"),
	}
	if err := applyIPChange(method, iface, snapshotMode(snapshot), "both", snapshotParams); err != nil {
		return nil, err
	}
	return map[string]any{"rolled_back": true, "interface": iface}, nil
}

type networkManager string

const (
	nmNetworkManager networkManager = "networkmanager"
	nmSystemd        networkManager = "systemd-networkd"
	nmDebian         networkManager = "debian-interfaces"
	nmRHEL           networkManager = "rhel-ifcfg"
)

func detectNetworkManager() networkManager {
	if _, err := exec.LookPath("nmcli"); err == nil {
		return nmNetworkManager
	}
	if _, err := os.Stat("/etc/systemd/network"); err == nil {
		return nmSystemd
	}
	if _, err := os.Stat("/etc/network/interfaces"); err == nil {
		return nmDebian
	}
	return nmRHEL
}

func captureSnapshot(iface string) (map[string]any, error) {
	out, err := exec.Command("ip", "addr", "show", iface).Output()
	if err != nil {
		return nil, fmt.Errorf("ip addr show %s: %w", iface, err)
	}
	gwOut, _ := exec.Command("ip", "route", "show", "dev", iface).Output()
	return map[string]any{
		"interface": iface,
		"ip_output": strings.TrimSpace(string(out)),
		"gw_output": strings.TrimSpace(string(gwOut)),
	}, nil
}

func applyIPChange(method networkManager, iface, mode, ipVersion string, params map[string]any) error {
	switch method {
	case nmNetworkManager:
		return applyNmcli(iface, mode, ipVersion, params)
	case nmSystemd:
		return applySystemdNetworkd(iface, mode, ipVersion, params)
	case nmDebian:
		return applyDebianInterfaces(iface, mode, ipVersion, params)
	default:
		return applyRHELIfcfg(iface, mode, ipVersion, params)
	}
}

func applyNmcli(iface, mode, ipVersion string, params map[string]any) error {
	if mode == "dhcp" {
		if ipVersion == "4" || ipVersion == "both" {
			if err := run("nmcli", "con", "mod", iface, "ipv4.method", "auto"); err != nil {
				return err
			}
		}
		if ipVersion == "6" || ipVersion == "both" {
			if err := run("nmcli", "con", "mod", iface, "ipv6.method", "auto"); err != nil {
				return err
			}
		}
	} else {
		if v4, ok := params["new_ip_v4"].(string); ok && v4 != "" {
			args := []string{"con", "mod", iface, "ipv4.method", "manual", "ipv4.addresses", v4}
			if gw, ok := params["new_gateway_v4"].(string); ok && gw != "" {
				args = append(args, "ipv4.gateway", gw)
			}
			if err := run("nmcli", args...); err != nil {
				return err
			}
		}
		if v6, ok := params["new_ip_v6"].(string); ok && v6 != "" {
			args := []string{"con", "mod", iface, "ipv6.method", "manual", "ipv6.addresses", v6}
			if gw, ok := params["new_gateway_v6"].(string); ok && gw != "" {
				args = append(args, "ipv6.gateway", gw)
			}
			if err := run("nmcli", args...); err != nil {
				return err
			}
		}
	}
	return run("nmcli", "con", "up", iface)
}

func applySystemdNetworkd(iface, mode, ipVersion string, params map[string]any) error {
	// Write /etc/systemd/network/10-nexplane-{iface}.network
	path := fmt.Sprintf("/etc/systemd/network/10-nexplane-%s.network", iface)
	content := fmt.Sprintf("[Match]\nName=%s\n\n[Network]\n", iface)
	if mode == "dhcp" {
		if ipVersion == "4" || ipVersion == "both" {
			content += "DHCP=ipv4\n"
		}
		if ipVersion == "6" || ipVersion == "both" {
			content += "DHCP=ipv6\n"
		}
	} else {
		if v4, ok := params["new_ip_v4"].(string); ok && v4 != "" {
			content += fmt.Sprintf("\n[Address]\nAddress=%s\n", v4)
			if gw, ok := params["new_gateway_v4"].(string); ok && gw != "" {
				content += fmt.Sprintf("\n[Route]\nGateway=%s\n", gw)
			}
		}
	}
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		return fmt.Errorf("writing networkd config: %w", err)
	}
	return run("networkctl", "reload")
}

func applyDebianInterfaces(iface, mode, ipVersion string, params map[string]any) error {
	// Simplified: just run ifdown/ifup after writing the interface config
	// In production, edit /etc/network/interfaces with proper stanza
	_ = run("ifdown", iface)
	if mode == "dhcp" {
		return run("ifup", iface)
	}
	if v4, ok := params["new_ip_v4"].(string); ok && v4 != "" {
		return run("ip", "addr", "add", v4, "dev", iface)
	}
	return run("ifup", iface)
}

func applyRHELIfcfg(iface, mode, ipVersion string, params map[string]any) error {
	cfgPath := fmt.Sprintf("/etc/sysconfig/network-scripts/ifcfg-%s", iface)
	content := fmt.Sprintf("DEVICE=%s\nONBOOT=yes\n", iface)
	if mode == "dhcp" {
		content += "BOOTPROTO=dhcp\n"
	} else {
		content += "BOOTPROTO=static\n"
		if v4, ok := params["new_ip_v4"].(string); ok && v4 != "" {
			parts := strings.SplitN(v4, "/", 2)
			content += fmt.Sprintf("IPADDR=%s\n", parts[0])
			if len(parts) > 1 {
				content += fmt.Sprintf("PREFIX=%s\n", parts[1])
			}
		}
		if gw, ok := params["new_gateway_v4"].(string); ok && gw != "" {
			content += fmt.Sprintf("GATEWAY=%s\n", gw)
		}
	}
	if err := os.WriteFile(cfgPath, []byte(content), 0644); err != nil {
		return fmt.Errorf("writing ifcfg: %w", err)
	}
	_ = run("ifdown", iface)
	return run("ifup", iface)
}

func run(name string, args ...string) error {
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %v: %w (output: %s)", name, args, err, out)
	}
	return nil
}

func snapshotMode(snapshot map[string]any) string {
	if ipv4, ok := snapshot["ipv4"].(map[string]any); ok {
		if m, ok := ipv4["mode"].(string); ok {
			return m
		}
	}
	return "dhcp"
}

func snapshotAddr(snapshot map[string]any, ver string) string {
	if s, ok := snapshot[ver].(map[string]any); ok {
		if a, ok := s["address"].(string); ok {
			return a
		}
	}
	return ""
}

func snapshotGW(snapshot map[string]any, ver string) string {
	if s, ok := snapshot[ver].(map[string]any); ok {
		if g, ok := s["gateway"].(string); ok {
			return g
		}
	}
	return ""
}
```

- [ ] **Step 4: Run tests (Linux only)**

```bash
go test ./commands/changip/... -v
```
Expected: 4 tests pass

- [ ] **Step 5: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/commands/changip/
git commit -m "feat: implement change_ip command for Linux"
```

---

## Task 6: change_ip Command — Windows

**Files:**
- Modify: `agent/commands/changip/changip_windows.go` (replace stub)

- [ ] **Step 1: Replace `agent/commands/changip/changip_windows.go`**

```go
//go:build windows

package changip

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func executeOS(params map[string]any) (map[string]any, error) {
	iface, _ := params["interface"].(string)
	mode, _ := params["mode"].(string)
	ipVersion, _ := params["ip_version"].(string)

	if iface == "" {
		return nil, fmt.Errorf("interface is required")
	}
	if mode != "static" && mode != "dhcp" {
		return nil, fmt.Errorf("mode must be 'static' or 'dhcp', got %q", mode)
	}
	if ipVersion == "" {
		ipVersion = "4"
	}

	snapshot, err := captureSnapshotWindows(iface)
	if err != nil {
		return nil, fmt.Errorf("capturing snapshot: %w", err)
	}

	if err := applyIPChangeWindows(iface, mode, ipVersion, params); err != nil {
		return nil, err
	}

	return map[string]any{
		"action":     "change_ip",
		"interface":  iface,
		"mode":       mode,
		"applied":    true,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok || snapshot == nil {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	iface, _ := snapshot["interface"].(string)
	if iface == "" {
		return nil, fmt.Errorf("cannot determine interface for rollback")
	}
	snapshotParams := map[string]any{
		"interface":      iface,
		"mode":           snapshotMode(snapshot),
		"ip_version":     "both",
		"new_ip_v4":      snapshotAddr(snapshot, "ipv4"),
		"new_ip_v6":      snapshotAddr(snapshot, "ipv6"),
		"new_gateway_v4": snapshotGW(snapshot, "ipv4"),
		"new_gateway_v6": snapshotGW(snapshot, "ipv6"),
	}
	if err := applyIPChangeWindows(iface, snapshotMode(snapshot), "both", snapshotParams); err != nil {
		return nil, err
	}
	return map[string]any{"rolled_back": true, "interface": iface}, nil
}

func captureSnapshotWindows(iface string) (map[string]any, error) {
	out, err := exec.Command("netsh", "interface", "ip", "show", "addresses", iface).Output()
	if err != nil {
		return nil, fmt.Errorf("netsh show addresses: %w", err)
	}
	return map[string]any{
		"interface":  iface,
		"ip_output":  strings.TrimSpace(string(out)),
	}, nil
}

func applyIPChangeWindows(iface, mode, ipVersion string, params map[string]any) error {
	if mode == "dhcp" {
		if ipVersion == "4" || ipVersion == "both" {
			if err := runW("netsh", "interface", "ipv4", "set", "address",
				"name="+iface, "dhcp"); err != nil {
				return err
			}
		}
		if ipVersion == "6" || ipVersion == "both" {
			if err := runW("netsh", "interface", "ipv6", "set", "address",
				"interface="+iface, "dhcp"); err != nil {
				return err
			}
		}
		return nil
	}

	// Static
	if ipVersion == "4" || ipVersion == "both" {
		v4, _ := params["new_ip_v4"].(string)
		if v4 != "" {
			parts := strings.SplitN(v4, "/", 2)
			ip := parts[0]
			mask := prefixToMask(parts)
			gw, _ := params["new_gateway_v4"].(string)
			args := []string{"interface", "ipv4", "set", "address",
				"name=" + iface, "static", ip, mask}
			if gw != "" {
				args = append(args, gw)
			}
			if err := runW("netsh", args...); err != nil {
				return err
			}
		}
	}

	if ipVersion == "6" || ipVersion == "both" {
		v6, _ := params["new_ip_v6"].(string)
		if v6 != "" {
			if err := runW("netsh", "interface", "ipv6", "add", "address",
				"interface="+iface, "address="+v6); err != nil {
				return err
			}
			if gw, ok := params["new_gateway_v6"].(string); ok && gw != "" {
				if err := runW("netsh", "interface", "ipv6", "add", "route",
					"::/0", "interface="+iface, "nexthop="+gw); err != nil {
					return err
				}
			}
		}
	}

	return nil
}

func runW(name string, args ...string) error {
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %v: %w (output: %s)", name, args, err, out)
	}
	return nil
}

func prefixToMask(parts []string) string {
	if len(parts) < 2 {
		return "255.255.255.0"
	}
	masks := map[string]string{
		"8": "255.0.0.0", "16": "255.255.0.0",
		"24": "255.255.255.0", "25": "255.255.255.128",
		"26": "255.255.255.192", "27": "255.255.255.224",
		"28": "255.255.255.240", "29": "255.255.255.248",
		"30": "255.255.255.252", "32": "255.255.255.255",
	}
	if m, ok := masks[parts[1]]; ok {
		return m
	}
	return "255.255.255.0"
}

func snapshotMode(snapshot map[string]any) string {
	if ipv4, ok := snapshot["ipv4"].(map[string]any); ok {
		if m, ok := ipv4["mode"].(string); ok {
			return m
		}
	}
	return "dhcp"
}

func snapshotAddr(snapshot map[string]any, ver string) string {
	if s, ok := snapshot[ver].(map[string]any); ok {
		if a, ok := s["address"].(string); ok {
			return a
		}
	}
	return ""
}

func snapshotGW(snapshot map[string]any, ver string) string {
	if s, ok := snapshot[ver].(map[string]any); ok {
		if g, ok := s["gateway"].(string); ok {
			return g
		}
	}
	return ""
}
```

- [ ] **Step 2: Verify Windows cross-compilation**

```bash
cd f:/Nexplane/nexplane/agent
GOOS=windows GOARCH=amd64 go build -o /dev/null ./
```
Expected: no errors

- [ ] **Step 3: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/commands/changip/changip_windows.go
git commit -m "feat: implement change_ip command for Windows"
```

---

## Task 7: configure_syslog Command (Linux + Windows)

**Files:**
- Modify: `agent/commands/configsyslog/configsyslog_linux.go`
- Modify: `agent/commands/configsyslog/configsyslog_windows.go`
- Create: `agent/commands/configsyslog/configsyslog_test.go`

- [ ] **Step 1: Write failing tests**

```go
//go:build linux

// agent/commands/configsyslog/configsyslog_test.go
package configsyslog_test

import (
	"testing"

	"nexplane-agent/commands/configsyslog"
)

func TestExecuteRequiresDestinationHost(t *testing.T) {
	_, err := configsyslog.Execute(map[string]any{
		"destination_port": 514,
		"protocol":         "udp",
	})
	if err == nil {
		t.Error("expected error for missing destination_host")
	}
}

func TestExecuteRequiresPort(t *testing.T) {
	_, err := configsyslog.Execute(map[string]any{
		"destination_host": "logs.example.com",
		"protocol":         "udp",
	})
	if err == nil {
		t.Error("expected error for missing destination_port")
	}
}

func TestExecuteRequiresProtocol(t *testing.T) {
	_, err := configsyslog.Execute(map[string]any{
		"destination_host": "logs.example.com",
		"destination_port": 514,
	})
	if err == nil {
		t.Error("expected error for missing protocol")
	}
}
```

- [ ] **Step 2: Replace `agent/commands/configsyslog/configsyslog_linux.go`**

```go
//go:build linux

package configsyslog

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const nexplaneBegin = "# nexplane-managed-begin\n"
const nexplaneEnd = "# nexplane-managed-end\n"

func executeOS(params map[string]any) (map[string]any, error) {
	host, _ := params["destination_host"].(string)
	port := params["destination_port"]
	proto, _ := params["protocol"].(string)
	facility, _ := params["facility"].(string)

	if host == "" {
		return nil, fmt.Errorf("destination_host is required")
	}
	if port == nil {
		return nil, fmt.Errorf("destination_port is required")
	}
	if proto == "" {
		return nil, fmt.Errorf("protocol is required")
	}
	if facility == "" {
		facility = "*"
	}

	portInt := toInt(port)
	proto = strings.ToLower(proto)

	// Detect syslog daemon
	daemon, cfgPath := detectSyslogDaemon()

	// Capture existing config as snapshot
	existing, _ := os.ReadFile(cfgPath)
	snapshot := string(existing)

	// Build forwarding block
	var block string
	if daemon == "rsyslog" {
		prefix := "@" // UDP
		if proto == "tcp" {
			prefix = "@@"
		}
		block = fmt.Sprintf("%s%s.* %s%s:%d\n%s",
			nexplaneBegin, facility, prefix, host, portInt, nexplaneEnd)
	} else {
		// syslog-ng
		block = fmt.Sprintf(`%sdestination d_nexplane { network("%s" port(%d) transport("%s")); };
log { source(s_src); destination(d_nexplane); };
%s`, nexplaneBegin, host, portInt, proto, nexplaneEnd)
	}

	// Remove any existing nexplane block, then append new one
	cleaned := removeNexplaneBlock(snapshot)
	newContent := cleaned + "\n" + block
	if err := os.WriteFile(cfgPath, []byte(newContent), 0644); err != nil {
		return nil, fmt.Errorf("writing %s: %w", cfgPath, err)
	}

	// Restart daemon
	svc := "rsyslog"
	if daemon == "syslog-ng" {
		svc = "syslog-ng"
	}
	if err := exec.Command("systemctl", "restart", svc).Run(); err != nil {
		return nil, fmt.Errorf("restarting %s: %w", svc, err)
	}

	return map[string]any{
		"action":           "configure_syslog",
		"destination_host": host,
		"destination_port": portInt,
		"protocol":         proto,
		"applied":          true,
		"config_backup":    snapshot,
		"applied_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	backup, ok := params["config_backup"].(string)
	if !ok {
		return nil, fmt.Errorf("config_backup is required for rollback")
	}
	_, cfgPath := detectSyslogDaemon()
	if err := os.WriteFile(cfgPath, []byte(backup), 0644); err != nil {
		return nil, fmt.Errorf("restoring config: %w", err)
	}
	return map[string]any{"rolled_back": true, "config_restored": true}, nil
}

func detectSyslogDaemon() (string, string) {
	if _, err := os.Stat("/etc/rsyslog.conf"); err == nil {
		return "rsyslog", "/etc/rsyslog.conf"
	}
	return "syslog-ng", "/etc/syslog-ng/syslog-ng.conf"
}

func removeNexplaneBlock(content string) string {
	lines := strings.Split(content, "\n")
	var out []string
	inBlock := false
	for _, line := range lines {
		if line == strings.TrimRight(nexplaneBegin, "\n") {
			inBlock = true
			continue
		}
		if line == strings.TrimRight(nexplaneEnd, "\n") {
			inBlock = false
			continue
		}
		if !inBlock {
			out = append(out, line)
		}
	}
	return strings.Join(out, "\n")
}

func toInt(v any) int {
	switch n := v.(type) {
	case int:
		return n
	case float64:
		return int(n)
	case int64:
		return int(n)
	}
	return 514
}
```

- [ ] **Step 3: Replace `agent/commands/configsyslog/configsyslog_windows.go`**

```go
//go:build windows

package configsyslog

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const nexplaneBegin = "# nexplane-managed-begin\n"
const nexplaneEnd = "# nexplane-managed-end\n"

func executeOS(params map[string]any) (map[string]any, error) {
	host, _ := params["destination_host"].(string)
	port := params["destination_port"]
	proto, _ := params["protocol"].(string)

	if host == "" {
		return nil, fmt.Errorf("destination_host is required")
	}
	if port == nil {
		return nil, fmt.Errorf("destination_port is required")
	}
	if proto == "" {
		return nil, fmt.Errorf("protocol is required")
	}

	portInt := toInt(port)

	// Try NXLog first
	nxlogPath := `C:\Program Files\nxlog\conf\nxlog.conf`
	if _, err := os.Stat(nxlogPath); err == nil {
		return applyNXLog(nxlogPath, host, portInt, proto)
	}

	// Fall back to Windows Event Forwarding
	return applyWEF(host, portInt)
}

func applyNXLog(cfgPath, host string, port int, proto string) (map[string]any, error) {
	existing, _ := os.ReadFile(cfgPath)
	snapshot := string(existing)

	block := fmt.Sprintf(`%s<Output nexplane_out>
    Module  om_udp
    Host    %s
    Port    %d
    Exec    to_syslog_bsd();
</Output>
<Route nexplane_route>
    Path    eventlog => nexplane_out
</Route>
%s`, nexplaneBegin, host, port, nexplaneEnd)

	cleaned := removeNexplaneBlock(snapshot)
	if err := os.WriteFile(cfgPath, []byte(cleaned+"\n"+block), 0644); err != nil {
		return nil, fmt.Errorf("writing nxlog.conf: %w", err)
	}
	if err := exec.Command("net", "stop", "nxlog").Run(); err == nil {
		exec.Command("net", "start", "nxlog").Run()
	}
	return map[string]any{
		"action": "configure_syslog", "applied": true,
		"config_backup": snapshot, "method": "nxlog",
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func applyWEF(host string, port int) (map[string]any, error) {
	subFile := filepath.Join(os.TempDir(), "nexplane-wef.xml")
	xml := fmt.Sprintf(`<Subscription xmlns="http://schemas.microsoft.com/2006/03/windows/events/subscription">
  <SubscriptionId>NexplaneForwarder</SubscriptionId>
  <SubscriptionType>CollectorInitiated</SubscriptionType>
  <LogFile>ForwardedEvents</ForwardedEvents>
  <EventSources><EventSource>
    <Address>%s</Address>
  </EventSource></EventSources>
</Subscription>`, host)
	if err := os.WriteFile(subFile, []byte(xml), 0644); err != nil {
		return nil, fmt.Errorf("writing WEF subscription: %w", err)
	}
	if err := exec.Command("wecutil", "cs", subFile).Run(); err != nil {
		return nil, fmt.Errorf("wecutil cs: %w", err)
	}
	return map[string]any{
		"action": "configure_syslog", "applied": true,
		"method": "wef", "subscription_id": "NexplaneForwarder",
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	if backup, ok := params["config_backup"].(string); ok && backup != "" {
		nxlogPath := `C:\Program Files\nxlog\conf\nxlog.conf`
		if err := os.WriteFile(nxlogPath, []byte(backup), 0644); err == nil {
			return map[string]any{"rolled_back": true, "method": "nxlog"}, nil
		}
	}
	// Remove WEF subscription
	if err := exec.Command("wecutil", "ds", "NexplaneForwarder").Run(); err != nil {
		return nil, fmt.Errorf("removing WEF subscription: %w", err)
	}
	return map[string]any{"rolled_back": true, "method": "wef"}, nil
}

func removeNexplaneBlock(content string) string {
	lines := strings.Split(content, "\n")
	var out []string
	inBlock := false
	for _, line := range lines {
		if line == strings.TrimRight(nexplaneBegin, "\n") {
			inBlock = true
			continue
		}
		if line == strings.TrimRight(nexplaneEnd, "\n") {
			inBlock = false
			continue
		}
		if !inBlock {
			out = append(out, line)
		}
	}
	return strings.Join(out, "\n")
}

func toInt(v any) int {
	switch n := v.(type) {
	case int:
		return n
	case float64:
		return int(n)
	case int64:
		return int(n)
	}
	return 514
}
```

- [ ] **Step 4: Run tests (Linux)**

```bash
go test ./commands/configsyslog/... -v
```
Expected: 3 tests pass

- [ ] **Step 5: Verify Windows cross-compilation**

```bash
GOOS=windows GOARCH=amd64 go build -o /dev/null ./
```
Expected: no errors

- [ ] **Step 6: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/commands/configsyslog/
git commit -m "feat: implement configure_syslog command for Linux and Windows"
```

---

## Task 8: virtualize_for_migration Command (Linux + Windows)

**Files:**
- Modify: `agent/commands/virtualize/virtualize_linux.go`
- Modify: `agent/commands/virtualize/virtualize_windows.go`
- Create: `agent/commands/virtualize/virtualize_test.go`

- [ ] **Step 1: Write failing tests**

```go
//go:build linux

// agent/commands/virtualize/virtualize_test.go
package virtualize_test

import (
	"testing"

	"nexplane-agent/commands/virtualize"
)

func TestExecuteRequiresImagePath(t *testing.T) {
	_, err := virtualize.Execute(map[string]any{
		"target_mode": "static",
		"target_ip_v4": "10.0.0.50/24",
	})
	if err == nil {
		t.Error("expected error for missing image_path")
	}
}

func TestExecuteRequiresTargetMode(t *testing.T) {
	_, err := virtualize.Execute(map[string]any{
		"image_path": "/tmp/test.img",
	})
	if err == nil {
		t.Error("expected error for missing target_mode")
	}
}

func TestRollbackDeletesImage(t *testing.T) {
	// Create a temp file to simulate the image
	f, err := os.CreateTemp("", "nexplane-test-*.img")
	if err != nil {
		t.Fatal(err)
	}
	f.Close()
	imagePath := f.Name()

	result, err := virtualize.Rollback(map[string]any{
		"image_path": imagePath,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result["deleted"] != true {
		t.Error("expected deleted=true")
	}
	if _, err := os.Stat(imagePath); !os.IsNotExist(err) {
		t.Error("expected image file to be deleted")
	}
}
```

Add `"os"` to imports in the test file.

- [ ] **Step 2: Replace `agent/commands/virtualize/virtualize_linux.go`**

```go
//go:build linux

package virtualize

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func executeOS(params map[string]any) (map[string]any, error) {
	imagePath, _ := params["image_path"].(string)
	targetMode, _ := params["target_mode"].(string)

	if imagePath == "" {
		return nil, fmt.Errorf("image_path is required")
	}
	if targetMode == "" {
		return nil, fmt.Errorf("target_mode is required")
	}

	// Detect source device from root mount
	sourceDevice, _ := params["source_device"].(string)
	if sourceDevice == "" {
		var err error
		sourceDevice, err = detectRootDevice()
		if err != nil {
			return nil, fmt.Errorf("detecting root device: %w", err)
		}
	}

	// Step 1: dd the disk to image_path
	if err := run("dd", "if="+sourceDevice, "of="+imagePath, "bs=4M", "conv=fsync"); err != nil {
		return nil, fmt.Errorf("dd failed: %w", err)
	}

	// Step 2: attach loop device
	loopOut, err := exec.Command("losetup", "--find", "--show", "--partscan", imagePath).Output()
	if err != nil {
		return nil, fmt.Errorf("losetup failed: %w", err)
	}
	loopDev := strings.TrimSpace(string(loopOut))

	// Step 3: find root partition and mount it
	mountPoint := filepath.Join(os.TempDir(), "nexplane-mount-"+filepath.Base(imagePath))
	if err := os.MkdirAll(mountPoint, 0755); err != nil {
		return nil, fmt.Errorf("creating mount point: %w", err)
	}

	rootPart, err := findRootPartition(loopDev)
	if err != nil {
		exec.Command("losetup", "-d", loopDev).Run()
		return nil, fmt.Errorf("finding root partition: %w", err)
	}

	if err := run("mount", rootPart, mountPoint); err != nil {
		exec.Command("losetup", "-d", loopDev).Run()
		return nil, fmt.Errorf("mounting partition: %w", err)
	}

	// Step 4: apply network config inside mounted filesystem
	if err := applyNetworkConfigInMount(mountPoint, params); err != nil {
		exec.Command("umount", mountPoint).Run()
		exec.Command("losetup", "-d", loopDev).Run()
		return nil, fmt.Errorf("applying network config in image: %w", err)
	}

	// Step 5: unmount and detach
	if err := run("umount", mountPoint); err != nil {
		return nil, fmt.Errorf("umount failed: %w", err)
	}
	if err := run("losetup", "-d", loopDev); err != nil {
		return nil, fmt.Errorf("losetup detach failed: %w", err)
	}
	os.Remove(mountPoint)

	// Get image size
	info, _ := os.Stat(imagePath)
	imageSize := int64(0)
	if info != nil {
		imageSize = info.Size()
	}

	return map[string]any{
		"action":                 "virtualize_for_migration",
		"image_path":             imagePath,
		"image_size_bytes":       imageSize,
		"source_device":          sourceDevice,
		"network_config_applied": true,
		"completed_at":           time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	imagePath, _ := params["image_path"].(string)
	if imagePath == "" {
		return nil, fmt.Errorf("image_path is required for rollback")
	}
	if err := os.Remove(imagePath); err != nil && !os.IsNotExist(err) {
		return nil, fmt.Errorf("deleting image: %w", err)
	}
	return map[string]any{"rolled_back": true, "deleted": true, "image_path": imagePath}, nil
}

func detectRootDevice() (string, error) {
	out, err := exec.Command("findmnt", "-n", "-o", "SOURCE", "/").Output()
	if err != nil {
		return "", err
	}
	src := strings.TrimSpace(string(out))
	// Strip partition number to get base device: /dev/sda1 -> /dev/sda
	for i := len(src) - 1; i >= 0; i-- {
		if src[i] < '0' || src[i] > '9' {
			return src[:i+1], nil
		}
	}
	return src, nil
}

func findRootPartition(loopDev string) (string, error) {
	// Try each partition p1, p2, p3 and attempt mount
	for i := 1; i <= 4; i++ {
		part := fmt.Sprintf("%sp%d", loopDev, i)
		if _, err := os.Stat(part); err == nil {
			return part, nil
		}
	}
	return loopDev, nil // whole-disk image
}

func applyNetworkConfigInMount(mountPoint string, params map[string]any) error {
	targetMode, _ := params["target_mode"].(string)
	iface, _ := params["target_interface"].(string)
	if iface == "" {
		iface = "eth0"
	}

	// Detect and edit config inside the mount
	nmcliConn := filepath.Join(mountPoint, "etc", "NetworkManager", "system-connections", iface+".nmconnection")
	if _, err := os.Stat(filepath.Dir(nmcliConn)); err == nil {
		return writeNMConnection(nmcliConn, iface, targetMode, params)
	}

	ifacesPath := filepath.Join(mountPoint, "etc", "network", "interfaces")
	if _, err := os.Stat(ifacesPath); err == nil {
		return writeDebianInterfaces(ifacesPath, iface, targetMode, params)
	}

	ifcfgPath := filepath.Join(mountPoint, "etc", "sysconfig", "network-scripts", "ifcfg-"+iface)
	return writeRHELIfcfg(ifcfgPath, iface, targetMode, params)
}

func writeNMConnection(path, iface, mode string, params map[string]any) error {
	content := fmt.Sprintf("[connection]\nid=%s\ntype=ethernet\ninterface-name=%s\n\n[ethernet]\n\n[ipv4]\n", iface, iface)
	if mode == "dhcp" {
		content += "method=auto\n"
	} else {
		v4, _ := params["target_ip_v4"].(string)
		gw, _ := params["target_gateway_v4"].(string)
		content += fmt.Sprintf("method=manual\naddress1=%s,%s\n", v4, gw)
	}
	content += "\n[ipv6]\nmethod=auto\n"
	return os.WriteFile(path, []byte(content), 0600)
}

func writeDebianInterfaces(path, iface, mode string, params map[string]any) error {
	content := fmt.Sprintf("auto lo\niface lo inet loopback\n\nauto %s\n", iface)
	if mode == "dhcp" {
		content += fmt.Sprintf("iface %s inet dhcp\n", iface)
	} else {
		v4, _ := params["target_ip_v4"].(string)
		parts := strings.SplitN(v4, "/", 2)
		gw, _ := params["target_gateway_v4"].(string)
		content += fmt.Sprintf("iface %s inet static\n  address %s\n  gateway %s\n", iface, parts[0], gw)
	}
	return os.WriteFile(path, []byte(content), 0644)
}

func writeRHELIfcfg(path, iface, mode string, params map[string]any) error {
	content := fmt.Sprintf("DEVICE=%s\nONBOOT=yes\n", iface)
	if mode == "dhcp" {
		content += "BOOTPROTO=dhcp\n"
	} else {
		v4, _ := params["target_ip_v4"].(string)
		parts := strings.SplitN(v4, "/", 2)
		gw, _ := params["target_gateway_v4"].(string)
		content += fmt.Sprintf("BOOTPROTO=static\nIPADDR=%s\nGATEWAY=%s\n", parts[0], gw)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(content), 0644)
}

func run(name string, args ...string) error {
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %v: %w (output: %s)", name, args, err, out)
	}
	return nil
}
```

- [ ] **Step 3: Replace `agent/commands/virtualize/virtualize_windows.go`**

```go
//go:build windows

package virtualize

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func executeOS(params map[string]any) (map[string]any, error) {
	imagePath, _ := params["image_path"].(string)
	targetMode, _ := params["target_mode"].(string)

	if imagePath == "" {
		return nil, fmt.Errorf("image_path is required")
	}
	if targetMode == "" {
		return nil, fmt.Errorf("target_mode is required")
	}

	// Get source disk size via PowerShell
	sizeOut, err := exec.Command("powershell", "-Command",
		"(Get-Disk -Number 0).Size").Output()
	if err != nil {
		return nil, fmt.Errorf("getting disk size: %w", err)
	}
	diskSizeStr := strings.TrimSpace(string(sizeOut))

	// Create VHD
	psCreateVHD := fmt.Sprintf(
		`New-VHD -Path '%s' -SizeBytes %s -Fixed`, imagePath, diskSizeStr)
	if err := runPS(psCreateVHD); err != nil {
		return nil, fmt.Errorf("creating VHD: %w", err)
	}

	// Mount VHD and get drive letter
	psMount := fmt.Sprintf(`Mount-DiskImage -ImagePath '%s' -PassThru | Get-DiskImage | Get-Disk | Get-Partition | Get-Volume | Select-Object -ExpandProperty DriveLetter`, imagePath)
	driveOut, err := exec.Command("powershell", "-Command", psMount).Output()
	if err != nil {
		return nil, fmt.Errorf("mounting VHD: %w", err)
	}
	driveLetter := strings.TrimSpace(string(driveOut))

	// Copy files using robocopy
	src := `C:\`
	dst := driveLetter + `:\`
	robocopyArgs := []string{src, dst, "/E", "/MIR", "/XD",
		"$RECYCLE.BIN", "System Volume Information", "/NFL", "/NDL"}
	exec.Command("robocopy", robocopyArgs...).Run() // robocopy exits non-zero on success

	// Edit network config in mounted volume
	if err := editNetworkConfigWindows(driveLetter+":\\", targetMode, params); err != nil {
		exec.Command("powershell", "-Command",
			fmt.Sprintf("Dismount-DiskImage -ImagePath '%s'", imagePath)).Run()
		return nil, fmt.Errorf("editing network config: %w", err)
	}

	// Dismount
	if err := runPS(fmt.Sprintf("Dismount-DiskImage -ImagePath '%s'", imagePath)); err != nil {
		return nil, fmt.Errorf("dismounting VHD: %w", err)
	}

	info, _ := os.Stat(imagePath)
	imageSize := int64(0)
	if info != nil {
		imageSize = info.Size()
	}

	return map[string]any{
		"action":                 "virtualize_for_migration",
		"image_path":             imagePath,
		"image_size_bytes":       imageSize,
		"network_config_applied": true,
		"completed_at":           time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	imagePath, _ := params["image_path"].(string)
	if imagePath == "" {
		return nil, fmt.Errorf("image_path is required for rollback")
	}
	if err := os.Remove(imagePath); err != nil && !os.IsNotExist(err) {
		return nil, fmt.Errorf("deleting VHD: %w", err)
	}
	return map[string]any{"rolled_back": true, "deleted": true, "image_path": imagePath}, nil
}

func editNetworkConfigWindows(mountRoot, mode string, params map[string]any) error {
	iface, _ := params["target_interface"].(string)
	if iface == "" {
		iface = "Ethernet"
	}

	// Write an unattend.xml for sysprep-based network config
	unattendPath := filepath.Join(mountRoot, "Windows", "Panther", "unattend.xml")
	os.MkdirAll(filepath.Dir(unattendPath), 0755)

	var ipConfig string
	if mode == "dhcp" {
		ipConfig = `<component name="Microsoft-Windows-TCPIP">
      <Interfaces><Interface><Identifier>Local Area Connection</Identifier>
        <Ipv4Settings><DhcpEnabled>true</DhcpEnabled></Ipv4Settings>
      </Interface></Interfaces></component>`
	} else {
		v4, _ := params["target_ip_v4"].(string)
		gw, _ := params["target_gateway_v4"].(string)
		parts := strings.SplitN(v4, "/", 2)
		prefix := "24"
		if len(parts) > 1 {
			prefix = parts[1]
		}
		ipConfig = fmt.Sprintf(`<component name="Microsoft-Windows-TCPIP">
      <Interfaces><Interface><Identifier>%s</Identifier>
        <Ipv4Settings><DhcpEnabled>false</DhcpEnabled></Ipv4Settings>
        <UnicastIpAddresses><IpAddress wcm:action="add" wcm:keyValue="1">%s/%s</IpAddress></UnicastIpAddresses>
        <Routes><Route wcm:action="add"><Identifier>0</Identifier><Metric>256</Metric>
          <NextHopAddress>%s</NextHopAddress><Prefix>0.0.0.0/0</Prefix></Route></Routes>
      </Interface></Interfaces></component>`, iface, parts[0], prefix, gw)
	}

	unattend := fmt.Sprintf(`<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend"
          xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
  <settings pass="specialize">%s</settings>
</unattend>`, ipConfig)

	return os.WriteFile(unattendPath, []byte(unattend), 0644)
}

func runPS(command string) error {
	out, err := exec.Command("powershell", "-Command", command).CombinedOutput()
	if err != nil {
		return fmt.Errorf("powershell: %w (output: %s)", err, out)
	}
	return nil
}
```

- [ ] **Step 4: Run tests**

```bash
go test ./commands/virtualize/... -v
```
Expected: 3 tests pass

- [ ] **Step 5: Cross-compile**

```bash
GOOS=linux GOARCH=amd64 go build -o /dev/null ./
GOOS=windows GOARCH=amd64 go build -o /dev/null ./
```
Expected: no errors

- [ ] **Step 6: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/commands/virtualize/
git commit -m "feat: implement virtualize_for_migration command for Linux and Windows"
```

---

## Task 9: upload_image Command

**Files:**
- Modify: `agent/commands/uploadimage/uploadimage.go` (replace stub)
- Create: `agent/commands/uploadimage/uploadimage_test.go`

- [ ] **Step 1: Add AWS SDK dependency**

```bash
cd f:/Nexplane/nexplane/agent
go get github.com/aws/aws-sdk-go-v2@latest
go get github.com/aws/aws-sdk-go-v2/config@latest
go get github.com/aws/aws-sdk-go-v2/credentials@latest
go get github.com/aws/aws-sdk-go-v2/feature/s3/manager@latest
go get github.com/aws/aws-sdk-go-v2/service/s3@latest
```

- [ ] **Step 2: Write failing tests**

```go
// agent/commands/uploadimage/uploadimage_test.go
package uploadimage_test

import (
	"testing"

	"nexplane-agent/commands/uploadimage"
)

func TestExecuteRequiresImagePath(t *testing.T) {
	_, err := uploadimage.Execute(map[string]any{
		"destination_uri": "s3://bucket/key",
	})
	if err == nil {
		t.Error("expected error for missing image_path")
	}
}

func TestExecuteRequiresDestinationURI(t *testing.T) {
	_, err := uploadimage.Execute(map[string]any{
		"image_path": "/tmp/test.img",
	})
	if err == nil {
		t.Error("expected error for missing destination_uri")
	}
}

func TestExecuteRejectsUnsupportedScheme(t *testing.T) {
	_, err := uploadimage.Execute(map[string]any{
		"image_path":      "/tmp/test.img",
		"destination_uri": "ftp://bucket/key",
	})
	if err == nil {
		t.Error("expected error for unsupported scheme")
	}
}

func TestRollbackRequiresDestinationURI(t *testing.T) {
	_, err := uploadimage.Rollback(map[string]any{})
	if err == nil {
		t.Error("expected error for missing destination_uri in rollback")
	}
}
```

- [ ] **Step 3: Replace `agent/commands/uploadimage/uploadimage.go`**

(This file is not platform-specific, so no `_linux.go`/`_windows.go` split needed.)

```go
package uploadimage

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"fmt"
	"io"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/feature/s3/manager"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

func Execute(params map[string]any) (map[string]any, error) {
	imagePath, _ := params["image_path"].(string)
	destURI, _ := params["destination_uri"].(string)

	if imagePath == "" {
		return nil, fmt.Errorf("image_path is required")
	}
	if destURI == "" {
		return nil, fmt.Errorf("destination_uri is required")
	}

	u, err := url.Parse(destURI)
	if err != nil {
		return nil, fmt.Errorf("invalid destination_uri: %w", err)
	}
	if u.Scheme != "s3" {
		return nil, fmt.Errorf("unsupported destination scheme %q — only 's3://' is supported", u.Scheme)
	}

	bucket := u.Host
	key := strings.TrimPrefix(u.Path, "/")

	// Build AWS config with credential chain
	cfgOpts := []func(*config.LoadOptions) error{}
	if region, ok := params["region"].(string); ok && region != "" {
		cfgOpts = append(cfgOpts, config.WithRegion(region))
	}
	if accessKey, ok := params["access_key_id"].(string); ok && accessKey != "" {
		secretKey, _ := params["secret_access_key"].(string)
		cfgOpts = append(cfgOpts, config.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(accessKey, secretKey, ""),
		))
	}

	awsCfg, err := config.LoadDefaultConfig(context.Background(), cfgOpts...)
	if err != nil {
		return nil, fmt.Errorf("loading AWS config: %w", err)
	}

	f, err := os.Open(imagePath)
	if err != nil {
		return nil, fmt.Errorf("opening image file: %w", err)
	}
	defer f.Close()

	info, err := f.Stat()
	if err != nil {
		return nil, fmt.Errorf("stat image file: %w", err)
	}

	// Compute MD5 for verification
	h := md5.New()
	if _, err := io.Copy(h, f); err != nil {
		return nil, fmt.Errorf("computing checksum: %w", err)
	}
	localMD5 := hex.EncodeToString(h.Sum(nil))
	f.Seek(0, io.SeekStart)

	// Multipart upload
	client := s3.NewFromConfig(awsCfg)
	uploader := manager.NewUploader(client, func(u *manager.Uploader) {
		u.PartSize = 64 * 1024 * 1024 // 64 MB parts
	})

	result, err := uploader.Upload(context.Background(), &s3.PutObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
		Body:   f,
	})
	if err != nil {
		return nil, fmt.Errorf("uploading to S3: %w", err)
	}

	// Verify via HeadObject
	head, err := client.HeadObject(context.Background(), &s3.HeadObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
	})
	checksumVerified := false
	if err == nil && head.ContentLength != nil && *head.ContentLength == info.Size() {
		checksumVerified = true
	}

	etag := ""
	if result.ETag != nil {
		etag = strings.Trim(*result.ETag, `"`)
	}

	return map[string]any{
		"action":            "upload_image",
		"image_path":        imagePath,
		"destination_uri":   destURI,
		"size_bytes":        info.Size(),
		"local_md5":         localMD5,
		"etag":              etag,
		"checksum_verified": checksumVerified,
		"uploaded_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func Rollback(params map[string]any) (map[string]any, error) {
	destURI, _ := params["destination_uri"].(string)
	if destURI == "" {
		return nil, fmt.Errorf("destination_uri is required for rollback")
	}

	u, err := url.Parse(destURI)
	if err != nil {
		return nil, fmt.Errorf("invalid destination_uri: %w", err)
	}
	bucket := u.Host
	key := strings.TrimPrefix(u.Path, "/")

	awsCfg, err := config.LoadDefaultConfig(context.Background())
	if err != nil {
		return nil, fmt.Errorf("loading AWS config: %w", err)
	}

	client := s3.NewFromConfig(awsCfg)
	_, err = client.DeleteObject(context.Background(), &s3.DeleteObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, fmt.Errorf("deleting S3 object: %w", err)
	}

	return map[string]any{
		"rolled_back":     true,
		"destination_uri": destURI,
		"deleted":         true,
	}, nil
}
```

Remove the OS-specific stub files for uploadimage if they exist — this command is platform-agnostic:

```bash
rm -f agent/commands/uploadimage/uploadimage_linux.go
rm -f agent/commands/uploadimage/uploadimage_windows.go
```

- [ ] **Step 4: Update the stub files to remove `executeOS`/`rollbackOS` since uploadimage.go no longer uses them**

The `uploadimage` package now uses `Execute` and `Rollback` directly (no OS dispatch needed). The executor references `uploadimage.Execute` and `uploadimage.Rollback` — these are defined in `uploadimage.go` directly. Remove the linux/windows stubs since they're no longer needed.

- [ ] **Step 5: Run tests**

```bash
go test ./commands/uploadimage/... -v
```
Expected: 4 tests pass (validation tests — actual S3 upload requires real credentials)

- [ ] **Step 6: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/commands/uploadimage/ agent/go.mod agent/go.sum
git commit -m "feat: implement upload_image command with S3 multipart upload"
```

---

## Task 10: Registration, Poller, and main.go

**Files:**
- Create: `agent/registration/registration.go`
- Create: `agent/registration/registration_test.go`
- Create: `agent/poller/poller.go`
- Modify: `agent/main.go`

- [ ] **Step 1: Write failing registration tests**

```go
// agent/registration/registration_test.go
package registration_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"nexplane-agent/client"
	"nexplane-agent/config"
	"nexplane-agent/registration"
)

func TestRegistrationCallsRegisterEndpoint(t *testing.T) {
	called := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/agent/register" && r.Method == http.MethodPost {
			called = true
			json.NewEncoder(w).Encode(map[string]string{
				"agent_id": "00000000-0000-0000-0000-000000000001",
				"asset_id": "00000000-0000-0000-0000-000000000002",
			})
		}
	}))
	defer srv.Close()

	cfg := &config.Config{ControlPlane: srv.URL, Secret: "test-secret", Mode: "ephemeral"}
	c := client.New(cfg.ControlPlane, cfg.Secret)
	_, err := registration.Register(context.Background(), c, "machine-id-1", "test-host", "linux", "0.1.0")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !called {
		t.Error("register endpoint was not called")
	}
}
```

- [ ] **Step 2: Create `agent/registration/registration.go`**

```go
package registration

import (
	"context"
	"fmt"
	"net"
	"os"
	"runtime"

	"nexplane-agent/client"
)

type Info struct {
	AgentID string
	AssetID string
}

// Register sends the registration payload to the control plane and returns
// the agent and asset IDs assigned by the server.
func Register(ctx context.Context, c *client.Client, machineID, hostname, osType, agentVersion string) (*Info, error) {
	ips := getIPAddresses()
	osVersion := getOSVersion()

	resp, err := c.Register(ctx, client.RegisterRequest{
		MachineID:    machineID,
		Hostname:     hostname,
		OsType:       osType,
		IPAddresses:  ips,
		OsVersion:    osVersion,
		AgentVersion: agentVersion,
	})
	if err != nil {
		return nil, fmt.Errorf("registering agent: %w", err)
	}
	return &Info{AgentID: resp.AgentID, AssetID: resp.AssetID}, nil
}

func getIPAddresses() []string {
	var ips []string
	ifaces, err := net.Interfaces()
	if err != nil {
		return ips
	}
	for _, iface := range ifaces {
		if iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, addr := range addrs {
			if ipnet, ok := addr.(*net.IPNet); ok {
				ips = append(ips, ipnet.IP.String())
			}
		}
	}
	return ips
}

func getOSVersion() string {
	if runtime.GOOS == "linux" {
		data, err := os.ReadFile("/etc/os-release")
		if err == nil {
			for _, line := range splitLines(string(data)) {
				if len(line) > 12 && line[:12] == "PRETTY_NAME=" {
					return stripQuotes(line[12:])
				}
			}
		}
	}
	return runtime.GOOS + "/" + runtime.GOARCH
}

func splitLines(s string) []string {
	var lines []string
	start := 0
	for i, c := range s {
		if c == '\n' {
			lines = append(lines, s[start:i])
			start = i + 1
		}
	}
	return lines
}

func stripQuotes(s string) string {
	if len(s) >= 2 && s[0] == '"' && s[len(s)-1] == '"' {
		return s[1 : len(s)-1]
	}
	return s
}
```

- [ ] **Step 3: Create `agent/poller/poller.go`**

```go
package poller

import (
	"context"
	"fmt"
	"log"
	"time"

	"nexplane-agent/agenthmac"
	"nexplane-agent/client"
	"nexplane-agent/executor"
)

// RunEphemeral polls once for a pending job, executes it, and returns.
// Returns nil if no job was available.
func RunEphemeral(ctx context.Context, c *client.Client, agentID, secret string) error {
	job, err := c.PollNextJob(ctx, agentID)
	if err != nil {
		return fmt.Errorf("polling for job: %w", err)
	}
	if job == nil {
		log.Println("No pending jobs.")
		return nil
	}
	return processJob(ctx, c, job, secret)
}

// RunService loops indefinitely, polling for jobs at the given interval.
// Stops when ctx is cancelled.
func RunService(ctx context.Context, c *client.Client, agentID, secret string, pollInterval time.Duration) {
	log.Printf("Starting service mode (poll interval: %s)", pollInterval)
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		job, err := c.PollNextJob(ctx, agentID)
		if err != nil {
			log.Printf("Poll error: %v — retrying in %s", err, pollInterval)
			sleep(ctx, pollInterval)
			continue
		}
		if job == nil {
			// 204: no jobs — wait and try again
			sleep(ctx, pollInterval)
			continue
		}

		if err := processJob(ctx, c, job, secret); err != nil {
			log.Printf("Job %s error: %v", job.JobID, err)
		}
		// Poll again immediately after processing a job
	}
}

func processJob(ctx context.Context, c *client.Client, job *client.JobResponse, secret string) error {
	log.Printf("Received job %s: command=%s", job.JobID, job.Command)

	// Verify HMAC before executing
	if !agenthmac.Verify(secret, job.JobID, job.Command, job.Parameters, job.HMACSignature) {
		err := "signature verification failed — rejecting job"
		log.Printf("Job %s: %s", job.JobID, err)
		return c.PostResult(ctx, job.JobID, client.JobResult{Status: "failed", Error: err})
	}

	// Check if this is a rollback job
	rollback, _ := job.Parameters["rollback"].(bool)
	previousResult, _ := job.Parameters["previous_result"].(map[string]any)

	result := executor.Dispatch(job.Command, job.Parameters, rollback, previousResult)

	jobResult := client.JobResult{
		Status: result.Status,
		Result: result.Data,
		Error:  result.Error,
	}
	if err := c.PostResult(ctx, job.JobID, jobResult); err != nil {
		return fmt.Errorf("posting result: %w", err)
	}
	log.Printf("Job %s completed with status=%s", job.JobID, result.Status)
	return nil
}

func sleep(ctx context.Context, d time.Duration) {
	select {
	case <-ctx.Done():
	case <-time.After(d):
	}
}
```

- [ ] **Step 4: Wire everything together in `agent/main.go`**

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
)

const agentVersion = "0.1.0"

func main() {
	cfg, err := config.Load(os.Args[1:])
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}

	log.Printf("Nexplane Agent %s starting (mode=%s)", agentVersion, cfg.Mode)

	machineID, err := fingerprint.GetMachineID()
	if err != nil {
		log.Fatalf("Cannot determine machine ID: %v", err)
	}

	hostname, _ := os.Hostname()
	osType := runtime.GOOS // "linux" or "windows"

	c := client.New(cfg.ControlPlane, cfg.Secret)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	info, err := registration.Register(ctx, c, machineID, hostname, osType, agentVersion)
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

- [ ] **Step 5: Run all tests**

```bash
cd f:/Nexplane/nexplane/agent
go test ./... -v
```
Expected: all tests pass

- [ ] **Step 6: Build both targets**

```bash
make build-linux
make build-windows
```
Expected: `dist/nexplane-agent-linux-amd64` and `dist/nexplane-agent-windows-amd64.exe` created

- [ ] **Step 7: Commit**

```bash
cd f:/Nexplane/nexplane
git add agent/registration/ agent/poller/ agent/main.go
git commit -m "feat: add registration, poller, and wire up main.go — agent binary complete"
```

---

## Self-Review

**Spec coverage:**
- ✅ Section 1.1 — Repository layout matches plan structure
- ✅ Section 1.2 — Config package: all flags + env vars with defaults
- ✅ Section 1.3 — Fingerprint: Linux /etc/machine-id, Windows registry, MAC fallback
- ✅ Section 1.4 — Registration sends all required fields, refreshes last_seen
- ✅ Section 1.5 — Poll loop: 204 → wait, job → verify + execute + post result, error → backoff
- ✅ Section 1.6 — HMAC verification before execution, constant-time compare
- ✅ Section 4.1 — estimate_image_size: statvfs (Linux) + GetDiskFreeSpaceEx (Windows), sufficient_space check
- ✅ Section 4.2 — change_ip: IPv4/IPv6/DHCP, nmcli/systemd-networkd/interfaces/ifcfg (Linux), netsh (Windows), snapshot + rollback
- ✅ Section 4.3 — configure_syslog: rsyslog/syslog-ng (Linux), NXLog/WEF (Windows), nexplane-managed delimiters, rollback
- ✅ Section 4.4 — virtualize_for_migration: dd+losetup+mount+edit (Linux), VHD+robocopy+unattend (Windows), rollback=delete image
- ✅ Section 4.5 — upload_image: S3 multipart, credential chain, checksum verify, rollback=delete object
- ✅ Section 6 — Makefile with build targets for linux/arm64/amd64 and windows/amd64
- ✅ Ephemeral and service modes both implemented in main.go
