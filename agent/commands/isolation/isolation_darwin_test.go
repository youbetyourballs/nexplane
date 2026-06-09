//go:build darwin

package isolation

import (
	"context"
	"os/exec"
	"strings"
	"testing"
)

func TestIsolateDarwin_CapturesAndAppliesRules(t *testing.T) {
	var cmds []string
	execCommandIsolation = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "pass in all\npass out all")
	}
	t.Cleanup(func() { execCommandIsolation = exec.Command })

	cfg := IsolationConfig{
		ManagementCIDR:  "10.0.0.0/8",
		ControlPlaneURL: "https://10.0.1.50",
	}
	state, err := isolateOS(context.Background(), cfg)
	if err != nil {
		t.Fatalf("isolateOS: %v", err)
	}
	if state.OS != "darwin" {
		t.Fatalf("expected OS=darwin, got %q", state.OS)
	}
	foundPfctl := false
	for _, c := range cmds {
		if strings.Contains(c, "pfctl") {
			foundPfctl = true
		}
	}
	if !foundPfctl {
		t.Fatalf("expected pfctl call; got: %v", cmds)
	}
}

func TestRestoreDarwin_RestoresRules(t *testing.T) {
	var cmds []string
	execCommandIsolation = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandIsolation = exec.Command })

	state := &PreIsolationState{
		OS:      "darwin",
		PFRules: "pass in all\npass out all\n",
	}
	if err := restoreOS(context.Background(), state); err != nil {
		t.Fatalf("restoreOS: %v", err)
	}
	foundRestore := false
	for _, c := range cmds {
		if strings.Contains(c, "pfctl") && strings.Contains(c, "-f") {
			foundRestore = true
		}
	}
	if !foundRestore {
		t.Fatalf("expected pfctl -f; got: %v", cmds)
	}
}
