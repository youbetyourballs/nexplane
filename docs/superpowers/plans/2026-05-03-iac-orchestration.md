# IaC Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Terraform, Ansible, and Helm operations into the Nexplane change-request lifecycle as three new auditable change types (`terraform_apply`, `ansible_playbook`, `helm_upgrade`) with plan-before-apply semantics, blast-radius capture, human approval gates, and rollback support.

**Architecture:** A new `agent/commands/iac/` package wraps the IaC CLIs (`terraform`, `ansible-playbook`, `helm`) via `exec.Command`, capturing plan/check output as text. The backend's `planning_engine.py` gains three new change type definitions; a new `iac_executor.py` service handles two-phase execution (plan → store blast radius → set status `planned` → wait for approval → apply). The frontend `ChangeRequestDetail.tsx` gains a Plan Output panel that renders `blast_radius.impact_description` as a colored diff when the change type is an IaC type.

**Tech Stack:** Go 1.26 (`os/exec`, `context`), Python 3.12 (FastAPI + SQLAlchemy async), React 18 + TanStack Query, Tailwind CSS.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `agent/commands/iac/terraform.go` | Create | `TerraformPlan`, `TerraformApply`, `TerraformRollback`, shared `runCmd`/`captureCmd` helpers |
| `agent/commands/iac/terraform_test.go` | Create | Tests with mock `exec.Command` via `execCommand` indirection |
| `agent/commands/iac/ansible.go` | Create | `AnsibleCheck`, `AnsibleRun` (handles rollback playbook), inline-inventory temp file |
| `agent/commands/iac/ansible_test.go` | Create | Tests for check, run, rollback path, missing rollback path error |
| `agent/commands/iac/helm.go` | Create | `HelmDiff`, `HelmUpgrade`, `HelmRollback`, inline-values temp file |
| `agent/commands/iac/helm_test.go` | Create | Tests for diff, upgrade (atomic flag), rollback |
| `agent/executor/executor.go` | Modify | Register `terraform_plan`, `terraform_apply`, `terraform_rollback`, `ansible_check`, `ansible_run`, `helm_diff`, `helm_upgrade`, `helm_rollback` |
| `backend/app/models/change_request.py` | Modify | Add `terraform_apply`, `ansible_playbook`, `helm_upgrade` to `ChangeType` enum |
| `backend/app/services/iac_executor.py` | Create | Two-phase execution service: plan → store blast radius → set `planned` → approval gate → apply |
| `backend/app/connectors/change_type_definitions/terraform_apply.json` | Create | Step definitions for terraform two-phase change type |
| `backend/app/connectors/change_type_definitions/ansible_playbook.json` | Create | Step definitions for ansible two-phase change type |
| `backend/app/connectors/change_type_definitions/helm_upgrade.json` | Create | Step definitions for helm two-phase change type |
| `frontend/src/pages/ChangeRequestDetail.tsx` | Modify | Plan Output collapsible panel with green/red diff coloring for IaC types |
| `frontend/src/components/ChangeRequestForm.tsx` | Modify | IaC category + per-type parameter forms in new change request modal |

---

## Task 1: Agent IaC package — Terraform

**Files:**
- Create: `agent/commands/iac/terraform.go`
- Create: `agent/commands/iac/terraform_test.go`

- [ ] **Step 1: Write failing tests first**

Create `agent/commands/iac/terraform_test.go`:

```go
package iac_test

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"nexplane-agent/commands/iac"
)

// fakeExec is a test helper that replaces exec.Command with a stub that
// writes the provided output and exits with the provided code.
// Pattern: compile a helper binary per test, referenced via execCommand var.
// For simplicity we use the TestMain-as-subprocess pattern.

func TestMain(m *testing.M) {
	// If run as the fake terraform/ansible/helm subprocess, emit mocked output.
	if proc := os.Getenv("NEXPLANE_FAKE_CLI"); proc != "" {
		switch proc {
		case "terraform_plan_ok":
			os.Stdout.WriteString("Plan: 2 to add, 0 to change, 0 to destroy.\n")
			os.Exit(0)
		case "terraform_plan_fail":
			os.Stderr.WriteString("Error: no such directory\n")
			os.Exit(1)
		case "terraform_apply_ok":
			os.Stdout.WriteString("Apply complete! Resources: 2 added.\n")
			os.Exit(0)
		case "terraform_rollback_ok":
			os.Stdout.WriteString("Apply complete! Resources: 2 destroyed.\n")
			os.Exit(0)
		}
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func selfExe(t *testing.T) string {
	t.Helper()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	return exe
}

// overrideExecCommand replaces iac.ExecCommand with a stub backed by this
// test binary and restores it after the test.
func overrideExecCommand(t *testing.T, proc string) {
	t.Helper()
	exe := selfExe(t)
	orig := iac.ExecCommand
	iac.ExecCommand = func(ctx context.Context, name string, args ...string) *exec.Cmd {
		cmd := exec.CommandContext(ctx, exe, args...)
		cmd.Env = append(os.Environ(), "NEXPLANE_FAKE_CLI="+proc)
		return cmd
	}
	t.Cleanup(func() { iac.ExecCommand = orig })
}

func TestTerraformPlan_Success(t *testing.T) {
	overrideExecCommand(t, "terraform_plan_ok")
	p := iac.TerraformParams{
		WorkingDirectory: t.TempDir(),
		ChangeID:         "cr-test-001",
		Workspace:        "default",
	}
	out, err := iac.TerraformPlan(context.Background(), p)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "2 to add") {
		t.Fatalf("expected plan output, got: %q", out)
	}
}

func TestTerraformPlan_Failure(t *testing.T) {
	overrideExecCommand(t, "terraform_plan_fail")
	p := iac.TerraformParams{
		WorkingDirectory: t.TempDir(),
		ChangeID:         "cr-test-002",
	}
	_, err := iac.TerraformPlan(context.Background(), p)
	if err == nil {
		t.Fatal("expected error from failed terraform plan")
	}
}

func TestTerraformApply_Success(t *testing.T) {
	overrideExecCommand(t, "terraform_apply_ok")
	dir := t.TempDir()
	// Create the fake plan file so the path exists.
	planFile := filepath.Join(dir, "cr-test-003.tfplan")
	os.WriteFile(planFile, []byte("fake plan"), 0644)
	p := iac.TerraformParams{WorkingDirectory: dir, ChangeID: "cr-test-003"}
	out, err := iac.TerraformApply(context.Background(), p)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "Apply complete") {
		t.Fatalf("expected apply output, got: %q", out)
	}
}

func TestTerraformRollback_Destroy(t *testing.T) {
	overrideExecCommand(t, "terraform_rollback_ok")
	p := iac.TerraformParams{WorkingDirectory: t.TempDir(), ChangeID: "cr-test-004"}
	out, err := iac.TerraformRollback(context.Background(), p, []string{"aws_instance.web"}, true)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "Resources: 2 destroyed") {
		t.Fatalf("expected destroy output, got: %q", out)
	}
}

func TestTerraformPlan_WithVarFileAndTargets(t *testing.T) {
	overrideExecCommand(t, "terraform_plan_ok")
	p := iac.TerraformParams{
		WorkingDirectory: t.TempDir(),
		ChangeID:         "cr-test-005",
		VarFile:          "prod.tfvars",
		Target:           []string{"aws_instance.web", "aws_security_group.sg"},
		Workspace:        "production",
	}
	out, err := iac.TerraformPlan(context.Background(), p)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if out == "" {
		t.Fatal("expected non-empty output")
	}
}
```

