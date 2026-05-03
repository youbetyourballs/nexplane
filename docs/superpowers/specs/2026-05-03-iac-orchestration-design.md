# IaC Orchestration — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** Treat Terraform, Ansible, and Helm operations as tracked, auditable Nexplane change types with plan-before-apply semantics, blast-radius capture, human approval gates, and rollback support.

---

## Background

Infrastructure changes today are executed outside Nexplane — engineers run `terraform apply` or `helm upgrade` in a terminal with no audit trail, no approval gate, and no rollback record tied to the platform. This spec introduces three new change types (`terraform_apply`, `ansible_playbook`, `helm_upgrade`) and a new agent package (`agent/commands/iac/`) that wire IaC operations into the existing change-request lifecycle: draft → planned → approved → executing → completed, with rollback at every stage.

A fourth pair of change types (`pulumi_up`, `cdk_deploy`) is sketched for future-proofing but not implemented in this iteration.

---

## Design Decisions

- **Agent-executed, CLI-wrapping:** The agent on the target host runs the IaC CLI (`terraform`, `ansible-playbook`, `helm`). The CLI must be present on the host — document as a prerequisite. No Nexplane-side IaC engine.
- **Plan output = blast radius:** Terraform plan output and Ansible check-mode output are stored verbatim as `blast_radius.impact_description` on the change request. The UI renders this before the approval step so reviewers see exactly what will change.
- **Two-phase execution:** Step 1 is always a dry-run/plan. Step 2 (apply) is gated on human approval in the Nexplane UI. `auto_approve: false` is the default for all IaC change types.
- **Helm prefers Kubernetes connector action over agent command:** The K8s connector already holds kubeconfig credentials. Helm upgrade is implemented as a connector action on the Kubernetes connector, not a new agent command. Agent-based fallback (`agent/commands/iac/helm.go`) is provided for hosts that have `helm` + `kubectl` installed locally.
- **Rollback is change-type-specific:** Terraform uses `terraform apply` targeting affected resources (or `terraform destroy` for net-new). Ansible rollback runs a designated `rollback_playbook_path`. Helm uses `helm rollback <release> <revision>`.
- **`dry_run` parameter on every IaC change type:** Setting `dry_run: true` runs only the plan/check step and marks the change completed without applying — useful for scheduled drift detection.
- **Existing change executor model unchanged:** New change types register their steps through the existing step-dispatch mechanism. No new executor infrastructure required.

---

## Section 1: New Change Types

### 1.1 `terraform_apply`

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `working_directory` | string | yes | — | Absolute path on the target host where the Terraform root module lives |
| `workspace` | string | no | `"default"` | Terraform workspace to select before planning |
| `var_file` | string | no | — | Path to a `.tfvars` file, relative to `working_directory` |
| `target` | string[] | no | — | Specific resources to target (`-target` flag); empty = all |
| `auto_approve` | bool | no | `false` | Skip human approval gate and apply immediately after plan |
| `dry_run` | bool | no | `false` | Run plan only; never apply |

**Execution steps:**

```
Step 1  PLAN    agent → terraform_plan command
                  terraform init
                  terraform workspace select <workspace>
                  terraform plan -out=<change_id>.tfplan [-var-file=...] [-target=...]
                  Capture stdout + stderr → blast_radius.impact_description
                  Store tfplan artifact path on agent host

Step 2  REVIEW  Nexplane sets change_request.status = "planned"
                  UI renders blast_radius.impact_description as preformatted diff
                  Reviewer approves or rejects

Step 3  APPLY   (skipped if dry_run=true or reviewer rejected)
                  agent → terraform_apply command
                  terraform apply <change_id>.tfplan
                  Capture exit code and output → step result

Rollback        agent → terraform_rollback command
                  For modified resources: terraform apply -target=<resource> (restores prior state)
                  For net-new resources:  terraform destroy -target=<resource>
```

