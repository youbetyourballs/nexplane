//go:build darwin

package reboot

import (
	"os/exec"
	"strings"
	"testing"
)

func TestExecuteOS_DarwinDryRun(t *testing.T) {
	result, err := executeOS(map[string]any{"dry_run": true})
	if err != nil {
		t.Fatalf("dry run failed: %v", err)
	}
	if result["dry_run"] != true {
		t.Fatal("expected dry_run=true")
	}
}

func TestExecuteOS_DarwinSchedules(t *testing.T) {
	var called []string
	execCommandDarwin = func(name string, args ...string) *exec.Cmd {
		called = append(called, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandDarwin = exec.Command })

	result, err := executeOS(map[string]any{"graceful_delay_seconds": float64(120)})
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	_ = result
	found := false
	for _, c := range called {
		if strings.Contains(c, "shutdown") && strings.Contains(c, "-r") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected shutdown -r; got: %v", called)
	}
}

func TestVerifyPostRebootOS_Darwin(t *testing.T) {
	execCommandDarwin = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "com.apple.Finder")
	}
	t.Cleanup(func() { execCommandDarwin = exec.Command })

	result, err := verifyPostRebootOS(map[string]any{
		"verify_services": []any{"com.apple.Finder"},
	})
	if err != nil {
		t.Fatalf("verifyPostRebootOS: %v", err)
	}
	results, _ := result["service_results"].(map[string]string)
	if results["com.apple.Finder"] == "" {
		t.Fatal("expected service result")
	}
}