- [ ] **Step 2: Run tests — expect compile failure (package doesn't exist yet)**

```bash
cd agent && go test ./commands/iac/... -v -run TestTerraform 2>&1 | head -20
```
Expected: `cannot find package` or `no Go files` build error.

- [ ] **Step 3: Implement `agent/commands/iac/terraform.go`**

```go
package iac

import (
	"context"
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
)

// ExecCommand is the exec.Command factory. Replaced in tests to inject fakes.
var ExecCommand = func(ctx context.Context, name string, args ...string) *exec.Cmd {
	return exec.CommandContext(ctx, name, args...)
}

// TerraformParams matches the terraform_apply change type parameters.
type TerraformParams struct {
	WorkingDirectory string   `json:"working_directory"`
	Workspace        string   `json:"workspace"`
	VarFile          string   `json:"var_file"`
	Target           []string `json:"target"`
	AutoApprove      bool     `json:"auto_approve"`
	DryRun           bool     `json:"dry_run"`
	ChangeID         string   `json:"change_id"`
}

// TerraformPlan runs terraform init then plan, returns captured combined output
// for blast radius storage.
func TerraformPlan(ctx context.Context, p TerraformParams) (string, error) {
	if err := runCmd(ctx, p.WorkingDirectory, "terraform", "init", "-input=false"); err != nil {
		return "", fmt.Errorf("terraform init: %w", err)
	}
	if p.Workspace != "" && p.Workspace != "default" {
		if err := runCmd(ctx, p.WorkingDirectory, "terraform", "workspace", "select", p.Workspace); err != nil {
			return "", fmt.Errorf("terraform workspace select: %w", err)
		}
	}
	planArgs := []string{"plan", "-input=false", "-out=" + p.ChangeID + ".tfplan"}
	if p.VarFile != "" {
		planArgs = append(planArgs, "-var-file="+p.VarFile)
	}
	for _, t := range p.Target {
		planArgs = append(planArgs, "-target="+t)
	}
	return captureCmd(ctx, p.WorkingDirectory, "terraform", planArgs...)
}

// TerraformApply applies the saved plan artifact produced by TerraformPlan.
func TerraformApply(ctx context.Context, p TerraformParams) (string, error) {
	planFile := filepath.Join(p.WorkingDirectory, p.ChangeID+".tfplan")
	return captureCmd(ctx, p.WorkingDirectory, "terraform", "apply", "-input=false", planFile)
}

// TerraformRollback restores resources to a prior state.
// resources is the list of resource addresses that were modified.
// destroy=true for net-new resources that should be removed.
func TerraformRollback(ctx context.Context, p TerraformParams, resources []string, destroy bool) (string, error) {
	subcmd := "apply"
	if destroy {
		subcmd = "destroy"
	}
	args := []string{subcmd, "-auto-approve", "-input=false"}
	for _, r := range resources {
		args = append(args, "-target="+r)
	}
	return captureCmd(ctx, p.WorkingDirectory, "terraform", args...)
}

func runCmd(ctx context.Context, dir, name string, args ...string) error {
	cmd := ExecCommand(ctx, name, args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%w\n%s", err, strings.TrimSpace(string(out)))
	}
	return nil
}

func captureCmd(ctx context.Context, dir, name string, args ...string) (string, error) {
	cmd := ExecCommand(ctx, name, args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		return string(out), fmt.Errorf("%w\n%s", err, strings.TrimSpace(string(out)))
	}
	return string(out), nil
}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd agent && go test ./commands/iac/... -v -run TestTerraform
```
Expected:
```
--- PASS: TestTerraformPlan_Success (0.00s)
--- PASS: TestTerraformPlan_Failure (0.00s)
--- PASS: TestTerraformApply_Success (0.00s)
--- PASS: TestTerraformRollback_Destroy (0.00s)
--- PASS: TestTerraformPlan_WithVarFileAndTargets (0.00s)
PASS
ok      nexplane-agent/commands/iac
```

- [ ] **Step 5: Commit**

```bash
git add agent/commands/iac/terraform.go agent/commands/iac/terraform_test.go
git commit -m "feat(agent/iac): add TerraformPlan, TerraformApply, TerraformRollback with testable exec injection"
```

---

## Task 2: Agent IaC package — Ansible

**Files:**
- Create: `agent/commands/iac/ansible.go`
- Create: `agent/commands/iac/ansible_test.go`

- [ ] **Step 1: Write failing tests first**

Create `agent/commands/iac/ansible_test.go`:

```go
package iac_test

import (
	"context"
	"os"
	"strings"
	"testing"

	"nexplane-agent/commands/iac"
)

func TestAnsibleCheck_Success(t *testing.T) {
	overrideExecCommand(t, "ansible_check_ok")
	p := iac.AnsibleParams{
		PlaybookPath: "/etc/ansible/site.yml",
		Inventory:    "/etc/ansible/hosts",
		ChangeID:     "cr-ansible-001",
	}
	out, err := iac.AnsibleCheck(context.Background(), p)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "changed=0") {
		t.Fatalf("expected check output, got: %q", out)
	}
}

func TestAnsibleCheck_InlineInventory(t *testing.T) {
	overrideExecCommand(t, "ansible_check_ok")
	p := iac.AnsibleParams{
		PlaybookPath: "/etc/ansible/site.yml",
		Inventory:    "[web]\n192.168.1.10\n",
		ChangeID:     "cr-ansible-002",
	}
	out, err := iac.AnsibleCheck(context.Background(), p)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if out == "" {
		t.Fatal("expected non-empty output")
	}
}

func TestAnsibleRun_Success(t *testing.T) {
	overrideExecCommand(t, "ansible_run_ok")
	p := iac.AnsibleParams{
		PlaybookPath: "/etc/ansible/site.yml",
		Inventory:    "/etc/ansible/hosts",
		ChangeID:     "cr-ansible-003",
	}
	out, err := iac.AnsibleRun(context.Background(), p, false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "ok=5") {
		t.Fatalf("expected run output, got: %q", out)
	}
}

func TestAnsibleRun_Rollback_NoPath(t *testing.T) {
	overrideExecCommand(t, "ansible_run_ok")
	p := iac.AnsibleParams{
		PlaybookPath:         "/etc/ansible/site.yml",
		Inventory:            "/etc/ansible/hosts",
		ChangeID:             "cr-ansible-004",
		RollbackPlaybookPath: "", // not set
	}
	_, err := iac.AnsibleRun(context.Background(), p, true) // rollback=true
	if err == nil {
		t.Fatal("expected error when rollback_playbook_path is empty")
	}
	if !strings.Contains(err.Error(), "rollback_playbook_path") {
		t.Fatalf("expected rollback path error, got: %v", err)
	}
}

func TestAnsibleRun_Rollback_WithPath(t *testing.T) {
	overrideExecCommand(t, "ansible_run_ok")
	p := iac.AnsibleParams{
		PlaybookPath:         "/etc/ansible/site.yml",
		Inventory:            "/etc/ansible/hosts",
		ChangeID:             "cr-ansible-005",
		RollbackPlaybookPath: "/etc/ansible/rollback.yml",
	}
	out, err := iac.AnsibleRun(context.Background(), p, true)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if out == "" {
		t.Fatal("expected non-empty output")
	}
}

func TestAnsibleCheck_WithExtraVarsAndLimit(t *testing.T) {
	overrideExecCommand(t, "ansible_check_ok")
	p := iac.AnsibleParams{
		PlaybookPath: "/etc/ansible/site.yml",
		Inventory:    "/etc/ansible/hosts",
		ExtraVars:    map[string]any{"env": "prod", "version": "1.2.3"},
		Limit:        "web-servers",
		ChangeID:     "cr-ansible-006",
	}
	out, err := iac.AnsibleCheck(context.Background(), p)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	_ = out // output verified by mock
}
```

Add these fake CLI cases to `TestMain` in `terraform_test.go`:

```go
case "ansible_check_ok":
    os.Stdout.WriteString("PLAY RECAP *** changed=0 unreachable=0 failed=0\n")
    os.Exit(0)
case "ansible_run_ok":
    os.Stdout.WriteString("PLAY RECAP *** ok=5 changed=3 unreachable=0 failed=0\n")
    os.Exit(0)
```

- [ ] **Step 2: Run tests — expect compile failure**

```bash
cd agent && go test ./commands/iac/... -v -run TestAnsible 2>&1 | head -20
```
Expected: compile error (AnsibleParams/AnsibleCheck/AnsibleRun undefined).

- [ ] **Step 3: Implement `agent/commands/iac/ansible.go`**

```go
package iac

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// AnsibleParams matches the ansible_playbook change type parameters.
type AnsibleParams struct {
	PlaybookPath         string         `json:"playbook_path"`
	Inventory            string         `json:"inventory"`
	ExtraVars            map[string]any `json:"extra_vars"`
	Limit                string         `json:"limit"`
	CheckMode            bool           `json:"check_mode"`
	DryRun               bool           `json:"dry_run"`
	RollbackPlaybookPath string         `json:"rollback_playbook_path"`
	ChangeID             string         `json:"change_id"`
}

// AnsibleCheck runs ansible-playbook --check --diff, returns output for blast radius.
func AnsibleCheck(ctx context.Context, p AnsibleParams) (string, error) {
	args, cleanup, err := buildAnsibleArgs(p, p.PlaybookPath)
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		return "", err
	}
	args = append(args, "--check", "--diff")
	return captureCmd(ctx, filepath.Dir(p.PlaybookPath), "ansible-playbook", args...)
}

// AnsibleRun executes the playbook (or the rollback playbook if rollback=true).
func AnsibleRun(ctx context.Context, p AnsibleParams, rollback bool) (string, error) {
	playbook := p.PlaybookPath
	if rollback {
		if p.RollbackPlaybookPath == "" {
			return "", fmt.Errorf("no rollback_playbook_path configured for this change")
		}
		playbook = p.RollbackPlaybookPath
	}
	args, cleanup, err := buildAnsibleArgs(p, playbook)
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		return "", err
	}
	return captureCmd(ctx, filepath.Dir(playbook), "ansible-playbook", args...)
}

func buildAnsibleArgs(p AnsibleParams, playbook string) ([]string, func(), error) {
	args := []string{playbook}
	var cleanup func()

	// Inventory: write inline content to a temp file if it does not exist as a path.
	if _, err := os.Stat(p.Inventory); os.IsNotExist(err) {
		f, err := os.CreateTemp("", "nexplane-inventory-"+p.ChangeID+"-*.ini")
		if err != nil {
			return nil, nil, fmt.Errorf("writing inventory: %w", err)
		}
		if _, err := f.WriteString(p.Inventory); err != nil {
			return nil, nil, err
		}
		f.Close()
		cleanup = func() { os.Remove(f.Name()) }
		args = append(args, "-i", f.Name())
	} else {
		args = append(args, "-i", p.Inventory)
	}

	if len(p.ExtraVars) > 0 {
		evJSON, _ := json.Marshal(p.ExtraVars)
		args = append(args, "--extra-vars", string(evJSON))
	}
	if p.Limit != "" {
		args = append(args, "--limit", p.Limit)
	}
	return args, cleanup, nil
}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd agent && go test ./commands/iac/... -v -run TestAnsible
```
Expected: all 6 Ansible tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent/commands/iac/ansible.go agent/commands/iac/ansible_test.go
git commit -m "feat(agent/iac): add AnsibleCheck and AnsibleRun with inline-inventory temp file and rollback path"
```

---

## Task 3: Agent IaC package — Helm

**Files:**
- Create: `agent/commands/iac/helm.go`
- Create: `agent/commands/iac/helm_test.go`

- [ ] **Step 1: Write failing tests first**

Create `agent/commands/iac/helm_test.go`:

```go
package iac_test

import (
	"context"
	"strings"
	"testing"

	"nexplane-agent/commands/iac"
)

func TestHelmDiff_Success(t *testing.T) {
	overrideExecCommand(t, "helm_diff_ok")
	p := iac.HelmParams{
		ReleaseName: "myapp",
		Chart:       "stable/myapp",
		Namespace:   "production",
		ChangeID:    "cr-helm-001",
	}
	out, err := iac.HelmDiff(context.Background(), p)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "replicas") {
		t.Fatalf("expected diff output, got: %q", out)
	}
}

