package iac_test

import (
	"context"
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