### 1.2 `ansible_playbook`

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `playbook_path` | string | yes | — | Absolute path to the playbook on the control node |
| `inventory` | string | yes | — | Inventory file path or inline INI/YAML string |
| `extra_vars` | object | no | `{}` | Key-value pairs passed as `--extra-vars` JSON |
| `limit` | string | no | — | Host pattern passed to `--limit` |
| `check_mode` | bool | no | `true` | Run `--check` as the plan step; set false to skip |
| `dry_run` | bool | no | `false` | Run check only; never execute |
| `rollback_playbook_path` | string | no | — | Absolute path to a rollback playbook (required if rollback is desired) |

**Execution steps:**

```
Step 1  CHECK   agent → ansible_check command
                  ansible-playbook <playbook_path> --check --diff
                    [-i <inventory>] [--extra-vars '<json>'] [--limit <pattern>]
                  Capture stdout + stderr → blast_radius.impact_description

Step 2  REVIEW  Nexplane sets change_request.status = "planned"
                  UI renders check-mode diff output

Step 3  EXECUTE (skipped if dry_run=true or rejected)
                  agent → ansible_run command
                  ansible-playbook <playbook_path> [-i ...] [--extra-vars ...] [--limit ...]
                  Capture exit code and output → step result

Rollback        If rollback_playbook_path is set:
                  agent → ansible_run command with rollback_playbook_path
                Otherwise: no automated rollback (log warning)
```

### 1.3 `helm_upgrade`

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `release_name` | string | yes | — | Helm release name |
| `chart` | string | yes | — | Chart reference (`repo/chart` or local path) |
| `chart_version` | string | no | — | Pinned chart version; omit for latest |
| `namespace` | string | no | `"default"` | Kubernetes namespace |
| `values` | string | no | `""` | Inline YAML values (written to a temp file and passed via `-f`) |
| `atomic` | bool | no | `true` | Pass `--atomic`; Helm auto-rolls back if pods fail readiness |
| `timeout` | string | no | `"5m"` | `--timeout` value |
| `dry_run` | bool | no | `false` | Run `helm upgrade --dry-run` only; never apply |

**Execution steps:**

```
Step 1  DIFF    Kubernetes connector action OR agent → helm_diff command
                  helm diff upgrade <release_name> <chart> [--version ...] [-f values.yaml]
                  (requires helm-diff plugin)
                  Capture diff output → blast_radius.impact_description

Step 2  REVIEW  Nexplane sets change_request.status = "planned"
                  UI renders helm diff output

Step 3  UPGRADE (skipped if dry_run=true or rejected)
                  Kubernetes connector action OR agent → helm_upgrade command
                  helm upgrade --install [--atomic] [--timeout 5m]
                    -n <namespace> [-f values.yaml] [--version ...] <release_name> <chart>
                  Capture exit code → step result

Rollback        Kubernetes connector action OR agent → helm_rollback command
                  helm rollback <release_name> <previous-revision> -n <namespace>
```

**Implementation preference:** Use the Kubernetes connector action when a K8s connector is configured for the target cluster (kubeconfig sourced from connector credentials). Fall back to `agent/commands/iac/helm.go` for hosts with `helm` + `kubectl` installed locally.

### 1.4 Future: `pulumi_up` and `cdk_deploy`

Same plan → review → deploy pattern as `terraform_apply`. Not implemented in this iteration.

| Change type | Plan command | Apply command | Rollback |
|-------------|-------------|---------------|---------|
| `pulumi_up` | `pulumi preview --json` | `pulumi up --yes` | `pulumi destroy --target <urn>` |
| `cdk_deploy` | `cdk diff` | `cdk deploy --require-approval never` | `cdk destroy` |

Agent commands: `agent/commands/iac/pulumi.go`, `agent/commands/iac/cdk.go`.

---

## Section 2: Agent Command Interfaces

New package: `agent/commands/iac/`

### 2.1 `agent/commands/iac/terraform.go`