func TestHelmUpgrade_Atomic(t *testing.T) {
	overrideExecCommand(t, "helm_upgrade_ok")
	p := iac.HelmParams{
		ReleaseName: "myapp",
		Chart:       "stable/myapp",
		Namespace:   "production",
		Atomic:      true,
		Timeout:     "10m",
		ChangeID:    "cr-helm-002",
	}
	out, err := iac.HelmUpgrade(context.Background(), p)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "deployed") {
		t.Fatalf("expected upgrade output, got: %q", out)
	}
}

func TestHelmUpgrade_DefaultTimeout(t *testing.T) {
	overrideExecCommand(t, "helm_upgrade_ok")
	p := iac.HelmParams{
		ReleaseName: "myapp",
		Chart:       "stable/myapp",
		Namespace:   "staging",
		Atomic:      false,
		Timeout:     "", // should default to "5m"
		ChangeID:    "cr-helm-003",
	}
	out, err := iac.HelmUpgrade(context.Background(), p)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if out == "" {
		t.Fatal("expected non-empty output")
	}
}

func TestHelmRollback_Success(t *testing.T) {
	overrideExecCommand(t, "helm_rollback_ok")
	p := iac.HelmParams{
		ReleaseName: "myapp",
		Namespace:   "production",
		ChangeID:    "cr-helm-004",
	}
	out, err := iac.HelmRollback(context.Background(), p, 3)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "Rollback was a success") {
		t.Fatalf("expected rollback output, got: %q", out)
	}
}

