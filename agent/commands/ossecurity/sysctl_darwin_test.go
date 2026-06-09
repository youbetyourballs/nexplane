//go:build darwin

package ossecurity

import (
	"os/exec"
	"strings"
	"testing"
)

func TestSysctlExecuteOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandSysctl = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "0")
	}
	t.Cleanup(func() { execCommandSysctl = exec.Command })

	result, err := sysctlExecuteOS(map[string]any{})
	if err != nil {
		t.Fatalf("sysctlExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	foundSysctl := false
	for _, c := range cmds {
		if strings.Contains(c, "sysctl") && strings.Contains(c, "-w") {
			foundSysctl = true
		}
	}
	if !foundSysctl {
		t.Fatalf("expected sysctl -w calls; got: %v", cmds)
	}
}

func TestSysctlRollbackOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandSysctl = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandSysctl = exec.Command })

	result, err := sysctlRollbackOS(map[string]any{
		"snapshot": "net.inet.ip.forwarding: 0\n",
	})
	if err != nil {
		t.Fatalf("sysctlRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
