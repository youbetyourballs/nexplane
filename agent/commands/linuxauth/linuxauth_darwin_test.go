//go:build darwin

package linuxauth

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestSSHExecuteOS_Darwin(t *testing.T) {
	tmpFile := t.TempDir() + "/sshd_config"
	os.WriteFile(tmpFile, []byte("# sshd config\nPermitRootLogin yes\n"), 0644)
	origPath := darwinSSHConfigPath
	darwinSSHConfigPath = tmpFile
	t.Cleanup(func() { darwinSSHConfigPath = origPath })

	execCommandLinuxAuthDarwin = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandLinuxAuthDarwin = exec.Command })

	result, err := sshExecuteOS(map[string]any{"permit_root_login": "no"})
	if err != nil {
		t.Fatalf("sshExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	data, _ := os.ReadFile(tmpFile)
	if !strings.Contains(string(data), "PermitRootLogin no") {
		t.Fatalf("expected PermitRootLogin no; got: %s", data)
	}
}

func TestSSHRollbackOS_Darwin(t *testing.T) {
	tmpFile := t.TempDir() + "/sshd_config"
	origPath := darwinSSHConfigPath
	darwinSSHConfigPath = tmpFile
	t.Cleanup(func() { darwinSSHConfigPath = origPath })

	execCommandLinuxAuthDarwin = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandLinuxAuthDarwin = exec.Command })

	result, err := sshRollbackOS(map[string]any{"snapshot": "# original config\nPermitRootLogin yes\n"})
	if err != nil {
		t.Fatalf("sshRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
	data, _ := os.ReadFile(tmpFile)
	if !strings.Contains(string(data), "PermitRootLogin yes") {
		t.Fatal("expected original config restored")
	}
}

func TestNTPExecuteOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandLinuxAuthDarwin = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "Network Time Server: time.apple.com")
	}
	t.Cleanup(func() { execCommandLinuxAuthDarwin = exec.Command })

	result, err := ntpExecuteOS(map[string]any{"servers": []any{"time.cloudflare.com"}})
	if err != nil {
		t.Fatalf("ntpExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	foundSet := false
	for _, c := range cmds {
		if strings.Contains(c, "systemsetup") && strings.Contains(c, "setnetworktimeserver") {
			foundSet = true
		}
	}
	if !foundSet {
		t.Fatalf("expected systemsetup -setnetworktimeserver; got: %v", cmds)
	}
}

func TestAuditUsersOS_Darwin(t *testing.T) {
	execCommandLinuxAuthDarwin = func(name string, args ...string) *exec.Cmd {
		if name == "dscl" && len(args) >= 2 && args[1] == "-list" {
			return exec.Command("printf", "root 0\\nadmin 501\\n")
		}
		return exec.Command("echo", "UserShell: /bin/zsh")
	}
	t.Cleanup(func() { execCommandLinuxAuthDarwin = exec.Command })

	result, err := auditUsersOS(map[string]any{})
	if err != nil {
		t.Fatalf("auditUsersOS: %v", err)
	}
	users, _ := result["users"].([]map[string]any)
	if len(users) == 0 {
		t.Fatal("expected users list")
	}
}