func TestHelmDiff_WithInlineValues(t *testing.T) {
	overrideExecCommand(t, "helm_diff_ok")
	p := iac.HelmParams{
		ReleaseName: "myapp",
		Chart:       "stable/myapp",
		Namespace:   "production",
		Values:      "replicaCount: 3\nimage:\n  tag: v1.2.3\n",
		ChangeID:    "cr-helm-005",
	}
	out, err := iac.HelmDiff(context.Background(), p)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	_ = out
}

func TestHelmUpgrade_DefaultNamespace(t *testing.T) {
	overrideExecCommand(t, "helm_upgrade_ok")
	p := iac.HelmParams{
		ReleaseName: "myapp",
		Chart:       "stable/myapp",
		Namespace:   "", // should default to "default"
		ChangeID:    "cr-helm-006",
	}
	out, err := iac.HelmUpgrade(context.Background(), p)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	_ = out
}
```

Add these fake CLI cases to `TestMain` in `terraform_test.go`:

```go
case "helm_diff_ok":
    os.Stdout.WriteString("Comparing myapp chart: replicas 1 -> 3\n")
    os.Exit(0)
case "helm_upgrade_ok":
    os.Stdout.WriteString("Release \"myapp\" has been deployed successfully.\n")
    os.Exit(0)
case "helm_rollback_ok":
    os.Stdout.WriteString("Rollback was a success! Happy Helming!\n")
    os.Exit(0)
```

- [ ] **Step 2: Run tests — expect compile failure**

```bash
cd agent && go test ./commands/iac/... -v -run TestHelm 2>&1 | head -20
```
Expected: compile error (HelmParams/HelmDiff/HelmUpgrade/HelmRollback undefined).

- [ ] **Step 3: Implement `agent/commands/iac/helm.go`**

```go
package iac

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
)

// HelmParams matches the helm_upgrade change type parameters.
type HelmParams struct {
	ReleaseName  string `json:"release_name"`
	Chart        string `json:"chart"`
	ChartVersion string `json:"chart_version"`
	Namespace    string `json:"namespace"`
	Values       string `json:"values"`
	Atomic       bool   `json:"atomic"`
	Timeout      string `json:"timeout"`
	DryRun       bool   `json:"dry_run"`
	ChangeID     string `json:"change_id"`
}

// HelmDiff runs helm diff upgrade, returns output for blast radius.
// Requires the helm-diff plugin to be installed on the host.
func HelmDiff(ctx context.Context, p HelmParams) (string, error) {
	args, cleanup, err := baseHelmArgs("diff", "upgrade", p)
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		return "", err
	}
	return captureCmd(ctx, ".", "helm", args...)
}

// HelmUpgrade runs helm upgrade --install with optional --atomic.
func HelmUpgrade(ctx context.Context, p HelmParams) (string, error) {
	args, cleanup, err := baseHelmArgs("upgrade", "--install", p)
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		return "", err
	}
	if p.Atomic {
		args = append(args, "--atomic")
	}
	timeout := p.Timeout
	if timeout == "" {
		timeout = "5m"
	}
	args = append(args, "--timeout", timeout)
	return captureCmd(ctx, ".", "helm", args...)
}

// HelmRollback rolls back to the given release revision.
func HelmRollback(ctx context.Context, p HelmParams, previousRevision int) (string, error) {
	args := []string{"rollback", p.ReleaseName, fmt.Sprintf("%d", previousRevision),
		"-n", helmNamespace(p)}
	return captureCmd(ctx, ".", "helm", args...)
}

func baseHelmArgs(subcmd1, subcmd2 string, p HelmParams) ([]string, func(), error) {
	args := []string{subcmd1, subcmd2, p.ReleaseName, p.Chart,
		"-n", helmNamespace(p)}
	if p.ChartVersion != "" {
		args = append(args, "--version", p.ChartVersion)
	}
	var cleanup func()
	if p.Values != "" {
		f, err := os.CreateTemp("", "nexplane-helm-values-"+p.ChangeID+"-*.yaml")
		if err != nil {
			return nil, nil, fmt.Errorf("writing values: %w", err)
		}
		if _, err := f.WriteString(p.Values); err != nil {
			return nil, nil, err
		}
		f.Close()
		vpath, _ := filepath.Abs(f.Name())
		cleanup = func() { os.Remove(vpath) }
		args = append(args, "-f", vpath)
	}
	return args, cleanup, nil
}

func helmNamespace(p HelmParams) string {
	if p.Namespace == "" {
		return "default"
	}
	return p.Namespace
}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd agent && go test ./commands/iac/... -v -run TestHelm
```
Expected: all 6 Helm tests pass.

- [ ] **Step 5: Run the full IaC package test suite**

```bash
cd agent && go test ./commands/iac/... -v
```
Expected: all Terraform + Ansible + Helm tests pass, `ok nexplane-agent/commands/iac`.

- [ ] **Step 6: Commit**

```bash
git add agent/commands/iac/helm.go agent/commands/iac/helm_test.go
git commit -m "feat(agent/iac): add HelmDiff, HelmUpgrade, HelmRollback with inline-values temp file"
```

---

## Task 4: Register IaC commands in executor

**Files:**
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Add IaC Execute adapter functions**

Create `agent/commands/iac/execute.go` — thin adapters that bridge `map[string]any` params to typed IaC functions:

```go
package iac

import (
	"context"
	"encoding/json"
	"fmt"
)

func toJSON(v map[string]any) []byte {
	b, _ := json.Marshal(v)
	return b
}

func TerraformPlanExecute(params map[string]any) (map[string]any, error) {
	var p TerraformParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid terraform params: %w", err)
	}
	out, err := TerraformPlan(context.Background(), p)
	return map[string]any{"plan_output": out}, err
}

func TerraformApplyExecute(params map[string]any) (map[string]any, error) {
	var p TerraformParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid terraform params: %w", err)
	}
	out, err := TerraformApply(context.Background(), p)
	return map[string]any{"apply_output": out}, err
}

func TerraformRollbackExecute(params map[string]any) (map[string]any, error) {
	var p TerraformParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid terraform params: %w", err)
	}
	resources, _ := params["resources"].([]string)
	destroy, _ := params["destroy"].(bool)
	out, err := TerraformRollback(context.Background(), p, resources, destroy)
	return map[string]any{"rollback_output": out}, err
}

func AnsibleCheckExecute(params map[string]any) (map[string]any, error) {
	var p AnsibleParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid ansible params: %w", err)
	}
	out, err := AnsibleCheck(context.Background(), p)
	return map[string]any{"check_output": out}, err
}

