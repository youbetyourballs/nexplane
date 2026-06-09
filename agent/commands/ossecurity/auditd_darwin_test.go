//go:build darwin

package ossecurity

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestAuditdExecuteOS_DarwinCISLevel1(t *testing.T) {
	tmpFile := t.TempDir() + "/audit_control"
	origPath := bsmAuditControl
	bsmAuditControl = tmpFile
	t.Cleanup(func() { bsmAuditControl = origPath })

	execCommandAuditd = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandAuditd = exec.Command })

	result, err := auditdExecuteOS(map[string]any{"profile": "cis_level1"})
	if err != nil {
		t.Fatalf("auditdExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	data, _ := os.ReadFile(tmpFile)
	if !strings.Contains(string(data), "dir:/var/audit") {
		t.Fatalf("expected audit_control written; got: %s", data)
	}
}

func TestAuditdRollbackOS_Darwin(t *testing.T) {
	tmpFile := t.TempDir() + "/audit_control"
	origPath := bsmAuditControl
	bsmAuditControl = tmpFile
	t.Cleanup(func() { bsmAuditControl = origPath })

	execCommandAuditd = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandAuditd = exec.Command })

	result, err := auditdRollbackOS(map[string]any{
		"snapshot": "dir:/var/audit\nflags:lo\n",
	})
	if err != nil {
		t.Fatalf("auditdRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
