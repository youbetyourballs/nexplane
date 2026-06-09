//go:build darwin

package changip

import (
	"encoding/json"
	"os/exec"
	"strings"
	"testing"
)

func TestExecuteOS_DarwinAutoMode(t *testing.T) {
	var cmds []string
	execCommand = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommand = exec.Command })

	params := map[string]any{
		"interface": "en0",
		"mode":      "auto",
	}
	result, err := executeOS(params)
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("snapshot missing from result")
	}
	found := false
	for _, c := range cmds {
		if strings.Contains(c, "networksetup") && strings.Contains(c, "setdhcp") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected networksetup -setdhcp call; got: %v", cmds)
	}
}

func TestExecuteOS_DarwinManualMode(t *testing.T) {
	var cmds []string
	execCommand = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "192.168.1.100")
	}
	t.Cleanup(func() { execCommand = exec.Command })

	params := map[string]any{
		"interface":   "en0",
		"mode":        "manual",
		"ip_address":  "10.0.0.50",
		"subnet_mask": "255.255.255.0",
		"gateway":     "10.0.0.1",
		"dns_servers": []any{"8.8.8.8"},
	}
	result, err := executeOS(params)
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	_ = result
	found := false
	for _, c := range cmds {
		if strings.Contains(c, "setmanual") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected networksetup -setmanual; got: %v", cmds)
	}
}

func TestRollbackOS_Darwin(t *testing.T) {
	snap := DarwinSnapshot{
		Interface:   "en0",
		ServiceName: "Wi-Fi",
		Mode:        "auto",
		IPv4:        "192.168.1.5",
		Subnet:      "255.255.255.0",
		Gateway:     "192.168.1.1",
		DNS:         []string{"8.8.8.8"},
	}
	b, _ := json.Marshal(snap)

	var cmds []string
	execCommand = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommand = exec.Command })

	result, err := rollbackOS(map[string]any{"snapshot": string(b)})
	if err != nil {
		t.Fatalf("rollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