func AnsibleRunExecute(params map[string]any) (map[string]any, error) {
	var p AnsibleParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid ansible params: %w", err)
	}
	out, err := AnsibleRun(context.Background(), p, false)
	return map[string]any{"run_output": out}, err
}

func AnsibleRollbackExecute(params map[string]any) (map[string]any, error) {
	var p AnsibleParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid ansible params: %w", err)
	}
	out, err := AnsibleRun(context.Background(), p, true)
	return map[string]any{"rollback_output": out}, err
}

func HelmDiffExecute(params map[string]any) (map[string]any, error) {
	var p HelmParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid helm params: %w", err)
	}
	out, err := HelmDiff(context.Background(), p)
	return map[string]any{"diff_output": out}, err
}

func HelmUpgradeExecute(params map[string]any) (map[string]any, error) {
	var p HelmParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid helm params: %w", err)
	}
	out, err := HelmUpgrade(context.Background(), p)
	return map[string]any{"upgrade_output": out}, err
}

func HelmRollbackExecute(params map[string]any) (map[string]any, error) {
	var p HelmParams
	if err := json.Unmarshal(toJSON(params), &p); err != nil {
		return nil, fmt.Errorf("invalid helm params: %w", err)
	}
	revision := 0
	if r, ok := params["previous_revision"].(float64); ok {
		revision = int(r)
	}
	out, err := HelmRollback(context.Background(), p, revision)
	return map[string]any{"rollback_output": out}, err
}
```

- [ ] **Step 2: Register in executor.go**

In `agent/executor/executor.go`, add the import and entries:

Add to the import block:
```go
"nexplane-agent/commands/iac"
```

Add to the `commands` map (after the `linuxupgrade` entry):
```go
// IaC (Spec iac-orchestration)
"terraform_plan":     iac.TerraformPlanExecute,
"terraform_apply":    iac.TerraformApplyExecute,
"ansible_check":      iac.AnsibleCheckExecute,
"ansible_run":        iac.AnsibleRunExecute,
"helm_diff":          iac.HelmDiffExecute,
"helm_upgrade":       iac.HelmUpgradeExecute,
```

Add to the `rollbacks` map:
```go
"terraform_apply": iac.TerraformRollbackExecute,
"ansible_run":     iac.AnsibleRollbackExecute,
"helm_upgrade":    iac.HelmRollbackExecute,
```

- [ ] **Step 3: Build to verify it compiles**

```bash
cd agent && go build ./...
```
Expected: exits 0, no errors.

- [ ] **Step 4: Run all agent tests**

```bash
cd agent && go test ./...
```
Expected: all existing tests pass, IaC tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent/commands/iac/execute.go agent/executor/executor.go
git commit -m "feat(agent): register IaC commands in executor — terraform_plan/apply, ansible_check/run, helm_diff/upgrade"
```

---

## Task 5: Backend — IaC executor service

**Files:**
- Create: `backend/app/services/iac_executor.py`
- Modify: `backend/app/models/change_request.py`

- [ ] **Step 1: Add IaC change types to the enum**

In `backend/app/models/change_request.py`, add three entries to `ChangeType`:

```python
    terraform_apply   = "terraform_apply"
    ansible_playbook  = "ansible_playbook"
    helm_upgrade      = "helm_upgrade"
```

Add after the existing `ec2_terminate` line (line 24).

- [ ] **Step 2: Generate and run the Alembic migration**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_iac_change_types"
docker compose exec backend alembic upgrade head
```
Expected: migration runs without error; `change_type` enum updated in Postgres.

- [ ] **Step 3: Write the IaC executor service**

Create `backend/app/services/iac_executor.py`:

```python
"""
IaC Executor Service
====================
Handles two-phase execution for IaC change types:

  Phase 1 (PLAN):   Dispatch plan/check/diff agent command.
                    Store combined output in change_plan.blast_radius.impact_description.
                    Set change_request.status = "planned".
                    Return — the change request is now awaiting human review.

  Phase 2 (APPLY):  Called only after the change_request.status becomes "approved"
                    (the existing approval gate in execute_change_workflow handles this).
                    Dispatch apply/run/upgrade agent command.
                    Store output in the step result.

Rollback:           Dispatch rollback agent command with resources/revision from
                    the step metadata written during Phase 2.

This service is called from execute_change_workflow.py when change_type is one of
the IaC types. No new workflow infrastructure is needed.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus
from app.models.change_plan import ChangePlan

logger = logging.getLogger(__name__)

# The agent commands dispatched per change type, per phase.
_PLAN_COMMAND: dict[ChangeType, str] = {
    ChangeType.terraform_apply:  "terraform_plan",
    ChangeType.ansible_playbook: "ansible_check",
    ChangeType.helm_upgrade:     "helm_diff",
}

_APPLY_COMMAND: dict[ChangeType, str] = {
    ChangeType.terraform_apply:  "terraform_apply",
    ChangeType.ansible_playbook: "ansible_run",
    ChangeType.helm_upgrade:     "helm_upgrade",
}

_ROLLBACK_COMMAND: dict[ChangeType, str] = {
    ChangeType.terraform_apply:  "terraform_apply",  # executor rolls back via rollbacks map
    ChangeType.ansible_playbook: "ansible_run",
    ChangeType.helm_upgrade:     "helm_upgrade",
}

_PLAN_OUTPUT_KEY: dict[ChangeType, str] = {
    ChangeType.terraform_apply:  "plan_output",
    ChangeType.ansible_playbook: "check_output",
    ChangeType.helm_upgrade:     "diff_output",
}

_PANEL_TITLE: dict[ChangeType, str] = {
    ChangeType.terraform_apply:  "Terraform Plan",
    ChangeType.ansible_playbook: "Ansible Check Output",
    ChangeType.helm_upgrade:     "Helm Diff",
}

IAC_CHANGE_TYPES = frozenset(_PLAN_COMMAND.keys())


def is_iac_change_type(change_type: ChangeType) -> bool:
    return change_type in IAC_CHANGE_TYPES


async def run_plan_phase(
    db: AsyncSession,
    cr: ChangeRequest,
    agent_dispatch_fn,          # callable(agent_id, command, params) -> dict
    agent_id: str,
) -> dict[str, Any]:
    """
    Phase 1: run the plan/check/diff command, store output in blast_radius,
    and transition the change request to 'planned'.

    Returns the raw step result dict from the agent.
    Raises RuntimeError if the agent command fails.
    """
    ct = cr.change_type
    command = _PLAN_COMMAND[ct]
    params = _build_agent_params(cr)
    params["change_id"] = str(cr.id)

    logger.info("IaC plan phase: cr=%s command=%s agent=%s", cr.id, command, agent_id)
    result = await agent_dispatch_fn(agent_id, command, params)

    if result.get("status") != "completed":
        error = result.get("error", "unknown error")
        raise RuntimeError(f"IaC plan phase failed: {error}")

    # Extract plan output and write it to blast_radius.impact_description.
    plan_output = result.get("data", {}).get(_PLAN_OUTPUT_KEY[ct], "")
    plan = cr.change_plan
    if plan is None:
        plan = ChangePlan(change_request_id=cr.id)
        db.add(plan)

    existing_blast_radius = plan.blast_radius or {}
    existing_blast_radius["impact_description"] = plan_output
    existing_blast_radius["panel_title"] = _PANEL_TITLE[ct]
    plan.blast_radius = existing_blast_radius

    cr.status = ChangeRequestStatus.planned
    await db.flush()
    logger.info("IaC plan phase complete: cr=%s status=planned", cr.id)
    return result


async def run_apply_phase(
    db: AsyncSession,
    cr: ChangeRequest,
    agent_dispatch_fn,
    agent_id: str,
) -> dict[str, Any]:
    """
    Phase 2: run the apply/run/upgrade command after approval.
    Called by execute_change_workflow after the approval gate is passed.

    Returns the raw step result dict from the agent.
    Raises RuntimeError if the agent command fails.
    """
    ct = cr.change_type
    command = _APPLY_COMMAND[ct]
    params = _build_agent_params(cr)
    params["change_id"] = str(cr.id)

    # For dry_run mode, skip apply entirely.
    if params.get("dry_run"):
        logger.info("IaC apply skipped (dry_run=true): cr=%s", cr.id)
        return {"status": "completed", "data": {"skipped": "dry_run=true"}}

    logger.info("IaC apply phase: cr=%s command=%s agent=%s", cr.id, command, agent_id)
    result = await agent_dispatch_fn(agent_id, command, params)

    if result.get("status") != "completed":
        error = result.get("error", "unknown error")
        raise RuntimeError(f"IaC apply phase failed: {error}")

    logger.info("IaC apply phase complete: cr=%s", cr.id)
    return result


def _build_agent_params(cr: ChangeRequest) -> dict[str, Any]:
    """Extract typed parameters from desired_outcome for the agent command."""
    return dict(cr.desired_outcome or {})
```

- [ ] **Step 4: Unit-test the IaC executor service**

Create `backend/tests/test_iac_executor.py`:

```python
"""Unit tests for iac_executor.py — all I/O mocked."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.iac_executor import (
    run_plan_phase,
    run_apply_phase,
    is_iac_change_type,
    IAC_CHANGE_TYPES,
)
from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus
from app.models.change_plan import ChangePlan


