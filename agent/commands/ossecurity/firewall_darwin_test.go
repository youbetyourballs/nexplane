//go:build darwin

package ossecurity

import (
	"os/exec"
	"strings"
	"testing"
)

func TestFirewallExecuteOS_DarwinAddRule(t *testing.T) {
	var cmds []string
	execCommandFirewall = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "Application firewall is enabled. (State = 1)")
	}
	t.Cleanup(func() { execCommandFirewall = exec.Command })

	params := map[string]any{
		"action": "add_rule",
		"rule": map[string]any{
			"app_path": "/Applications/Foo.app",
		},
	}
	result, err := firewallExecuteOS(params)
	if err != nil {
		t.Fatalf("firewallExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot in result")
	}
	found := false
	for _, c := range cmds {
		if strings.Contains(c, "socketfilterfw") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected socketfilterfw call; got: %v", cmds)
	}
}

func TestFirewallRollbackOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandFirewall = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandFirewall = exec.Command })

	result, err := firewallRollbackOS(map[string]any{
		"snapshot": "Application firewall is enabled.",
		"tool":     "socketfilterfw",
	})
	if err != nil {
		t.Fatalf("firewallRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
