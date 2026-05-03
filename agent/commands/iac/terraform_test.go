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
		case "ansible_check_ok":
			os.Stdout.WriteString("PLAY RECAP *** changed=0 unreachable=0 failed=0\n")
			os.Exit(0)
		case "ansible_run_ok":
			os.Stdout.WriteString("PLAY RECAP *** ok=5 changed=3 unreachable=0 failed=0\n")
			os.Exit(0)
		case "helm_diff_ok":
			os.Stdout.WriteString("Comparing myapp chart: replicas 1 -> 3\n")
			os.Exit(0)
		case "helm_upgrade_ok":
			os.Stdout.WriteString("Release \"myapp\" has been deployed successfully.\n")
			os.Exit(0)
		case "helm_rollback_ok":
			os.Stdout.WriteString("Rollback was a success! Happy Helming!\n")
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
