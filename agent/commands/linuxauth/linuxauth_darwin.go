//go:build darwin

package linuxauth

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

var execCommandLinuxAuthDarwin = exec.Command
var darwinSSHConfigPath = "/etc/ssh/sshd_config"

var darwinSSHDefaults = map[string]string{
	"PermitRootLogin":        "no",
	"PasswordAuthentication": "no",
	"PubkeyAuthentication":   "yes",
	"ClientAliveInterval":    "300",
	"ClientAliveCountMax":    "3",
	"MaxAuthTries":           "4",
	"X11Forwarding":          "no",
	"PermitEmptyPasswords":   "no",
}

var darwinSSHParamMap = map[string]string{
	"permit_root_login":       "PermitRootLogin",
	"password_authentication": "PasswordAuthentication",
	"pubkey_authentication":   "PubkeyAuthentication",
	"client_alive_interval":   "ClientAliveInterval",
	"client_alive_count_max":  "ClientAliveCountMax",
	"max_auth_tries":          "MaxAuthTries",
	"x11_forwarding":          "X11Forwarding",
	"permit_empty_passwords":  "PermitEmptyPasswords",
	"port":                    "Port",
	"allow_users":             "AllowUsers",
	"allow_groups":            "AllowGroups",
}

func pamExecuteOS(params map[string]any) (map[string]any, error) {
	snapshot := map[string]string{}
	for _, svc := range []string{"login", "sudo", "su"} {
		data, _ := os.ReadFile("/etc/pam.d/" + svc)
		snapshot[svc] = string(data)
	}
	minLen, _ := params["min_length"].(float64)
	maxFailed, _ := params["max_failed_attempts"].(float64)
	if minLen > 0 {
		policyXML := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>minChars</key><integer>%d</integer></dict></plist>`, int(minLen))
		execCommandLinuxAuthDarwin("pwpolicy", "-setaccountpolicies", policyXML).Run()
	}
	if maxFailed > 0 {
		policyXML := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>maxFailedLoginAttempts</key><integer>%d</integer></dict></plist>`, int(maxFailed))
		execCommandLinuxAuthDarwin("pwpolicy", "-setaccountpolicies", policyXML).Run()
	}
	return map[string]any{"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339)}, nil
}

func pamRollbackOS(params map[string]any) (map[string]any, error) {
	if snapshot, ok := params["snapshot"].(map[string]any); ok {
		for svc, content := range snapshot {
			if s, ok := content.(string); ok && s != "" {
				os.WriteFile("/etc/pam.d/"+svc, []byte(s), 0644)
			}
		}
	}
	execCommandLinuxAuthDarwin("pwpolicy", "-clearaccountpolicies").Run()
	return map[string]any{"rolled_back": true}, nil
}

func sshExecuteOS(params map[string]any) (map[string]any, error) {
	existing, err := os.ReadFile(darwinSSHConfigPath)
	if err != nil {
		return nil, fmt.Errorf("reading sshd_config: %w", err)
	}
	snapshot := string(existing)
	settings := make(map[string]string)
	for k, v := range darwinSSHDefaults {
		settings[k] = v
	}
	for paramKey, sshKey := range darwinSSHParamMap {
		if val, ok := params[paramKey]; ok {
			settings[sshKey] = fmt.Sprintf("%v", val)
		}
	}
	newConfig := applySSHSettings(snapshot, settings)
	if err := os.WriteFile(darwinSSHConfigPath, []byte(newConfig), 0644); err != nil {
		return nil, fmt.Errorf("writing sshd_config: %w", err)
	}
	if out, err := execCommandLinuxAuthDarwin("sshd", "-t").CombinedOutput(); err != nil {
		os.WriteFile(darwinSSHConfigPath, existing, 0644)
		return nil, fmt.Errorf("sshd -t validation failed: %s: %w", out, err)
	}
	execCommandLinuxAuthDarwin("launchctl", "unload", "/System/Library/LaunchDaemons/ssh.plist").Run()
	execCommandLinuxAuthDarwin("launchctl", "load", "-w", "/System/Library/LaunchDaemons/ssh.plist").Run()
	return map[string]any{"snapshot": snapshot, "config_path": darwinSSHConfigPath, "applied_at": time.Now().UTC().Format(time.RFC3339)}, nil
}

func sshRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	if snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if err := os.WriteFile(darwinSSHConfigPath, []byte(snapshot), 0644); err != nil {
		return nil, fmt.Errorf("restoring sshd_config: %w", err)
	}
	execCommandLinuxAuthDarwin("launchctl", "unload", "/System/Library/LaunchDaemons/ssh.plist").Run()
	execCommandLinuxAuthDarwin("launchctl", "load", "-w", "/System/Library/LaunchDaemons/ssh.plist").Run()
	return map[string]any{"rolled_back": true}, nil
}

