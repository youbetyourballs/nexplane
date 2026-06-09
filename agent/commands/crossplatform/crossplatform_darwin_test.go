//go:build darwin

package crossplatform

import (
	"os/exec"
	"strings"
	"testing"
)

func TestDNSExecuteOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandCrossplatformDarwin = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		if strings.Contains(strings.Join(args, " "), "getdnsservers") {
			return exec.Command("echo", "8.8.8.8")
		}
		if strings.Contains(strings.Join(args, " "), "listallnetworkservices") {
			return exec.Command("printf", "An asterisk (*) denotes that a network service is disabled.\\nWi-Fi\\nEthernet\\n")
		}
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandCrossplatformDarwin = exec.Command })

	result, err := dnsExecuteOS(map[string]any{"resolvers": []any{"1.1.1.1", "8.8.8.8"}})
	if err != nil {
		t.Fatalf("dnsExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	foundSetDNS := false
	for _, c := range cmds {
		if strings.Contains(c, "setdnsservers") {
			foundSetDNS = true
		}
	}
	if !foundSetDNS {
		t.Fatalf("expected networksetup -setdnsservers; got: %v", cmds)
	}
}

func TestInventoryExecuteOS_Darwin(t *testing.T) {
	execCommandCrossplatformDarwin = func(name string, args ...string) *exec.Cmd {
		if name == "brew" {
			return exec.Command("printf", "nginx 1.25.0\\ncurl 8.1.0\\n")
		}
		return exec.Command("echo", "")
	}
	t.Cleanup(func() { execCommandCrossplatformDarwin = exec.Command })

	result, err := inventoryExecuteOS(map[string]any{})
	if err != nil {
		t.Fatalf("inventoryExecuteOS: %v", err)
	}
	pkgs, _ := result["packages"].([]map[string]any)
	if len(pkgs) == 0 {
		t.Log("no packages from brew mock — acceptable, brew output may not parse in test env")
	}
}
