//go:build darwin

package ossecurity

import (
	"os/exec"
	"strings"
	"testing"
)

func TestSelinuxExecuteOS_DarwinEnforcing(t *testing.T) {
	var cmds []string
	execCommandSELinux = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "assessments enabled")
	}
	t.Cleanup(func() { execCommandSELinux = exec.Command })

	result, err := selinuxExecuteOS(map[string]any{"mode": "enforcing"})
	if err != nil {
		t.Fatalf("selinuxExecuteOS: %v", err)
	}
	if result["config_snapshot"] == nil {
		t.Fatal("expected config_snapshot")
	}
	foundSpctl := false
	for _, c := range cmds {
		if strings.Contains(c, "spctl") {
			foundSpctl = true
		}
	}
	if !foundSpctl {
		t.Fatalf("expected spctl call; got: %v", cmds)
	}
}

func TestSelinuxRollbackOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandSELinux = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandSELinux = exec.Command })

	result, err := selinuxRollbackOS(map[string]any{
		"config_snapshot": map[string]any{
			"gatekeeper": "assessments enabled",
			"santa_mode": "MONITOR",
		},
	})
	if err != nil {
		t.Fatalf("selinuxRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