```go
package iac

import (
    "context"
    "fmt"
    "os/exec"
    "path/filepath"
    "strings"
)

// TerraformParams matches the terraform_apply change type parameters.
type TerraformParams struct {
    WorkingDirectory string   `json:"working_directory"`
    Workspace        string   `json:"workspace"`
    VarFile          string   `json:"var_file"`
    Target           []string `json:"target"`
    AutoApprove      bool     `json:"auto_approve"`
    DryRun           bool     `json:"dry_run"`
    ChangeID         string   `json:"change_id"` // used to name the plan artifact
}

// TerraformPlan runs init + plan, returns captured output for blast radius.
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
    cmd := exec.CommandContext(ctx, name, args...)
    cmd.Dir = dir
    out, err := cmd.CombinedOutput()
    if err != nil {
        return fmt.Errorf("%w\n%s", err, strings.TrimSpace(string(out)))
    }
    return nil
}

func captureCmd(ctx context.Context, dir, name string, args ...string) (string, error) {
    cmd := exec.CommandContext(ctx, name, args...)
    cmd.Dir = dir
    out, err := cmd.CombinedOutput()
    if err != nil {
        return string(out), fmt.Errorf("%w\n%s", err, strings.TrimSpace(string(out)))
    }
    return string(out), nil
}
```

### 2.2 `agent/commands/iac/ansible.go`

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
    PlaybookPath         string            `json:"playbook_path"`
    Inventory            string            `json:"inventory"`
    ExtraVars            map[string]any    `json:"extra_vars"`
    Limit                string            `json:"limit"`
    CheckMode            bool              `json:"check_mode"`
    DryRun               bool              `json:"dry_run"`
    RollbackPlaybookPath string            `json:"rollback_playbook_path"`
    ChangeID             string            `json:"change_id"`
}

// AnsibleCheck runs ansible-playbook --check --diff, returns output for blast radius.
func AnsibleCheck(ctx context.Context, p AnsibleParams) (string, error) {
    args, cleanup, err := buildAnsibleArgs(p)
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
    args, cleanup, err := buildAnsibleArgs(p)
    if cleanup != nil {
        defer cleanup()
    }
    if err != nil {
        return "", err
    }
    // Replace playbook path (first element after args built from params)
    fullArgs := append([]string{playbook}, args[1:]...)
    return captureCmd(ctx, filepath.Dir(playbook), "ansible-playbook", fullArgs...)
}