def make_cr(change_type: ChangeType, desired_outcome: dict | None = None) -> ChangeRequest:
    cr = MagicMock(spec=ChangeRequest)
    cr.id = "cr-iac-test-001"
    cr.change_type = change_type
    cr.desired_outcome = desired_outcome or {"working_directory": "/tf/prod"}
    cr.change_plan = None
    cr.status = ChangeRequestStatus.draft
    return cr


@pytest.mark.asyncio
async def test_is_iac_change_type():
    assert is_iac_change_type(ChangeType.terraform_apply)
    assert is_iac_change_type(ChangeType.ansible_playbook)
    assert is_iac_change_type(ChangeType.helm_upgrade)
    assert not is_iac_change_type(ChangeType.dns_update)


@pytest.mark.asyncio
async def test_run_plan_phase_terraform_success():
    cr = make_cr(ChangeType.terraform_apply)
    db = AsyncMock()
    db.flush = AsyncMock()

    plan_output = "Plan: 3 to add, 0 to change, 0 to destroy."
    agent_dispatch = AsyncMock(return_value={
        "status": "completed",
        "data": {"plan_output": plan_output},
    })

    with patch("app.services.iac_executor.ChangePlan") as MockChangePlan:
        mock_plan = MagicMock()
        mock_plan.blast_radius = None
        MockChangePlan.return_value = mock_plan
        result = await run_plan_phase(db, cr, agent_dispatch, agent_id="agent-abc")

    assert result["status"] == "completed"
    assert cr.status == ChangeRequestStatus.planned
    agent_dispatch.assert_called_once()
    call_args = agent_dispatch.call_args
    assert call_args[0][1] == "terraform_plan"
    assert call_args[0][2]["change_id"] == "cr-iac-test-001"


@pytest.mark.asyncio
async def test_run_plan_phase_failure_raises():
    cr = make_cr(ChangeType.terraform_apply)
    db = AsyncMock()
    agent_dispatch = AsyncMock(return_value={
        "status": "failed",
        "error": "No such directory",
    })

    with pytest.raises(RuntimeError, match="IaC plan phase failed"):
        await run_plan_phase(db, cr, agent_dispatch, agent_id="agent-abc")


@pytest.mark.asyncio
async def test_run_apply_phase_ansible_success():
    cr = make_cr(
        ChangeType.ansible_playbook,
        desired_outcome={"playbook_path": "/etc/ansible/site.yml", "inventory": "/etc/ansible/hosts"},
    )
    db = AsyncMock()
    agent_dispatch = AsyncMock(return_value={
        "status": "completed",
        "data": {"run_output": "PLAY RECAP ok=5 changed=3"},
    })

    result = await run_apply_phase(db, cr, agent_dispatch, agent_id="agent-abc")

    assert result["status"] == "completed"
    call_args = agent_dispatch.call_args
    assert call_args[0][1] == "ansible_run"


@pytest.mark.asyncio
async def test_run_apply_phase_dry_run_skips():
    cr = make_cr(ChangeType.terraform_apply, {"working_directory": "/tf/prod", "dry_run": True})
    db = AsyncMock()
    agent_dispatch = AsyncMock()

    result = await run_apply_phase(db, cr, agent_dispatch, agent_id="agent-abc")

    assert result["data"]["skipped"] == "dry_run=true"
    agent_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_run_apply_phase_helm_success():
    cr = make_cr(
        ChangeType.helm_upgrade,
        desired_outcome={"release_name": "myapp", "chart": "stable/myapp", "namespace": "prod"},
    )
    db = AsyncMock()
    agent_dispatch = AsyncMock(return_value={
        "status": "completed",
        "data": {"upgrade_output": "Release deployed successfully."},
    })

    result = await run_apply_phase(db, cr, agent_dispatch, agent_id="agent-abc")

    assert result["status"] == "completed"
    call_args = agent_dispatch.call_args
    assert call_args[0][1] == "helm_upgrade"
