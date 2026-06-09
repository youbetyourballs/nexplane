//go:build darwin

package fleet

import (
	"context"
	"os/exec"
	"strings"
	"testing"
)

func TestRestartServiceOS_DarwinLaunchctl(t *testing.T) {
	var cmds []string
	execCommandFleetDarwin = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandFleetDarwin = exec.Command })

	result := restartServiceOS(context.Background(), "nginx")
	if result["running"] != true {
		t.Fatalf("expected running=true; got %v", result)
	}
	found := false
	for _, c := range cmds {
		if strings.Contains(c, "launchctl") || strings.Contains(c, "brew") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected launchctl or brew; got: %v", cmds)
	}
}

func TestPushConfigFileOS_Darwin(t *testing.T) {
	tmpDir := t.TempDir()
	result := pushConfigFileOS(map[string]any{
		"file_path":    tmpDir + "/test.conf",
		"file_content": "key=value",
	})
	if result["error"] != nil {
		t.Fatalf("pushConfigFileOS error: %v", result["error"])
	}
}
