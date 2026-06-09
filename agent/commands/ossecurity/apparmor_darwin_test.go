//go:build darwin

package ossecurity

import (
	"os/exec"
	"strings"
	"testing"
)

func TestApparmorExecuteOS_DarwinEnforce(t *testing.T) {
	var cmds []string
	execCommandAppArmor = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "Added rule.")
	}
	t.Cleanup(func() { execCommandAppArmor = exec.Command })

	params := map[string]any{
		"profile_name":    "com.example.foo",
		"profile_content": `[{"sha256":"abc123","comment":"test"}]`,
		"mode":            "enforce",
	}
	result, err := apparmorExecuteOS(params)
	if err != nil {
		t.Fatalf("apparmorExecuteOS: %v", err)
	}
	if result["profile_name"] != "com.example.foo" {
		t.Fatalf("expected profile_name; got %v", result)
	}
	foundSanta := false
	for _, c := range cmds {
		if strings.Contains(c, "santactl") && strings.Contains(c, "--allow") {
			foundSanta = true
		}
	}
	if !foundSanta {
		t.Fatalf("expected santactl --allow; got: %v", cmds)
	}
}

func TestApparmorRollbackOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandAppArmor = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "Removed rule.")
	}
	t.Cleanup(func() { execCommandAppArmor = exec.Command })

	result, err := apparmorRollbackOS(map[string]any{
		"snapshot": map[string]any{
			"profile":        "com.example.foo",
			"rules_added":    []any{"abc123"},
			"team_ids_added": []any{},
		},
	})
	if err != nil {
		t.Fatalf("apparmorRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
