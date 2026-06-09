//go:build darwin

package configsyslog

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestExecuteOS_DarwinWritesForwardLine(t *testing.T) {
	tmpFile := t.TempDir() + "/syslog.conf"
	origPath := darwinSyslogConf
	darwinSyslogConf = tmpFile
	t.Cleanup(func() { darwinSyslogConf = origPath })

	execCommandSyslogDarwin = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandSyslogDarwin = exec.Command })

	result, err := executeOS(map[string]any{
		"destination_host": "10.0.0.5",
		"destination_port": float64(514),
		"protocol":         "udp",
		"facility":         "*.*",
	})
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	if result["config_path"] == nil {
		t.Fatal("expected config_path")
	}
	data, _ := os.ReadFile(tmpFile)
	if !strings.Contains(string(data), "10.0.0.5") {
		t.Fatalf("expected forward line with host; got: %s", data)
	}
}

func TestRollbackOS_DarwinRestores(t *testing.T) {
	tmpFile := t.TempDir() + "/syslog.conf"
	origPath := darwinSyslogConf
	darwinSyslogConf = tmpFile
	t.Cleanup(func() { darwinSyslogConf = origPath })

	execCommandSyslogDarwin = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandSyslogDarwin = exec.Command })

	result, err := rollbackOS(map[string]any{"snapshot": "# original\n"})
	if err != nil {
		t.Fatalf("rollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
