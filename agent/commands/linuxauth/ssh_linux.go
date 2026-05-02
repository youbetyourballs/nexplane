//go:build linux

package linuxauth

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

var sshDefaults = map[string]string{
	"PermitRootLogin":        "no",
	"PasswordAuthentication": "no",
	"PubkeyAuthentication":   "yes",
	"Ciphers":                "chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com",
	"MACs":                   "hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com",
	"KexAlgorithms":          "curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group16-sha512",
	"ClientAliveInterval":    "300",
	"ClientAliveCountMax":    "3",
	"MaxAuthTries":           "4",
	"LoginGraceTime":         "60",
	"X11Forwarding":          "no",
	"PermitEmptyPasswords":   "no",
}

var sshParamMap = map[string]string{
	"permit_root_login": "PermitRootLogin", "password_authentication": "PasswordAuthentication",
	"pubkey_authentication": "PubkeyAuthentication", "allowed_ciphers": "Ciphers",
	"allowed_macs": "MACs", "allowed_kex_algorithms": "KexAlgorithms",
	"client_alive_interval": "ClientAliveInterval", "client_alive_count_max": "ClientAliveCountMax",
	"max_auth_tries": "MaxAuthTries", "login_grace_time": "LoginGraceTime",
	"x11_forwarding": "X11Forwarding", "permit_empty_passwords": "PermitEmptyPasswords",
	"port": "Port", "allow_users": "AllowUsers", "allow_groups": "AllowGroups",
}

func sshExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("harden_ssh requires root privileges")
	}
	if _, err := exec.LookPath("sshd"); err != nil {
		return nil, fmt.Errorf("sshd not found: %w", err)
	}

	settings := make(map[string]string)
	for k, v := range sshDefaults {
		settings[k] = v
	}
	if userSettings, ok := params["settings"].(map[string]any); ok {
		for k, v := range userSettings {
			key := k
			if mapped, ok := sshParamMap[k]; ok {
				key = mapped
			}
			settings[key] = fmt.Sprintf("%v", v)
		}
	}

	snapshot := map[string]any{}
	if data, err := os.ReadFile("/etc/ssh/sshd_config"); err == nil {
		snapshot["/etc/ssh/sshd_config"] = string(data)
	}
	dropInDir := "/etc/ssh/sshd_config.d"
	if files, err := os.ReadDir(dropInDir); err == nil {
		for _, f := range files {
			p := filepath.Join(dropInDir, f.Name())
			if data, err := os.ReadFile(p); err == nil {
				snapshot[p] = string(data)
			}
		}
	}

	if err := os.MkdirAll(dropInDir, 0755); err != nil {
		return nil, fmt.Errorf("creating sshd_config.d: %w", err)
	}

	dropInPath := "/etc/ssh/sshd_config.d/99-nexplane-hardening.conf"
	var sb strings.Builder
	sb.WriteString("# Nexplane SSH hardening — do not edit manually\n\n")
	for k, v := range settings {
		if v != "" {
			fmt.Fprintf(&sb, "%s %s\n", k, v)
		}
	}
	if err := os.WriteFile(dropInPath, []byte(sb.String()), 0600); err != nil {
		return nil, fmt.Errorf("writing drop-in: %w", err)
	}

	if out, err := exec.Command("sshd", "-t").CombinedOutput(); err != nil {
		os.Remove(dropInPath)
		return nil, fmt.Errorf("sshd -t failed: %s", out)
	}

	if err := exec.Command("systemctl", "reload", "sshd").Run(); err != nil {
		exec.Command("systemctl", "reload", "ssh").Run() //nolint:errcheck
	}

	return map[string]any{
		"settings_applied":     settings,
		"drop_in_path":         dropInPath,
		"sshd_config_snapshot": snapshot,
		"applied_at":           time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func sshRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["sshd_config_snapshot"].(map[string]any)
	if !ok || snapshot == nil {
		return nil, fmt.Errorf("sshd_config_snapshot is required for rollback")
	}
	os.Remove("/etc/ssh/sshd_config.d/99-nexplane-hardening.conf")
	for path, content := range snapshot {
		os.WriteFile(path, []byte(content.(string)), 0600) //nolint:errcheck
	}
	if err := exec.Command("systemctl", "reload", "sshd").Run(); err != nil {
		exec.Command("systemctl", "reload", "ssh").Run() //nolint:errcheck
	}
	return map[string]any{"rolled_back": true}, nil
}
