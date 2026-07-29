package authorizedkeys

import (
	"fmt"
	"os"
	"strings"
)

func _homeDir(user string) (string, error) {
	data, err := os.ReadFile("/etc/passwd")
	if err != nil {
		return "", fmt.Errorf("cannot read /etc/passwd: %w", err)
	}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Split(line, ":")
		if len(fields) >= 7 && fields[0] == user {
			return fields[5], nil
		}
	}
	return "", fmt.Errorf("user %q not found in /etc/passwd", user)
}

func AddAuthorizedKey(params map[string]any) (map[string]any, error) {
	pubKey, _ := params["public_key"].(string)
	user, _ := params["user"].(string)
	if user == "" {
		user = "root"
	}
	if strings.TrimSpace(pubKey) == "" {
		return nil, fmt.Errorf("add_authorized_key: public_key is required")
	}
	homeDir, err := _homeDir(user)
	if err != nil {
		return nil, err
	}
	sshDir := homeDir + "/.ssh"
	authFile := sshDir + "/authorized_keys"

	if err := os.MkdirAll(sshDir, 0700); err != nil {
		return nil, fmt.Errorf("add_authorized_key: mkdir .ssh: %w", err)
	}
	existing, _ := os.ReadFile(authFile)
	keyLine := strings.TrimSpace(pubKey)
	for _, line := range strings.Split(string(existing), "\n") {
		if strings.TrimSpace(line) == keyLine {
			return map[string]any{"added": false, "user": user, "reason": "already present"}, nil
		}
	}
	f, err := os.OpenFile(authFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		return nil, fmt.Errorf("add_authorized_key: open authorized_keys: %w", err)
	}
	defer f.Close()
	if _, err := fmt.Fprintln(f, keyLine); err != nil {
		return nil, fmt.Errorf("add_authorized_key: write: %w", err)
	}
	return map[string]any{"added": true, "user": user}, nil
}

func RemoveAuthorizedKey(params map[string]any) (map[string]any, error) {
	pubKey, _ := params["public_key"].(string)
	user, _ := params["user"].(string)
	if user == "" {
		user = "root"
	}
	if strings.TrimSpace(pubKey) == "" {
		return nil, fmt.Errorf("remove_authorized_key: public_key is required")
	}
	homeDir, err := _homeDir(user)
	if err != nil {
		return nil, err
	}
	authFile := homeDir + "/.ssh/authorized_keys"
	existing, err := os.ReadFile(authFile)
	if err != nil {
		return map[string]any{"removed": false, "user": user, "reason": "file not found"}, nil
	}
	keyLine := strings.TrimSpace(pubKey)
	var kept []string
	removed := false
	for _, line := range strings.Split(string(existing), "\n") {
		if strings.TrimSpace(line) == keyLine {
			removed = true
			continue
		}
		kept = append(kept, line)
	}
	if err := os.WriteFile(authFile, []byte(strings.Join(kept, "\n")), 0600); err != nil {
		return nil, fmt.Errorf("remove_authorized_key: write: %w", err)
	}
	return map[string]any{"removed": removed, "user": user}, nil
}
