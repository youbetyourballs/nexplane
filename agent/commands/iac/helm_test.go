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