func applySSHSettings(config string, settings map[string]string) string {
	applied := make(map[string]bool)
	var lines []string
	for _, line := range strings.Split(config, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			lines = append(lines, line)
			continue
		}
		parts := strings.SplitN(trimmed, " ", 2)
		if len(parts) == 2 {
			key := parts[0]
			if val, ok := settings[key]; ok {
				lines = append(lines, key+" "+val)
				applied[key] = true
				continue
			}
		}
		lines = append(lines, line)
	}
	for k, v := range settings {
		if !applied[k] {
			lines = append(lines, k+" "+v)
		}
	}
	return strings.Join(lines, "\n")
}

func certsExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action == "" {
		action = "add"
	}
	certPath, _ := params["cert_path"].(string)
	certPEM, _ := params["cert_pem"].(string)
	tmpPath := ""
	if certPEM != "" {
		tmpPath = "/tmp/nexplane-cert-import.pem"
		if err := os.WriteFile(tmpPath, []byte(certPEM), 0600); err != nil {
			return nil, fmt.Errorf("writing cert: %w", err)
		}
		defer os.Remove(tmpPath)
		certPath = tmpPath
	}
	if certPath == "" {
		return nil, fmt.Errorf("cert_path or cert_pem is required")
	}
	switch action {
	case "add":
		out, err := execCommandLinuxAuthDarwin("security", "add-trusted-cert", "-d", "-r", "trustRoot", "-k", "/Library/Keychains/System.keychain", certPath).CombinedOutput()
		if err != nil {
			return nil, fmt.Errorf("security add-trusted-cert: %s: %w", out, err)
		}
	case "remove":
		out, err := execCommandLinuxAuthDarwin("security", "delete-certificate", "-c", certPath, "/Library/Keychains/System.keychain").CombinedOutput()
		if err != nil {
			return nil, fmt.Errorf("security delete-certificate: %s: %w", out, err)
		}
	default:
		return nil, fmt.Errorf("unsupported action %q", action)
	}
	return map[string]any{"action": action, "cert_path": certPath, "applied_at": time.Now().UTC().Format(time.RFC3339)}, nil
}

func certsRollbackOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	reverseAction := "remove"
	if action == "remove" {
		reverseAction = "add"
	}
	return certsExecuteOS(map[string]any{
		"action":    reverseAction,
		"cert_path": params["cert_path"],
		"cert_pem":  params["cert_pem"],
	})
}

func ntpExecuteOS(params map[string]any) (map[string]any, error) {
	snapOut, _ := execCommandLinuxAuthDarwin("systemsetup", "-getnetworktimeserver").Output()
	snapshot := strings.TrimSpace(string(snapOut))
	execCommandLinuxAuthDarwin("systemsetup", "-setusingnetworktime", "on").Run()
	serversRaw, _ := params["servers"].([]any)
	if len(serversRaw) > 0 {
		if primary, ok := serversRaw[0].(string); ok && primary != "" {
			execCommandLinuxAuthDarwin("systemsetup", "-setnetworktimeserver", primary).Run()
		}
	}
	return map[string]any{"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339)}, nil
}

func ntpRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	server := snapshot
	if idx := strings.Index(snapshot, ": "); idx != -1 {
		server = strings.TrimSpace(snapshot[idx+2:])
	}
	if server != "" {
		execCommandLinuxAuthDarwin("systemsetup", "-setnetworktimeserver", server).Run()
	}
	return map[string]any{"rolled_back": true}, nil
}

func auditUsersOS(_ map[string]any) (map[string]any, error) {
	out, err := execCommandLinuxAuthDarwin("dscl", ".", "-list", "/Users", "UniqueID").Output()
	if err != nil {
		return nil, fmt.Errorf("dscl list users: %w", err)
	}
	var users []map[string]any
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) < 2 {
			continue
		}
		username := parts[0]
		shellOut, _ := execCommandLinuxAuthDarwin("dscl", ".", "-read", "/Users/"+username, "UserShell").Output()
		shell := ""
		for _, l := range strings.Split(string(shellOut), "\n") {
			if strings.HasPrefix(strings.TrimSpace(l), "UserShell:") {
				shell = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(l), "UserShell:"))
			}
		}
		users = append(users, map[string]any{"username": username, "uid": parts[1], "shell": shell})
	}
	return map[string]any{"users": users, "collected_at": time.Now().UTC().Format(time.RFC3339)}, nil
}

func auditPrivescOS(_ map[string]any) (map[string]any, error) {
	sudoersOut, _ := execCommandLinuxAuthDarwin("sudo", "-l").Output()
	setuidOut, _ := execCommandLinuxAuthDarwin("find", "/usr/bin", "/usr/local/bin", "/bin", "-perm", "-4000").Output()
	return map[string]any{
		"sudo_rules":      string(sudoersOut),
		"setuid_binaries": strings.Split(strings.TrimSpace(string(setuidOut)), "\n"),
		"collected_at":    time.Now().UTC().Format(time.RFC3339),
	}, nil
}
