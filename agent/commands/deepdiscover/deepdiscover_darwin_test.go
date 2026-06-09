//go:build darwin

package deepdiscover

import (
	"os/exec"
	"testing"
)

func TestExecuteOS_Darwin(t *testing.T) {
	execCommandDarwin = func(name string, args ...string) *exec.Cmd {
		switch name {
		case "launchctl":
			return exec.Command("printf", "PID\tStatus\tLabel\n100\t0\tcom.example.webserver\n")
		default:
			return exec.Command("echo", "")
		}
	}
	t.Cleanup(func() { execCommandDarwin = exec.Command })

	result, err := executeOS(map[string]any{})
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	if result.OS != "darwin" {
		t.Fatalf("expected OS=darwin, got %q", result.OS)
	}
}
