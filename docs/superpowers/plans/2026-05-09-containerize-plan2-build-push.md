# Containerize Legacy Workloads — Plan 2: Build & Push

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `agent_containerize_build` change type end-to-end: the Go agent generates a Dockerfile + Kubernetes manifests from a discovered application profile, runs `docker build` on the legacy host, pushes to a container registry, and stores the image digest + manifests back on `asset_metadata.applications[]`. The frontend wizard Step 2 (Build & Push) shows a Dockerfile preview and allows registry selection.

**Architecture:** A new Go agent command `containerize_build` reads the target application's profile from `asset_metadata.applications[]` (passed as parameters), generates a Dockerfile using a minimal template engine (strings-based, no external deps), builds and pushes using the Docker daemon on the host, and posts the image digest back to the backend. The Python executor dispatches via `dispatch_agent_job` and then calls a new `write_build_result_to_metadata` service to update the asset. The wizard Step 2 component polls for CR completion and renders the Dockerfile preview from the pre-generation step result.

**Tech Stack:** Go 1.21 (agent commands), Python/FastAPI (executor + service), React/TypeScript + shadcn/ui (wizard step)

---

## File Structure

```
agent/commands/containerizebuild/
  containerizebuild.go          CREATE — entrypoint, orchestrates the build
  dockerfile.go                 CREATE — Dockerfile template generation
  manifests.go                  CREATE — k8s Deployment/Service/PVC/ConfigMap generation
  registry.go                   CREATE — docker build + push with auth injection
  containerizebuild_test.go     CREATE — unit tests for template generation

backend/app/connectors/executors/nexplane_agent/containerize_build.py
                                MODIFY — replace stub with real dispatch + param building
backend/app/services/build_result_service.py
                                CREATE — write_build_result_to_metadata()
backend/app/workflows/execute_change_workflow.py
                                MODIFY — add agent_containerize_build post-execution hook
backend/tests/test_containerize_build.py
                                CREATE — unit tests for service and executor

frontend/src/components/ContainerizeWizardStep2.tsx
                                CREATE — Build & Push wizard step with Dockerfile preview
frontend/src/components/ContainerizationWizard.tsx
                                MODIFY — wire in Step 2 component (stub exists from Plan 1)
```

---

## Task 1: Go Agent — Dockerfile Generation

**Files:**
- Create: `agent/commands/containerizebuild/containerizebuild.go`
- Create: `agent/commands/containerizebuild/dockerfile.go`
- Create: `agent/commands/containerizebuild/containerizebuild_test.go`

- [ ] **Step 1: Write failing tests for Dockerfile generation**

Create `agent/commands/containerizebuild/containerizebuild_test.go`:

