package credrotation

import (
	"bufio"
	"context"
	"fmt"
	"os/exec"
	"strings"
)

// ReplaceCredentialInContent scans content line by line and replaces any
// occurrence of oldPassword with newPassword. Exported for testing.
func ReplaceCredentialInContent(content, username, newPassword string) string {
	var sb strings.Builder
	scanner := bufio.NewScanner(strings.NewReader(content))
	for scanner.Scan() {
		line := scanner.Text()
		// Match common env-file patterns: KEY=value or key = value
		// Replace the value portion when the line looks like a DB password entry
		if strings.Contains(strings.ToUpper(line), "PASSWORD") ||
			strings.Contains(strings.ToUpper(line), "PASS") ||
			strings.Contains(strings.ToUpper(line), "PWD") {
			// Replace everything after the first = sign
			if idx := strings.Index(line, "="); idx != -1 {
				line = line[:idx+1] + newPassword
			}
		}
		sb.WriteString(line + "\n")
	}
	return sb.String()
}

// ReplaceEnvVarInContent replaces the value of a named environment variable
// in a KEY=value formatted file. Exported for testing.
func ReplaceEnvVarInContent(content, envVarName, newValue string) string {
	var sb strings.Builder
	scanner := bufio.NewScanner(strings.NewReader(content))
	for scanner.Scan() {
		line := scanner.Text()
		prefix := envVarName + "="
		if strings.HasPrefix(line, prefix) {
			line = prefix + newValue
		}
		sb.WriteString(line + "\n")
	}
	return sb.String()
}

// sshKeyFingerprint computes the SHA256 fingerprint of a public key line
// by shelling out to ssh-keygen. Returns the fingerprint string (without
// the "SHA256:" prefix) or an error if the line is not a valid public key.
func sshKeyFingerprint(ctx context.Context, pubKeyLine string) (string, error) {
	if strings.TrimSpace(pubKeyLine) == "" || strings.HasPrefix(pubKeyLine, "#") {
		return "", fmt.Errorf("empty or comment line")
	}
	cmd := exec.CommandContext(ctx, "ssh-keygen", "-l", "-E", "sha256", "-f", "/dev/stdin")
	cmd.Stdin = strings.NewReader(pubKeyLine + "\n")
	out, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("ssh-keygen fingerprint: %w", err)
	}
	// Output format: "2048 SHA256:<fp> comment (RSA)"
	fields := strings.Fields(string(out))
	for _, f := range fields {
		if strings.HasPrefix(f, "SHA256:") {
			return strings.TrimPrefix(f, "SHA256:"), nil
		}
	}
	return "", fmt.Errorf("could not parse fingerprint from: %s", out)
}
