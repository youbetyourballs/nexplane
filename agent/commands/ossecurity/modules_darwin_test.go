//go:build darwin

package ossecurity

import (
	"os/exec"
	"strings"
	"testing"
)

func TestBlacklistExecuteOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandModules = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "com.apple.driver.FakeDriver  0 0")
	}
	t.Cleanup(func() { execCommandModules = exec.Command })

	result, err := blacklistExecuteOS(map[string]any{
		"modules": []any{"com.apple.driver.FakeDriver"},
	})
	if err != nil {
		t.Fatalf("blacklistExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	foundSysext := false
	for _, c := range cmds {
		if strings.Contains(c, "systemextensionsctl") {
			foundSysext = true
		}
	}
	if !foundSysext {
		t.Fatalf("expected systemextensionsctl; got: %v", cmds)
	}
}

func TestBlacklistRollbackOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandModules = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandModules = exec.Command })

	result, err := blacklistRollbackOS(map[string]any{
		"snapshot": "com.apple.driver.FakeDriver  1 0",
	})
	if err != nil {
		t.Fatalf("blacklistRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
