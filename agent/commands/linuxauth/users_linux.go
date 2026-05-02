//go:build linux

package linuxauth

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

func auditUsersOS(_ map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("audit_users_and_groups requires root privileges")
	}

	type userEntry struct {
		name  string
		uid   int
		home  string
		shell string
	}

	var users []userEntry
	if f, err := os.Open("/etc/passwd"); err == nil {
		defer f.Close()
		sc := bufio.NewScanner(f)
		for sc.Scan() {
			line := sc.Text()
			if strings.HasPrefix(line, "#") || line == "" {
				continue
			}
			parts := strings.Split(line, ":")
			if len(parts) < 7 {
				continue
			}
			uid, _ := strconv.Atoi(parts[2])
			users = append(users, userEntry{name: parts[0], uid: uid, home: parts[5], shell: parts[6]})
		}
	}

	emptyPass := map[string]bool{}
	if f, err := os.Open("/etc/shadow"); err == nil {
		defer f.Close()
		sc := bufio.NewScanner(f)
		for sc.Scan() {
			parts := strings.SplitN(sc.Text(), ":", 3)
			if len(parts) >= 2 && parts[1] == "" {
				emptyPass[parts[0]] = true
			}
		}
	}

	sudoMembers := []string{}
	if f, err := os.Open("/etc/group"); err == nil {
		defer f.Close()
		sc := bufio.NewScanner(f)
		for sc.Scan() {
			parts := strings.SplitN(sc.Text(), ":", 4)
			if len(parts) == 4 && (parts[0] == "sudo" || parts[0] == "wheel") && parts[3] != "" {
				sudoMembers = append(sudoMembers, strings.Split(parts[3], ",")...)
			}
		}
	}

	var findings []map[string]any
	for _, u := range users {
		if u.uid == 0 && u.name != "root" {
			findings = append(findings, map[string]any{"user": u.name, "tag": "uid0-non-root",
				"description": fmt.Sprintf("Account %q has UID 0 but is not root", u.name)})
		}
		if emptyPass[u.name] {
			findings = append(findings, map[string]any{"user": u.name, "tag": "empty-password",
				"description": fmt.Sprintf("Account %q has empty password", u.name)})
		}
		if u.uid > 0 && u.uid < 1000 && (strings.HasSuffix(u.shell, "/bash") || strings.HasSuffix(u.shell, "/sh")) {
			findings = append(findings, map[string]any{"user": u.name, "tag": "svc-interactive-shell",
				"description": fmt.Sprintf("Service account %q (uid=%d) has interactive shell %q", u.name, u.uid, u.shell)})
		}
		if u.uid >= 1000 {
			out, _ := exec.Command("chage", "-l", u.name).Output()
			if strings.Contains(string(out), "never") {
				findings = append(findings, map[string]any{"user": u.name, "tag": "user-no-expiry",
					"description": fmt.Sprintf("Account %q has no password expiry", u.name)})
			}
			if fi, err := os.Stat(u.home); err == nil && fi.Mode()&0002 != 0 {
				findings = append(findings, map[string]any{"user": u.name, "tag": "world-writable-home",
					"description": fmt.Sprintf("Home %q is world-writable", u.home)})
			}
		}
	}
	if len(sudoMembers) > 0 {
		findings = append(findings, map[string]any{"tag": "sudo-group-members",
			"description": fmt.Sprintf("sudo/wheel members for review: %v", sudoMembers),
			"members":     sudoMembers})
	}

	tags := []string{}
	if len(findings) > 0 {
		tags = append(tags, "users-audit-findings")
	}
	return map[string]any{
		"findings":   findings,
		"total":      len(findings),
		"tags":       tags,
		"audited_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
