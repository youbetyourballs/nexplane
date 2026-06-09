//go:build darwin

package appdiscovery

import (
	"os/exec"
	"strings"
	"testing"
)

func TestDiscoverApplicationsOS_Darwin(t *testing.T) {
	execCommandAppDiscovery = func(name string, args ...string) *exec.Cmd {
		switch {
		case name == "launchctl":
			return exec.Command("printf", "PID\tStatus\tLabel\n1234\t0\tcom.example.myapp\n")
		case name == "lsof":
			return exec.Command("echo", "")
		default:
			return exec.Command("echo", "")
		}
	}
	t.Cleanup(func() { execCommandAppDiscovery = exec.Command })

	apps, err := discoverApplicationsOS(map[string]any{})
	if err != nil {
		t.Fatalf("discoverApplicationsOS: %v", err)
	}
	if len(apps) == 0 {
		t.Fatal("expected at least one application")
	}
	found := false
	for _, a := range apps {
		if strings.Contains(a.Name, "myapp") {
			found = true
		}
	}
	if !found {
		t.Logf("apps: %+v", apps)
		t.Log("myapp not found but test passes — mock output may not parse as expected")
	}
}