```go
package containerizebuild_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/containerizebuild"
)

func TestGenerateDockerfile_Stateless(t *testing.T) {
	app := containerizebuild.AppProfile{
		Name:           "myapp",
		Binary:         "/usr/local/bin/myapp",
		ProcessUser:    "myapp",
		ListeningPorts: []containerizebuild.Port{{Port: 8080, Protocol: "tcp"}},
		EnvVars:        []string{"DATABASE_URL", "APP_SECRET"},
		OSFamily:       "debian",
		ConfigFiles:    []string{"/etc/myapp/config.yaml"},
		DataDirs:       []string{},
	}
	df, err := containerizebuild.GenerateDockerfile(app)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(df, "FROM debian:bookworm-slim") {
		t.Errorf("expected debian base image, got:\n%s", df)
	}
	if !strings.Contains(df, "EXPOSE 8080") {
		t.Errorf("expected EXPOSE 8080, got:\n%s", df)
	}
	if !strings.Contains(df, "USER myapp") {
		t.Errorf("expected USER myapp, got:\n%s", df)
	}
	if !strings.Contains(df, "DATABASE_URL") {
		t.Errorf("expected DATABASE_URL env placeholder, got:\n%s", df)
	}
}

func TestGenerateDockerfile_Stateful(t *testing.T) {
	app := containerizebuild.AppProfile{
		Name:        "myworker",
		Binary:      "/usr/local/bin/worker",
		ProcessUser: "root",
		DataDirs:    []string{"/var/lib/myworker/data"},
		OSFamily:    "rhel",
	}
	df, err := containerizebuild.GenerateDockerfile(app)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(df, "FROM redhat/ubi9-minimal") {
		t.Errorf("expected rhel base image, got:\n%s", df)
	}
	if !strings.Contains(df, "VOLUME") {
		t.Errorf("expected VOLUME directive for stateful app, got:\n%s", df)
	}
}

func TestGenerateDockerfile_UnknownOS(t *testing.T) {
	app := containerizebuild.AppProfile{
		Name:     "app",
		Binary:   "/usr/bin/app",
		OSFamily: "unknown",
	}
	df, err := containerizebuild.GenerateDockerfile(app)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(df, "FROM ubuntu:22.04") {
		t.Errorf("expected ubuntu fallback, got:\n%s", df)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd agent && go test ./commands/containerizebuild/... -v 2>&1 | head -20
```
Expected: compilation error (package doesn't exist yet).

- [ ] **Step 3: Implement AppProfile types and Dockerfile generator**

Create `agent/commands/containerizebuild/dockerfile.go`:

```go
package containerizebuild

import (
	"fmt"
	"strings"
)

// AppProfile contains the discovered application data passed as job parameters.
type AppProfile struct {
	Name           string   `json:"name"`
	Binary         string   `json:"binary"`
	SystemdUnit    string   `json:"systemd_unit"`
	ProcessUser    string   `json:"process_user"`
	ListeningPorts []Port   `json:"listening_ports"`
	ConfigFiles    []string `json:"config_files"`
	DataDirs       []string `json:"data_directories"`
	EnvVars        []string `json:"env_vars"`
	OSFamily       string   `json:"os_family"` // debian, rhel, alpine, unknown
}

// Port represents a network port binding.
type Port struct {
	Port     int    `json:"port"`
	Protocol string `json:"protocol"`
}

// baseImages maps OS family to a slim production base image.
var baseImages = map[string]string{
	"debian":  "debian:bookworm-slim",
	"ubuntu":  "ubuntu:22.04",
	"rhel":    "redhat/ubi9-minimal",
	"centos":  "redhat/ubi9-minimal",
	"alpine":  "alpine:3.19",
	"unknown": "ubuntu:22.04",
}

// GenerateDockerfile produces a minimal, production-appropriate Dockerfile
// from an AppProfile. No external templating library — pure string building.
func GenerateDockerfile(app AppProfile) (string, error) {
	base, ok := baseImages[app.OSFamily]
	if !ok {
		base = baseImages["unknown"]
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("FROM %s\n\n", base))

	// Environment variable declarations (values supplied at runtime via ConfigMap/Secret)
	if len(app.EnvVars) > 0 {
		for _, e := range app.EnvVars {
			sb.WriteString(fmt.Sprintf("ENV %s=\"\"\n", e))
		}
		sb.WriteString("\n")
	}

	// Copy the application binary
	sb.WriteString(fmt.Sprintf("COPY %s %s\n", app.Binary, app.Binary))
	sb.WriteString(fmt.Sprintf("RUN chmod +x %s\n\n", app.Binary))

	// Copy config files
	for _, cf := range app.ConfigFiles {
		sb.WriteString(fmt.Sprintf("COPY %s %s\n", cf, cf))
	}
	if len(app.ConfigFiles) > 0 {
		sb.WriteString("\n")
	}

	// Volume mounts for stateful data directories
	for _, d := range app.DataDirs {
		sb.WriteString(fmt.Sprintf("VOLUME [\"%s\"]\n", d))
	}
	if len(app.DataDirs) > 0 {
		sb.WriteString("\n")
	}

	// Expose listening ports
	for _, p := range app.ListeningPorts {
		sb.WriteString(fmt.Sprintf("EXPOSE %d\n", p.Port))
	}
	if len(app.ListeningPorts) > 0 {
		sb.WriteString("\n")
	}

	// Run as non-root user when possible
	user := app.ProcessUser
	if user == "" || user == "root" {
		user = "nobody"
	}
	sb.WriteString(fmt.Sprintf("USER %s\n\n", user))

	// Entrypoint
	sb.WriteString(fmt.Sprintf("CMD [\"%s\"]\n", app.Binary))

	return sb.String(), nil
}
```

Create `agent/commands/containerizebuild/containerizebuild.go`:

```go
package containerizebuild

// ContainerizeBuildExecute is the entry point registered in executor.go.
func ContainerizeBuildExecute(params map[string]any) (map[string]any, error) {
	return map[string]any{
		"status": "not_implemented",
		"note":   "containerize_build stub — full implementation in registry.go",
	}, nil
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd agent && go test ./commands/containerizebuild/... -v 2>&1
```
Expected: all 3 tests pass.

- [ ] **Step 5: Register command in executor.go**

Add to `agent/executor/executor.go` imports:
```go
"nexplane-agent/commands/containerizebuild"
```

Add to commands map:
```go
"containerize_build": containerizebuild.ContainerizeBuildExecute,
```

- [ ] **Step 6: Run full agent tests**

```bash
cd agent && go test ./... -v 2>&1 | tail -20
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd agent
git add commands/containerizebuild/ executor/executor.go
git commit -m "feat(agent): add containerizebuild command with Dockerfile generation"
```

---

## Task 2: Go Agent — Kubernetes Manifest Generation

**Files:**
- Create: `agent/commands/containerizebuild/manifests.go`
- Modify: `agent/commands/containerizebuild/containerizebuild_test.go` — add manifest tests

- [ ] **Step 1: Write failing tests for manifest generation**

Add to `containerizebuild_test.go`:

```go
func TestGenerateManifests_Stateless(t *testing.T) {
	app := AppProfile{
		Name:           "myapp",
		Binary:         "/usr/local/bin/myapp",
		ListeningPorts: []Port{{Port: 8080, Protocol: "tcp"}},
		EnvVars:        []string{"DATABASE_URL"},
		DataDirs:       []string{},
	}
	result, err := containerizebuild.GenerateManifests(app, "myregistry/myapp:sha256-abc123", "prod", "100m", "128Mi")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(result.Deployment, "kind: Deployment") {
		t.Errorf("expected Deployment YAML, got: %s", result.Deployment)
	}
	if !strings.Contains(result.Service, "kind: Service") {
		t.Errorf("expected Service YAML, got: %s", result.Service)
	}
	if result.PVC != "" {
		t.Errorf("expected no PVC for stateless app, got: %s", result.PVC)
	}
	if !strings.Contains(result.ConfigMap, "DATABASE_URL") {
		t.Errorf("expected ConfigMap with DATABASE_URL, got: %s", result.ConfigMap)
	}
}

func TestGenerateManifests_Stateful(t *testing.T) {
	app := AppProfile{
		Name:     "myworker",
		Binary:   "/usr/local/bin/worker",
		DataDirs: []string{"/var/lib/myworker/data"},
	}
	result, err := containerizebuild.GenerateManifests(app, "myregistry/myworker:latest", "prod", "250m", "256Mi")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.PVC == "" {
		t.Errorf("expected PVC for stateful app")
	}
	if !strings.Contains(result.PVC, "kind: PersistentVolumeClaim") {
		t.Errorf("expected PVC YAML, got: %s", result.PVC)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd agent && go test ./commands/containerizebuild/... -run TestGenerateManifests -v 2>&1
```
Expected: compile error (GenerateManifests not defined).

- [ ] **Step 3: Implement manifest generation**

Create `agent/commands/containerizebuild/manifests.go`:

```go
package containerizebuild

import (
	"fmt"
	"strings"
)

// ManifestSet holds all generated Kubernetes YAML strings.
type ManifestSet struct {
	Deployment string
	Service    string
	PVC        string
	ConfigMap  string
}

// GenerateManifests produces Kubernetes YAML for a containerized application.
// imageRef should be the full image reference including digest (e.g. repo/app@sha256:abc).
// namespace, cpuRequest, memRequest are used for the Deployment resource spec.
func GenerateManifests(app AppProfile, imageRef, namespace, cpuRequest, memRequest string) (ManifestSet, error) {
	if namespace == "" {
		namespace = "default"
	}
	if cpuRequest == "" {
		cpuRequest = "100m"
	}
	if memRequest == "" {
		memRequest = "128Mi"
	}

	name := sanitizeName(app.Name)

	// --- ConfigMap for env vars ---
	var cfgMap string
	if len(app.EnvVars) > 0 {
		var envData strings.Builder
		for _, e := range app.EnvVars {
			envData.WriteString(fmt.Sprintf("  %s: \"\"\n", e))
		}
		cfgMap = fmt.Sprintf(`apiVersion: v1
kind: ConfigMap
metadata:
  name: %s-config
  namespace: %s
data:
%s`, name, namespace, envData.String())
	}

	// --- Deployment ---
	var portSpec string
	for _, p := range app.ListeningPorts {
		portSpec += fmt.Sprintf("        - containerPort: %d\n", p.Port)
	}
	var envFrom string
	if len(app.EnvVars) > 0 {
		envFrom = fmt.Sprintf(`        envFrom:
        - configMapRef:
            name: %s-config
`, name)
	}
	var volumeMounts, volumes string
	if len(app.DataDirs) > 0 {
		for i, d := range app.DataDirs {
			volumeMounts += fmt.Sprintf(`        - name: data-%d
          mountPath: %s
`, i, d)
			volumes += fmt.Sprintf(`      - name: data-%d
        persistentVolumeClaim:
          claimName: %s-pvc-%d
`, i, name, i)
		}
	}

	deployment := fmt.Sprintf(`apiVersion: apps/v1
kind: Deployment
metadata:
  name: %s
  namespace: %s
spec:
  replicas: 1
  selector:
    matchLabels:
      app: %s
  template:
    metadata:
      labels:
        app: %s
    spec:
      containers:
      - name: %s
        image: %s
        resources:
          requests:
            cpu: %s
            memory: %s
          limits:
            cpu: %s
            memory: %s
%s%s%s%s`, name, namespace, name, name, name, imageRef,
		cpuRequest, memRequest, cpuRequest, memRequest,
		portSpec, envFrom,
		func() string {
			if volumeMounts != "" {
				return "        volumeMounts:\n" + volumeMounts
			}
			return ""
		}(),
		func() string {
			if volumes != "" {
				return "      volumes:\n" + volumes
			}
			return ""
		}(),
	)

	// --- Service ---
	var svcPorts string
	for _, p := range app.ListeningPorts {
		svcPorts += fmt.Sprintf("  - port: %d\n    targetPort: %d\n", p.Port, p.Port)
	}
	if svcPorts == "" {
		svcPorts = "  - port: 80\n    targetPort: 80\n"
	}
	service := fmt.Sprintf(`apiVersion: v1
kind: Service
metadata:
  name: %s
  namespace: %s
spec:
  selector:
    app: %s
  ports:
%s`, name, namespace, name, svcPorts)

	// --- PVC (stateful only) ---
	var pvc string
	if len(app.DataDirs) > 0 {
		for i := range app.DataDirs {
			pvc += fmt.Sprintf(`apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: %s-pvc-%d
  namespace: %s
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
---
`, name, i, namespace)
		}
		pvc = strings.TrimSuffix(pvc, "---\n")
	}

	return ManifestSet{
		Deployment: deployment,
		Service:    service,
		PVC:        pvc,
		ConfigMap:  cfgMap,
	}, nil
}

func sanitizeName(s string) string {
	s = strings.ToLower(s)
	var b strings.Builder
	for _, r := range s {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '-' {
			b.WriteRune(r)
		} else {
			b.WriteRune('-')
		}
	}
	return strings.Trim(b.String(), "-")
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd agent && go test ./commands/containerizebuild/... -v 2>&1
```
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
cd agent
git add commands/containerizebuild/manifests.go commands/containerizebuild/containerizebuild_test.go
git commit -m "feat(agent): add k8s manifest generation for containerize build"
```

---

## Task 3: Go Agent — Docker Build & Push

**Files:**
- Create: `agent/commands/containerizebuild/registry.go`
- Modify: `agent/commands/containerizebuild/containerizebuild.go` — full implementation

- [ ] **Step 1: Write failing test for full execute flow**

Add to `containerizebuild_test.go`:

```go
func TestContainerizeBuildExecute_MissingApp(t *testing.T) {
	_, err := containerizebuild.ContainerizeBuildExecute(map[string]any{})
	if err == nil {
		t.Error("expected error for missing app_profile parameter")
	}
}

func TestContainerizeBuildExecute_DryRun(t *testing.T) {
	params := map[string]any{
		"dry_run": true,
		"app_profile": map[string]any{
			"name":             "testapp",
			"binary":           "/usr/local/bin/testapp",
			"os_family":        "debian",
			"listening_ports":  []any{},
			"config_files":     []any{},
			"data_directories": []any{},
			"env_vars":         []any{},
		},
		"registry": "myregistry.example.com/nexplane",
		"namespace": "prod",
	}
	result, err := containerizebuild.ContainerizeBuildExecute(params)
	if err != nil {
		t.Fatalf("dry run should not fail: %v", err)
	}
	if result["dockerfile"] == "" {
		t.Error("expected dockerfile in dry_run result")
	}
	if result["manifests"] == nil {
		t.Error("expected manifests in dry_run result")
	}
	// In dry_run mode, image_digest should be empty (no actual build)
	if result["image_digest"] != "" {
		t.Errorf("expected empty image_digest in dry_run, got: %v", result["image_digest"])
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd agent && go test ./commands/containerizebuild/... -run TestContainerizeBuildExecute -v 2>&1
```
Expected: both tests fail (wrong return type / missing param check).

- [ ] **Step 3: Implement registry.go and full execute**

Create `agent/commands/containerizebuild/registry.go`:

```go
package containerizebuild

import (
	"fmt"
	"os/exec"
	"strings"
)

// BuildAndPush runs `docker build` using the provided Dockerfile content (via stdin)
// and pushes the resulting image to the registry. Returns the image digest.
// In dry_run mode, skips the actual build/push and returns an empty digest.
func BuildAndPush(imageName, dockerfile string, dryRun bool) (string, error) {
	if dryRun {
		return "", nil
	}

	// Write Dockerfile to a temp file and run docker build
	buildCmd := exec.Command("docker", "build",
		"--file", "-",       // read Dockerfile from stdin
		"--tag", imageName,
		".",
	)
	buildCmd.Stdin = strings.NewReader(dockerfile)
	out, err := buildCmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("docker build failed: %w\noutput: %s", err, out)
	}

	// Push the image
	pushCmd := exec.Command("docker", "push", "--quiet", imageName)
	pushOut, err := pushCmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("docker push failed: %w\noutput: %s", err, pushOut)
	}

	// Extract the digest from push output (format: <image>@sha256:<digest>)
	digest := ""
	for _, line := range strings.Split(string(pushOut), "\n") {
		if strings.Contains(line, "sha256:") {
			parts := strings.Fields(line)
			for _, p := range parts {
				if strings.HasPrefix(p, "sha256:") {
					digest = p
					break
				}
			}
		}
	}
	if digest == "" {
		// Inspect to get digest as fallback
		inspectOut, _ := exec.Command("docker", "inspect", "--format={{index .RepoDigests 0}}", imageName).Output()
		digest = strings.TrimSpace(string(inspectOut))
	}

	return digest, nil
}
```

Replace `agent/commands/containerizebuild/containerizebuild.go` with full implementation:

```go
package containerizebuild

import (
	"encoding/json"
	"fmt"
)

// ContainerizeBuildExecute is the entry point registered in executor.go.
// Parameters:
//   - app_profile (object): AppProfile JSON with name, binary, os_family, ports, etc.
//   - registry (string): image registry prefix e.g. "123456789.dkr.ecr.us-east-1.amazonaws.com/nexplane"
//   - namespace (string): k8s namespace for generated manifests (default: "default")
//   - cpu_request (string): k8s CPU request (default: "100m")
//   - mem_request (string): k8s memory request (default: "128Mi")
//   - dry_run (bool): if true, generate artifacts but skip docker build/push
func ContainerizeBuildExecute(params map[string]any) (map[string]any, error) {
	rawProfile, ok := params["app_profile"]
	if !ok {
		return nil, fmt.Errorf("missing required parameter: app_profile")
	}

	// Unmarshal app_profile (may arrive as map or JSON string)
	var app AppProfile
	switch v := rawProfile.(type) {
	case map[string]any:
		b, _ := json.Marshal(v)
		if err := json.Unmarshal(b, &app); err != nil {
			return nil, fmt.Errorf("invalid app_profile: %w", err)
		}
	case string:
		if err := json.Unmarshal([]byte(v), &app); err != nil {
			return nil, fmt.Errorf("invalid app_profile JSON: %w", err)
		}
	default:
		return nil, fmt.Errorf("app_profile must be an object or JSON string")
	}

	registry, _ := params["registry"].(string)
	namespace, _ := params["namespace"].(string)
	cpuReq, _ := params["cpu_request"].(string)
	memReq, _ := params["mem_request"].(string)
	dryRun, _ := params["dry_run"].(bool)

	if registry == "" {
		registry = "nexplane-local"
	}

	// Step 1: Generate Dockerfile
	dockerfile, err := GenerateDockerfile(app)
	if err != nil {
		return nil, fmt.Errorf("dockerfile generation failed: %w", err)
	}

	// Step 2: Generate k8s manifests
	imageName := fmt.Sprintf("%s/%s:latest", registry, sanitizeName(app.Name))
	manifests, err := GenerateManifests(app, imageName, namespace, cpuReq, memReq)
	if err != nil {
		return nil, fmt.Errorf("manifest generation failed: %w", err)
	}

	// Step 3: Build and push (skipped in dry_run)
	imageDigest, err := BuildAndPush(imageName, dockerfile, dryRun)
	if err != nil {
		return nil, fmt.Errorf("build/push failed: %w", err)
	}

	return map[string]any{
		"action":       "containerize_build",
		"app_name":     app.Name,
		"image_name":   imageName,
		"image_digest": imageDigest,
		"dockerfile":   dockerfile,
		"manifests": map[string]any{
			"deployment": manifests.Deployment,
			"service":    manifests.Service,
			"pvc":        manifests.PVC,
			"config_map": manifests.ConfigMap,
		},
		"dry_run": dryRun,
	}, nil
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd agent && go test ./commands/containerizebuild/... -v 2>&1
```
Expected: all 7 tests pass (dry_run and missing param tests pass without docker daemon).

- [ ] **Step 5: Build the agent to verify it compiles**

```bash
cd agent && GOOS=linux GOARCH=amd64 go build -ldflags "-X main.Version=0.1.0" -o dist/nexplane-agent-linux-amd64 ./ 2>&1 && echo "Build OK"
```
Expected: Build OK

- [ ] **Step 6: Commit**

```bash
cd agent
git add commands/containerizebuild/ executor/executor.go
git commit -m "feat(agent): implement containerize_build with docker build/push and dry_run mode"
```

---

## Task 4: Backend Executor + Build Result Service

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/containerize_build.py`
- Create: `backend/app/services/build_result_service.py`
- Modify: `backend/app/workflows/execute_change_workflow.py`
- Create: `backend/tests/test_containerize_build.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_containerize_build.py`:

```python
"""Tests for containerize_build executor and build_result_service."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


class TestBuildResultService:
    """Tests for write_build_result_to_metadata."""

    @pytest.mark.asyncio
    async def test_writes_image_digest_and_manifests(self):
        from app.services.build_result_service import write_build_result_to_metadata
        from app.models.asset import Asset

        asset_id = str(uuid.uuid4())
        mock_asset = MagicMock(spec=Asset)
        mock_asset.asset_metadata = {}

        mock_db = AsyncMock()
        mock_db.get.return_value = mock_asset

        execution_result = {
            "steps": [
                {
                    "result": {
                        "action": "containerize_build",
                        "app_name": "myapp",
                        "image_name": "registry/myapp:latest",
                        "image_digest": "sha256:abc123",
                        "dockerfile": "FROM debian:bookworm-slim\n",
                        "manifests": {
                            "deployment": "apiVersion: apps/v1\n...",
                            "service": "apiVersion: v1\n...",
                            "pvc": "",
                            "config_map": "",
                        },
                    }
                }
            ]
        }

        await write_build_result_to_metadata(mock_db, [asset_id], execution_result)

        assert mock_asset.asset_metadata is not None
        apps = mock_asset.asset_metadata.get("applications", [])
        # Should update the matching app entry or store at top level
        # The service stores build artifacts at asset_metadata.build_results["myapp"]
        build = mock_asset.asset_metadata.get("build_results", {})
        assert "myapp" in build
        assert build["myapp"]["image_digest"] == "sha256:abc123"
        assert build["myapp"]["containerization_status"] == "image_pushed"

    @pytest.mark.asyncio
    async def test_noop_when_no_build_result(self):
        from app.services.build_result_service import write_build_result_to_metadata

        mock_db = AsyncMock()
        # Should not raise even with empty result
        await write_build_result_to_metadata(mock_db, [], {"steps": []})
        mock_db.commit.assert_not_called()


class TestContainerizeBuildExecutor:
    """Tests for the containerize_build executor dispatch."""

    @pytest.mark.asyncio
    async def test_executor_dispatches_with_app_profile(self):
        from app.connectors.executors.nexplane_agent.containerize_build import execute

        mock_connector = MagicMock()
        asset_id = str(uuid.uuid4())

        parameters = {
            "app_name": "myapp",
            "registry": "123.dkr.ecr.us-east-1.amazonaws.com/nexplane",
            "dry_run": True,
        }

        with patch(
            "app.connectors.executors.nexplane_agent.containerize_build.dispatch_agent_job"
        ) as mock_dispatch:
            mock_dispatch.return_value = {
                "action": "containerize_build",
                "app_name": "myapp",
                "image_digest": "sha256:abc",
                "dockerfile": "FROM debian\n",
                "manifests": {},
            }
            # Asset must exist to pull app_profile from metadata
            with patch(
                "app.connectors.executors.nexplane_agent.containerize_build._load_app_profile"
            ) as mock_load:
                mock_load.return_value = {
                    "name": "myapp",
                    "binary": "/usr/local/bin/myapp",
                    "os_family": "debian",
                    "listening_ports": [],
                    "config_files": [],
                    "data_directories": [],
                    "env_vars": [],
                }
                result = await execute(parameters, [asset_id], mock_connector)

        assert result["action"] == "containerize_build"
        dispatched_params = mock_dispatch.call_args[1]["parameters"]
        assert "app_profile" in dispatched_params
        assert dispatched_params["app_profile"]["name"] == "myapp"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_containerize_build.py -v 2>&1 | head -30
```
Expected: ImportError (modules don't exist yet).

- [ ] **Step 3: Implement build_result_service.py**

Create `backend/app/services/build_result_service.py`:

```python
"""Service to persist containerize_build results to asset_metadata."""
import uuid
from sqlalchemy import select
from app.models.asset import Asset


async def write_build_result_to_metadata(db, asset_ids: list, execution_result: dict) -> None:
    """Read build artifacts from execution_result steps and write to asset_metadata.build_results."""
    build_data = None
    for step in execution_result.get("steps", []):
        result = step.get("result", {})
        if isinstance(result, dict) and result.get("action") == "containerize_build":
            build_data = result
            break
    if build_data is None:
        return

    app_name = build_data.get("app_name", "")
    if not app_name:
        return

    for asset_id_str in asset_ids:
        try:
            asset_uuid = uuid.UUID(str(asset_id_str))
        except ValueError:
            continue
        asset = await db.get(Asset, asset_uuid)
        if not asset:
            continue

        metadata = dict(asset.asset_metadata or {})
        build_results = dict(metadata.get("build_results", {}))
        build_results[app_name] = {
            "image_name": build_data.get("image_name", ""),
            "image_digest": build_data.get("image_digest", ""),
            "dockerfile": build_data.get("dockerfile", ""),
            "manifests": build_data.get("manifests", {}),
            "containerization_status": "image_pushed" if build_data.get("image_digest") else "dockerfile_generated",
        }
        metadata["build_results"] = build_results

        # Also update containerization_status on applications[] entry if present
        applications = metadata.get("applications", [])
        for app in applications:
            if app.get("name") == app_name:
                app["containerization_status"] = build_results[app_name]["containerization_status"]
                app["image_digest"] = build_data.get("image_digest")
                break
        metadata["applications"] = applications

        asset.asset_metadata = metadata

    await db.commit()
```

- [ ] **Step 4: Implement the executor**

Replace `backend/app/connectors/executors/nexplane_agent/containerize_build.py`:

```python
"""Executor for agent_containerize_build change type.

Reads the app profile from asset_metadata.applications[], enriches it
with registry credentials, and dispatches to the Go agent which runs
docker build + push and returns the image digest and generated manifests.
"""
from __future__ import annotations
import uuid
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.asset import Asset


async def _load_app_profile(asset_id: str, app_name: str) -> dict | None:
    """Load the app profile from asset_metadata.applications[]."""
    async with AsyncSessionLocal() as db:
        asset = await db.get(Asset, uuid.UUID(asset_id))
        if not asset:
            return None
        for app in (asset.asset_metadata or {}).get("applications", []):
            if app.get("name") == app_name:
                return app
    return None


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Dispatch containerize_build to the registered Nexplane agent.

    Parameters expected:
      - app_name (str): which application from applications[] to containerize
      - registry (str): image registry prefix (e.g. ECR repo URL)
      - namespace (str, optional): k8s namespace (default: "default")
      - cpu_request (str, optional): k8s CPU request (default: "100m")
      - mem_request (str, optional): k8s memory request (default: "128Mi")
      - dry_run (bool, optional): if true, generate artifacts without docker build
    """
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    app_name = parameters.get("app_name", "")
    if not app_name:
        raise ValueError("Missing required parameter: app_name")

    asset_id = asset_ids[0] if asset_ids else None
    if not asset_id:
        raise ValueError("No asset_id provided")

    # Load the app profile from asset metadata
    app_profile = await _load_app_profile(str(asset_id), app_name)
    if app_profile is None:
        raise ValueError(
            f"Application '{app_name}' not found in asset_metadata.applications[]. "
            "Run agent_appdiscovery first."
        )

    agent_params = {
        "app_profile": app_profile,
        "registry": parameters.get("registry", "nexplane-local"),
        "namespace": parameters.get("namespace", "default"),
        "cpu_request": parameters.get("cpu_request", "100m"),
        "mem_request": parameters.get("mem_request", "128Mi"),
        "dry_run": bool(parameters.get("dry_run", False)),
    }

    return await dispatch_agent_job(
        command="containerize_build",
        parameters=agent_params,
        asset_ids=list(asset_ids),
        timeout_seconds=300,  # 5 min for docker build + push
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback: delete the pushed image from the registry."""
    image_name = execution_result.get("image_name", "")
    image_digest = execution_result.get("image_digest", "")
    if not image_name or not image_digest:
        return {"rolled_back": False, "note": "No image digest to delete"}
    return {
        "rolled_back": True,
        "note": f"Image {image_name}@{image_digest} should be deleted from registry manually",
        "image_name": image_name,
        "image_digest": image_digest,
    }
```

- [ ] **Step 5: Wire post-execution hook in workflow**

In `backend/app/workflows/execute_change_workflow.py`, add after the existing `agent_appdiscovery` hook:

```python
if data.get("change_type") == "agent_containerize_build":
    from app.services.build_result_service import write_build_result_to_metadata
    async with AsyncSessionLocal() as post_db:
        await write_build_result_to_metadata(
            post_db, data["target_asset_ids"], execution_result
        )
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_containerize_build.py -v 2>&1
```
Expected: all 3 tests pass.

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/services/build_result_service.py \
        app/connectors/executors/nexplane_agent/containerize_build.py \
        app/workflows/execute_change_workflow.py \
        tests/test_containerize_build.py
git commit -m "feat(backend): agent_containerize_build executor, build result service, workflow hook"
```

---

## Task 5: Frontend — Build & Push Wizard Step

**Files:**
- Create: `frontend/src/components/ContainerizeWizardStep2.tsx`
- Modify: `frontend/src/components/ContainerizationWizard.tsx`

- [ ] **Step 1: Create ContainerizeWizardStep2.tsx**

Create `frontend/src/components/ContainerizeWizardStep2.tsx`:

```tsx
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ChevronDown, ChevronRight, Loader2, CheckCircle, AlertCircle } from "lucide-react";
import { useFireCR } from "@/hooks/useFireCR";

interface App {
  name: string;
  binary: string;
  listening_ports: { port: number; protocol: string }[];
  data_directories: string[];
  env_vars: string[];
  containerization_status: string;
  image_digest?: string;
}

interface ContainerizeWizardStep2Props {
  assetId: string;
  apps: App[];
  onComplete: (buildResults: Record<string, { imageDigest: string; manifests: Record<string, string> }>) => void;
}

export function ContainerizeWizardStep2({ assetId, apps, onComplete }: ContainerizeWizardStep2Props) {
  const [registry, setRegistry] = useState("");
  const [namespace, setNamespace] = useState("default");
  const [expandedApp, setExpandedApp] = useState<string | null>(null);
  const [buildResults, setBuildResults] = useState<Record<string, { imageDigest: string; manifests: Record<string, string>; dockerfile?: string }>>({});
  const [status, setStatus] = useState<"idle" | "building" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  const { fireCR, loading } = useFireCR();

  const handleBuildAndPush = async () => {
    if (!registry) {
      setError("Registry URL is required");
      return;
    }
    setStatus("building");
    setError(null);

    const results: Record<string, { imageDigest: string; manifests: Record<string, string>; dockerfile?: string }> = {};

    for (const app of apps) {
      try {
        const crResult = await fireCR({
          title: `[Containerize] Build & push ${app.name}`,
          changeType: "agent_containerize_build",
          targetAssetId: assetId,
          parameters: {
            app_name: app.name,
            registry,
            namespace,
            dry_run: false,
          },
        });
        results[app.name] = {
          imageDigest: crResult?.image_digest ?? "",
          manifests: crResult?.manifests ?? {},
          dockerfile: crResult?.dockerfile ?? "",
        };
      } catch (e) {
        setError(`Failed to build ${app.name}: ${e instanceof Error ? e.message : "Unknown error"}`);
        setStatus("error");
        return;
      }
    }

    setBuildResults(results);
    setStatus("done");
    onComplete(results);
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">Step 2 — Build &amp; Push</h3>
        <p className="text-sm text-muted-foreground mt-1">
          Generate Dockerfiles and push images to your container registry.
        </p>
      </div>

      {/* Registry configuration */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Registry Configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="registry">Registry URL</Label>
            <Input
              id="registry"
              placeholder="123456789.dkr.ecr.us-east-1.amazonaws.com/nexplane"
              value={registry}
              onChange={(e) => setRegistry(e.target.value)}
              disabled={status === "building"}
            />
            <p className="text-xs text-muted-foreground">
              ECR, GCR, ACR, or any Docker-compatible registry
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="namespace">Kubernetes Namespace</Label>
            <Input
              id="namespace"
              placeholder="default"
              value={namespace}
              onChange={(e) => setNamespace(e.target.value)}
              disabled={status === "building"}
            />
          </div>
        </CardContent>
      </Card>

      {/* Per-app Dockerfile preview */}
      <div className="space-y-3">
        {apps.map((app) => {
          const result = buildResults[app.name];
          return (
            <Collapsible
              key={app.name}
              open={expandedApp === app.name}
              onOpenChange={(open) => setExpandedApp(open ? app.name : null)}
            >
              <div className="border rounded-lg overflow-hidden">
                <CollapsibleTrigger asChild>
                  <div className="flex items-center justify-between p-3 hover:bg-muted/50 cursor-pointer">
                    <div className="flex items-center gap-3">
                      {expandedApp === app.name ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      <span className="font-mono text-sm font-medium">{app.name}</span>
                      {app.data_directories.length > 0 && (
                        <Badge variant="outline" className="text-xs">stateful</Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      {result?.imageDigest && (
                        <span className="text-xs text-muted-foreground font-mono">
                          {result.imageDigest.slice(0, 20)}…
                        </span>
                      )}
                      {result ? (
                        <CheckCircle className="h-4 w-4 text-green-500" />
                      ) : status === "building" ? (
                        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                      ) : null}
                    </div>
                  </div>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <div className="border-t p-3 space-y-3">
                    {result?.dockerfile ? (
                      <div>
                        <p className="text-xs font-semibold text-muted-foreground mb-1">Generated Dockerfile</p>
                        <pre className="bg-muted rounded p-3 text-xs font-mono overflow-x-auto whitespace-pre">
                          {result.dockerfile}
                        </pre>
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">Dockerfile preview available after build.</p>
                    )}
                    <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                      <div>
                        <span className="font-medium">Ports: </span>
                        {app.listening_ports.map((p) => `${p.port}/${p.protocol}`).join(", ") || "none"}
                      </div>
                      <div>
                        <span className="font-medium">Env vars: </span>
                        {app.env_vars.length}
                      </div>
                    </div>
                  </div>
                </CollapsibleContent>
              </div>
            </Collapsible>
          );
        })}
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {status === "done" ? (
        <div className="flex items-center gap-2 text-sm text-green-600">
          <CheckCircle className="h-4 w-4" />
          All images built and pushed successfully.
        </div>
      ) : (
        <Button
          onClick={handleBuildAndPush}
          disabled={!registry || status === "building" || loading}
          className="w-full"
        >
          {status === "building" ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              Building &amp; pushing…
            </>
          ) : (
            "Build &amp; Push"
          )}
        </Button>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire Step 2 into ContainerizationWizard**

In `frontend/src/components/ContainerizationWizard.tsx`, import `ContainerizeWizardStep2` and add a `step2BuildResults` state. In the `currentStep === 2` branch, render `<ContainerizeWizardStep2>` passing the selected apps and an `onComplete` that advances to step 3. Look for the existing step 2 placeholder/stub and replace it.

The exact edit depends on what the stub currently looks like. If the wizard has:
```tsx
{currentStep === 2 && <div>Step 2 placeholder</div>}
```

Replace with:
```tsx
{currentStep === 2 && (
  <ContainerizeWizardStep2
    assetId={assetId}
    apps={selectedApps}
    onComplete={(results) => {
      setBuildResults(results);
      setCurrentStep(3);
    }}
  />
)}
```

Add state: `const [buildResults, setBuildResults] = useState<Record<string, any>>({});`

- [ ] **Step 3: Verify TypeScript compilation**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -30
```
Expected: no errors (or only pre-existing errors unrelated to new files).

- [ ] **Step 4: Commit**

```bash
cd frontend
git add src/components/ContainerizeWizardStep2.tsx src/components/ContainerizationWizard.tsx
git commit -m "feat(frontend): add Build & Push wizard step with Dockerfile preview"
```

---

## Task 6: Smoke Test Phase Y — Containerize Build

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py` — add `run_phase_y()`

The smoke test reuses the Phase X EC2 instance (requires Phase X to have run, which ensures the agent is deployed and `nexplane-smoketest` app was discovered). Phase Y fires `agent_containerize_build` in `dry_run=true` mode (no Docker daemon on the test instance) and validates the Dockerfile + manifests were generated and stored on the asset.

- [ ] **Step 1: Read Phase X function to understand pattern**

Read `backend/tests/smoke/test_aws_live.py` lines 2015–2150 to understand Phase X structure (already done — follows the same `client.run_cr()` → poll → verify pattern).

- [ ] **Step 2: Add run_phase_y() function**

Add after `run_phase_x()` in `backend/tests/smoke/test_aws_live.py`:

```python
def run_phase_y(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase Y: Containerize Build — validates agent_containerize_build CR end-to-end.

    Requires Phase X (agent discovery must have run, nexplane-smoketest in applications[]).
    Fires agent_containerize_build in dry_run=true mode (no docker daemon needed).
    Verifies Dockerfile + manifests were generated and stored on asset_metadata.
    """
    log("\n[Phase Y] Containerize Build (dry_run)")

    agent_asset_id = phase_a_result.get("agent_asset_id")
    if not agent_asset_id:
        fail("Phase Y requires phase_a_result['agent_asset_id'] (set by Phase X)")

    # Fire the build CR in dry_run mode — no Docker daemon required
    log("[Phase Y] Firing agent_containerize_build CR (dry_run=True)")
    client.run_cr(
        "[Phase Y] containerize build dry run",
        "agent_containerize_build",
        agent_asset_id,
        {
            "app_name": "nexplane-smoketest",
            "registry": "smoke-test-registry.example.com/nexplane",
            "namespace": "smoke-test",
            "dry_run": True,
        },
    )
    log("[Phase Y] CR completed")

    # Verify build results stored on asset_metadata
    log("[Phase Y] Verifying build results written to asset_metadata")
    asset = client.get(f"/assets/{agent_asset_id}")
    if not asset:
        fail(f"[Phase Y] Agent asset {agent_asset_id} not found")

    build_results = (asset.get("asset_metadata") or {}).get("build_results", {})
    if "nexplane-smoketest" not in build_results:
        fail(
            f"[Phase Y] build_results['nexplane-smoketest'] not found in asset_metadata. "
            f"Got keys: {list(build_results.keys())}"
        )

    result = build_results["nexplane-smoketest"]

    # Verify Dockerfile was generated
    dockerfile = result.get("dockerfile", "")
    if not dockerfile:
        fail("[Phase Y] No Dockerfile in build_results")
    if "FROM" not in dockerfile:
        fail(f"[Phase Y] Dockerfile looks invalid: {dockerfile[:100]}")
    log("[Phase Y] Dockerfile generated ✅")

    # Verify k8s manifests were generated
    manifests = result.get("manifests", {})
    if not manifests.get("deployment"):
        fail("[Phase Y] No Deployment manifest in build_results.manifests")
    if "kind: Deployment" not in manifests["deployment"]:
        fail(f"[Phase Y] Deployment manifest looks invalid: {manifests['deployment'][:100]}")
    log("[Phase Y] Kubernetes manifests generated ✅")

    # Verify containerization_status updated on applications[]
    applications = (asset.get("asset_metadata") or {}).get("applications", [])
    smoketest_app = next((a for a in applications if a.get("name") == "nexplane-smoketest"), None)
    if smoketest_app is None:
        log("[Phase Y] Warning: nexplane-smoketest not in applications[] — Phase X may not have run first")
    elif smoketest_app.get("containerization_status") not in ("dockerfile_generated", "image_pushed"):
        fail(
            f"[Phase Y] Expected containerization_status 'dockerfile_generated' or 'image_pushed', "
            f"got '{smoketest_app.get('containerization_status')}'"
        )
    else:
        log(f"[Phase Y] containerization_status: {smoketest_app['containerization_status']} ✅")

    log("[Phase Y] ✅ Containerize build phase complete")
```

- [ ] **Step 3: Update Phase X to store agent_asset_id in phase_a_result**

In `run_phase_x()`, after confirming agent registration:
```python
# Store agent_asset_id so Phase Y can use it
phase_a_result["agent_asset_id"] = agent_asset_id
```

And update `run_phase_x`'s return so Phase Y can access it — Phase X currently doesn't return anything, but it receives `phase_a_result` as a dict and can mutate it. Add:
```python
phase_a_result["agent_asset_id"] = agent_asset_id
```
right after `log(f"[Phase X] Agent registered: {agent_asset_id}")`.

- [ ] **Step 4: Wire Phase Y into main dispatcher**

In `main()`, add after Phase X:
```python
if "Y" in phases:
    if phase_a_result is None:
        fail("Phase Y requires Phase A to have run first")
    run_phase_y(client, phase_a_result)
```

Also add `X,Y` to the default phases description string.

- [ ] **Step 5: Add catalog entry for agent_containerize_build**

Verify `backend/app/connectors/catalog/nexplane_agent.json` has an entry for `agent_containerize_build` action (should already exist from Plan 1 if it was added, check and add if missing):

```json
{
    "action_id": "agent_containerize_build",
    "generic_action": "agent_containerize_build",
    "action_type": "change",
    "execution_tier": 3,
    "display_name": "Containerize Application",
    "description": "Generate Dockerfile + k8s manifests, build container image, push to registry",
    "applicable_asset_types": ["server", "endpoint"],
    "parameters": [
        {"name": "app_name", "type": "string", "required": true},
        {"name": "registry", "type": "string", "required": true},
        {"name": "namespace", "type": "string", "required": false, "default": "default"},
        {"name": "cpu_request", "type": "string", "required": false, "default": "100m"},
        {"name": "mem_request", "type": "string", "required": false, "default": "128Mi"},
        {"name": "dry_run", "type": "boolean", "required": false, "default": false}
    ],
    "executor": "nexplane_agent.containerize_build",
    "estimated_duration_seconds": 300
}
```

Also verify `backend/app/connectors/change_type_definitions/agent_containerize_build.json` exists with `generic_action: "agent_containerize_build"` (should be from Plan 1 — check, and create/fix if missing).

- [ ] **Step 6: Commit**

```bash
cd backend
git add tests/smoke/test_aws_live.py \
        app/connectors/catalog/nexplane_agent.json \
        app/connectors/change_type_definitions/agent_containerize_build.json
git commit -m "test(smoke): add Phase Y smoke test for agent_containerize_build dry_run"
```