func buildAnsibleArgs(p AnsibleParams) ([]string, func(), error) {
    args := []string{p.PlaybookPath}
    var cleanup func()

    // Inventory: write inline content to a temp file if it's not a file path.
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

### 2.3 `agent/commands/iac/helm.go`

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

// HelmRollback rolls back to the previous release revision.
func HelmRollback(ctx context.Context, p HelmParams, previousRevision int) (string, error) {
    args := []string{"rollback", p.ReleaseName, fmt.Sprintf("%d", previousRevision),
        "-n", namespace(p)}
    return captureCmd(ctx, ".", "helm", args...)
}

func baseHelmArgs(subcmd1, subcmd2 string, p HelmParams) ([]string, func(), error) {
    args := []string{subcmd1, subcmd2, p.ReleaseName, p.Chart,
        "-n", namespace(p)}
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

func namespace(p HelmParams) string {
    if p.Namespace == "" {
        return "default"
    }
    return p.Namespace
}
```

---

## Section 3: Blast Radius Mapping

The existing change request model has a `blast_radius` field. IaC plan output maps to it as follows:

| IaC tool | Plan step output | `blast_radius.impact_description` |
|----------|-----------------|----------------------------------|
| Terraform | `terraform plan` stdout (including resource diff lines) | Verbatim plan text, stored after `Step 1` completes |
| Ansible | `ansible-playbook --check --diff` stdout | Verbatim check-mode diff output |
| Helm | `helm diff upgrade` stdout | Verbatim helm-diff output |

**Backend change (Python — `backend/app/services/change_executor.py` or equivalent):**

After Step 1 completes for any IaC change type, the executor writes the step output back to the change request:

```python
await change_request_repo.update_blast_radius(
    change_id=change_request.id,
    impact_description=step_result.output,
)
await change_request_repo.set_status(change_request.id, "planned")
```

The executor then pauses and waits for the change request status to become `"approved"` before dispatching Step 2. This is the existing approval-gate pattern — no new infrastructure needed.

---

## Section 4: UI Changes

### 4.1 Change Request Detail — Plan Diff Panel

When a change request of an IaC type reaches `"planned"` status, the detail view renders `blast_radius.impact_description` in a dedicated **Plan Output** panel:

- Preformatted monospace block with syntax highlighting (green `+` lines, red `-` lines)
- Panel title changes per type: "Terraform Plan", "Ansible Check Output", "Helm Diff"
- Approve / Reject buttons remain in the existing approval section

No new API endpoints needed — the existing `GET /api/change-requests/{id}` response already includes `blast_radius`.

### 4.2 Change Type Selector

Extend the change-request creation form to surface three new change types under an "Infrastructure as Code" category with per-type parameter forms:

| Change type label | Internal type key | Parameter form fields |
|-------------------|------------------|-----------------------|
| Terraform Apply | `terraform_apply` | Working directory, workspace, var file, targets (multi), auto-approve toggle, dry run toggle |
| Ansible Playbook | `ansible_playbook` | Playbook path, inventory (textarea or file path), extra vars (JSON editor), limit, check mode toggle, rollback playbook path |
| Helm Upgrade | `helm_upgrade` | Release name, chart, chart version, namespace, values (YAML textarea), atomic toggle, timeout, dry run toggle |

---

## Section 5: Prerequisites

These are documented in the Nexplane host-onboarding guide and enforced by the agent at command dispatch time (agent returns a descriptive error if the CLI is missing):

| Change type | Required on target host | Checked by |
|-------------|------------------------|------------|
| `terraform_apply` | `terraform` CLI | `agent/commands/iac/terraform.go` checks `which terraform` |
| `ansible_playbook` | `ansible-playbook` CLI | `agent/commands/iac/ansible.go` checks `which ansible-playbook` |
| `helm_upgrade` (agent path) | `helm` CLI, `helm-diff` plugin, `kubectl` + kubeconfig | `agent/commands/iac/helm.go` checks each |
| `helm_upgrade` (connector path) | Kubernetes connector configured with kubeconfig | K8s connector validates on action dispatch |

---

## Files Changed

| File | Change |
|------|--------|
| `agent/commands/iac/terraform.go` | New — `TerraformPlan`, `TerraformApply`, `TerraformRollback` |
| `agent/commands/iac/ansible.go` | New — `AnsibleCheck`, `AnsibleRun` (handles rollback playbook) |
| `agent/commands/iac/helm.go` | New — `HelmDiff`, `HelmUpgrade`, `HelmRollback` |
| `backend/app/models/change_request.py` | Add `terraform_apply`, `ansible_playbook`, `helm_upgrade` to change type enum |
| `backend/app/schemas/change_request.py` | Add per-type parameter schemas (`TerraformApplyParams`, `AnsiblePlaybookParams`, `HelmUpgradeParams`) |
| `backend/app/services/change_executor.py` | Register IaC change types; write plan output to `blast_radius` after Step 1; set status to `"planned"` and wait for approval before Step 2 |
| `backend/app/services/connectors/kubernetes.py` | Add `helm_diff`, `helm_upgrade`, `helm_rollback` connector actions |
| `frontend/src/components/ChangeRequestForm.tsx` | Add IaC change type category and per-type parameter forms |
| `frontend/src/components/ChangeRequestDetail.tsx` | Add Plan Output panel rendered from `blast_radius.impact_description` when status is `"planned"` |
| `docs/host-prerequisites.md` | Document IaC CLI requirements per change type |
