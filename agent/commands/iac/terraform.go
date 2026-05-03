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