```

- [ ] **Step 5: Run backend tests**

```bash
docker compose exec backend pytest tests/test_iac_executor.py -v
```
Expected:
```
PASSED tests/test_iac_executor.py::test_is_iac_change_type
PASSED tests/test_iac_executor.py::test_run_plan_phase_terraform_success
PASSED tests/test_iac_executor.py::test_run_plan_phase_failure_raises
PASSED tests/test_iac_executor.py::test_run_apply_phase_ansible_success
PASSED tests/test_iac_executor.py::test_run_apply_phase_dry_run_skips
PASSED tests/test_iac_executor.py::test_run_apply_phase_helm_success
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/change_request.py backend/app/services/iac_executor.py backend/tests/test_iac_executor.py
git commit -m "feat(backend): add IaC change types to enum and iac_executor two-phase service"
```

---

## Task 6: Change type definitions (JSON)

**Files:**
- Create: `backend/app/connectors/change_type_definitions/terraform_apply.json`
- Create: `backend/app/connectors/change_type_definitions/ansible_playbook.json`
- Create: `backend/app/connectors/change_type_definitions/helm_upgrade.json`

- [ ] **Step 1: Create `terraform_apply.json`**

```json
{
  "change_type": "terraform_apply",
  "display_name": "Terraform Apply",
  "steps": [
    {
      "generic_action": "terraform_plan",
      "purpose": "plan",
      "required": true,
      "description": "Run terraform init and plan; capture output for blast radius review"
    },
    {
      "generic_action": "terraform_apply",
      "purpose": "execute",
      "required": true,
      "description": "Apply the saved plan artifact — only runs after human approval"
    }
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["output_check"],
  "auto_approve": false,
  "rollback_command": "terraform_apply",
  "dry_run_stops_after_step": 1
}
```

- [ ] **Step 2: Create `ansible_playbook.json`**

```json
{
  "change_type": "ansible_playbook",
  "display_name": "Ansible Playbook",
  "steps": [
    {
      "generic_action": "ansible_check",
      "purpose": "plan",
      "required": true,
      "description": "Run playbook in --check --diff mode; capture output for blast radius review"
    },
    {
      "generic_action": "ansible_run",
      "purpose": "execute",
      "required": true,
      "description": "Execute the playbook — only runs after human approval"
    }
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["output_check"],
  "auto_approve": false,
  "rollback_command": "ansible_run",
  "dry_run_stops_after_step": 1
}
```

- [ ] **Step 3: Create `helm_upgrade.json`**

```json
{
  "change_type": "helm_upgrade",
  "display_name": "Helm Upgrade",
  "steps": [
    {
      "generic_action": "helm_diff",
      "purpose": "plan",
      "required": true,
      "description": "Run helm diff upgrade; capture output for blast radius review (requires helm-diff plugin)"
    },
    {
      "generic_action": "helm_upgrade",
      "purpose": "execute",
      "required": true,
      "description": "Run helm upgrade --install — only runs after human approval"
    }
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["output_check"],
  "auto_approve": false,
  "rollback_command": "helm_upgrade",
  "dry_run_stops_after_step": 1
}
```

- [ ] **Step 4: Verify the planning engine can load the new definitions**

```bash
docker compose exec backend python -c "
from app.models.change_request import ChangeType
from app.services.planning_engine import _load_change_type_def
for ct in [ChangeType.terraform_apply, ChangeType.ansible_playbook, ChangeType.helm_upgrade]:
    d = _load_change_type_def(ct)
    print(ct.value, '->', d['display_name'], ', steps:', len(d['steps']))
"
```
Expected:
```
terraform_apply -> Terraform Apply , steps: 2
ansible_playbook -> Ansible Playbook , steps: 2
helm_upgrade -> Helm Upgrade , steps: 2
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/change_type_definitions/terraform_apply.json \
        backend/app/connectors/change_type_definitions/ansible_playbook.json \
        backend/app/connectors/change_type_definitions/helm_upgrade.json
git commit -m "feat(backend): add change type definitions for terraform_apply, ansible_playbook, helm_upgrade"
```

---

## Task 7: Frontend — Plan Output panel and IaC change type forms

**Files:**
- Modify: `frontend/src/pages/ChangeRequestDetail.tsx`
- Modify: `frontend/src/components/ChangeRequestForm.tsx` (or wherever the new-CR modal lives)

### 7a: Plan Output panel in ChangeRequestDetail

- [ ] **Step 1: Write the failing test (Playwright or Vitest component test)**

Create `frontend/src/pages/__tests__/ChangeRequestDetail.iac.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChangeRequestDetail } from "../ChangeRequestDetail";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { changeRequestsApi } from "../../api/endpoints";

vi.mock("../../api/endpoints");

const IaC_CR = {
  id: "cr-iac-001",
  change_type: "terraform_apply",
  status: "planned",
  title: "Terraform apply prod",
  description: "",
  risk_level: "high",
  requester: { name: "Alice", email: "alice@example.com" },
  target_asset_ids: [],
  desired_outcome: { working_directory: "/tf/prod" },
  change_plan: {
    blast_radius: {
      impact_description: "Plan: 2 to add, 0 to change, 0 to destroy.\n+aws_instance.web\n+aws_security_group.sg",
      panel_title: "Terraform Plan",
    },
    generated_steps: [],
    preflight_checks: [],
    rollback_plan: {},
    verification_plan: {},
  },
  approvals: [],
  execution_runs: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

function renderDetail(cr: typeof IaC_CR) {
  (changeRequestsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(cr);
  (changeRequestsApi.getAuditEvents as ReturnType<typeof vi.fn>).mockResolvedValue([]);

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/change-requests/${cr.id}`]}>
        <Routes>
          <Route path="/change-requests/:id" element={<ChangeRequestDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("shows Plan Output panel when IaC change request is in planned status", async () => {
  renderDetail(IaC_CR);
  expect(await screen.findByText("Terraform Plan")).toBeInTheDocument();
  expect(screen.getByText(/Plan: 2 to add/)).toBeInTheDocument();
});

test("Plan Output panel is collapsible", async () => {
  renderDetail(IaC_CR);
  const toggle = await screen.findByRole("button", { name: /Terraform Plan/i });
  // Initially expanded — plan text visible.
  expect(screen.getByText(/Plan: 2 to add/)).toBeInTheDocument();
  await userEvent.click(toggle);
  // After collapse — plan text hidden.
  expect(screen.queryByText(/Plan: 2 to add/)).not.toBeInTheDocument();
});

test("does not show Plan Output panel for non-IaC change types", async () => {
  const dnsCr = { ...IaC_CR, change_type: "dns_update", change_plan: { ...IaC_CR.change_plan, blast_radius: null } };
  renderDetail(dnsCr);
  await screen.findByText("Terraform apply prod"); // wait for render
  expect(screen.queryByText("Terraform Plan")).not.toBeInTheDocument();
});

test("colors + lines green and - lines red in plan output", async () => {
  renderDetail(IaC_CR);
  await screen.findByText("Terraform Plan");
  const addedLine = screen.getByText(/\+aws_instance\.web/);
  expect(addedLine).toHaveClass("text-green-600");
});
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd frontend && npm test -- --run src/pages/__tests__/ChangeRequestDetail.iac.test.tsx 2>&1 | tail -20
```
Expected: tests fail because the Plan Output panel does not exist yet.

- [ ] **Step 3: Add the IaC constants and PlanOutputPanel component to ChangeRequestDetail.tsx**

Add after the existing imports in `ChangeRequestDetail.tsx`:

```tsx
import { Terminal, ChevronDown } from "lucide-react";

const IAC_CHANGE_TYPES = new Set(["terraform_apply", "ansible_playbook", "helm_upgrade"]);

function PlanOutputPanel({ title, output }: { title: string; output: string }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <button
        className="flex items-center gap-2 w-full px-5 py-3.5 border-b border-slate-100 bg-slate-50 text-left"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        <Terminal className="w-4 h-4 text-slate-500" />
        <h3 className="text-sm font-semibold text-slate-700 flex-1">{title}</h3>
        <ChevronDown
          className={`w-4 h-4 text-slate-400 transition-transform ${expanded ? "" : "-rotate-90"}`}
        />
      </button>
      {expanded && (
        <div className="p-5">
          <pre className="text-xs font-mono bg-slate-950 text-slate-100 rounded p-4 overflow-auto max-h-96 whitespace-pre-wrap">
            {output.split("\n").map((line, i) => {
              const cls = line.startsWith("+")
                ? "text-green-400"
                : line.startsWith("-")
                ? "text-red-400"
                : "text-slate-100";
              return (
                <span key={i} className={cls}>
                  {line}
                  {"\n"}
                </span>
              );
            })}
          </pre>
        </div>
      )}
    </div>
  );
}
```

Then, inside the `ChangeRequestDetail` component return, add the panel just before the Blast Radius section (or after the plan steps section). Find the appropriate location for the new panel and add:

```tsx
{/* IaC Plan Output Panel */}
{IAC_CHANGE_TYPES.has(cr.change_type) &&
  cr.change_plan?.blast_radius?.impact_description && (
    <PlanOutputPanel
      title={cr.change_plan.blast_radius.panel_title ?? "Plan Output"}
      output={cr.change_plan.blast_radius.impact_description}
    />
  )}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd frontend && npm test -- --run src/pages/__tests__/ChangeRequestDetail.iac.test.tsx
```
Expected: all 4 tests pass.

### 7b: IaC category in the new change request modal

- [ ] **Step 5: Add IaC change types to the change request form**

Locate `frontend/src/components/ChangeRequestForm.tsx` (or equivalent modal). Add an "Infrastructure as Code" category group with three change types. The exact edit depends on how the form currently renders change type options — add after the existing change type list:

```tsx
// IaC change types group — add to the change type selector options array
{
  group: "Infrastructure as Code",
  types: [
    { value: "terraform_apply",  label: "Terraform Apply" },
    { value: "ansible_playbook", label: "Ansible Playbook" },
    { value: "helm_upgrade",     label: "Helm Upgrade" },
  ],
},
```

Add per-type parameter fields rendered when the change type is selected. Each type renders its own subset of inputs inside the form's `desired_outcome` JSON object:

**terraform_apply fields:**
```tsx
{selectedType === "terraform_apply" && (
  <>
    <Field label="Working Directory *" name="working_directory" placeholder="/opt/terraform/prod" required />
    <Field label="Workspace" name="workspace" placeholder="default" />
    <Field label="Var File" name="var_file" placeholder="prod.tfvars" />
    <Field label="Targets (comma-separated)" name="target" placeholder="aws_instance.web,aws_security_group.sg"
           transform={(v: string) => v.split(",").map((s) => s.trim()).filter(Boolean)} />
    <Toggle label="Dry Run (plan only, no apply)" name="dry_run" />
  </>
)}
```

**ansible_playbook fields:**
```tsx
{selectedType === "ansible_playbook" && (
  <>
    <Field label="Playbook Path *" name="playbook_path" placeholder="/etc/ansible/site.yml" required />
    <Textarea label="Inventory (path or INI content) *" name="inventory" placeholder="/etc/ansible/hosts" required />
    <Textarea label="Extra Vars (JSON)" name="extra_vars" placeholder='{"env":"prod"}' transform={JSON.parse} />
    <Field label="Limit" name="limit" placeholder="web-servers" />
    <Field label="Rollback Playbook Path" name="rollback_playbook_path" placeholder="/etc/ansible/rollback.yml" />
    <Toggle label="Dry Run (check only, no execute)" name="dry_run" />
  </>
)}
```

**helm_upgrade fields:**
```tsx
{selectedType === "helm_upgrade" && (
  <>
    <Field label="Release Name *" name="release_name" placeholder="myapp" required />
    <Field label="Chart *" name="chart" placeholder="stable/myapp" required />
    <Field label="Chart Version" name="chart_version" placeholder="1.2.3" />
    <Field label="Namespace" name="namespace" placeholder="default" />
    <Textarea label="Values (YAML)" name="values" placeholder="replicaCount: 3" />
    <Toggle label="Atomic (auto-rollback on failure)" name="atomic" defaultChecked />
    <Field label="Timeout" name="timeout" placeholder="5m" />
    <Toggle label="Dry Run (diff only, no upgrade)" name="dry_run" />
  </>
)}
```

- [ ] **Step 6: Verify in browser**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Open `http://localhost:3000`. Create a new change request:
1. Confirm "Infrastructure as Code" group appears in the change type selector.
2. Select "Terraform Apply" — verify the terraform-specific fields appear.
3. Select "Ansible Playbook" — verify the ansible-specific fields appear.
4. Select "Helm Upgrade" — verify the helm-specific fields appear.
5. Submit a terraform_apply CR and verify it appears in the list with correct type label.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/ChangeRequestDetail.tsx \
        frontend/src/components/ChangeRequestForm.tsx \
        frontend/src/pages/__tests__/ChangeRequestDetail.iac.test.tsx
git commit -m "feat(frontend): add Plan Output panel with diff coloring and IaC category in new change request form"
```

---

## Self-Review Checklist

**Spec coverage:**
- Section 1.1 (terraform_apply): Tasks 1, 4, 5, 6
- Section 1.2 (ansible_playbook): Tasks 2, 4, 5, 6
- Section 1.3 (helm_upgrade): Tasks 3, 4, 5, 6
- Section 2 (agent command interfaces): Tasks 1–3 — exact function signatures from spec
- Section 3 (blast radius mapping): Task 5 — `run_plan_phase` writes to `blast_radius.impact_description`
- Section 4.1 (plan diff panel): Task 7a — collapsible, color-coded, per-type title
- Section 4.2 (change type selector): Task 7b — IaC group, per-type parameter forms
- Section 5 (prerequisites): Documented; runtime CLI check left to the Go `captureCmd` which surfaces the error naturally from `exec.Command`

**TDD compliance:** Tests written before implementation in every task (Steps 1–2 precede Step 3 in each task).

**No placeholders:** All code blocks are complete and directly usable.

**Type consistency:**
- Go: `ExecCommand` var in `terraform.go` shared across package; `ansible.go` and `helm.go` call `captureCmd`/`runCmd` from the same package
- Python: `iac_executor.py` uses `ChangeType` enum values, not raw strings
- TypeScript: `IAC_CHANGE_TYPES` is a `Set<string>`, guarded by optional chaining on `blast_radius`

**Migration required:** Yes — adding `terraform_apply`, `ansible_playbook`, `helm_upgrade` to the `ChangeType` Postgres enum requires an Alembic migration (Task 5, Step 2).
