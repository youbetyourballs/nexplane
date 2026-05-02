//go:build linux

package linuxauth

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func distroInstallPath(certName string) (string, []string) {
	if _, err := os.Stat("/etc/debian_version"); err == nil {
		return fmt.Sprintf("/usr/local/share/ca-certificates/%s.crt", certName),
			[]string{"update-ca-certificates"}
	}
	return fmt.Sprintf("/etc/pki/ca-trust/source/anchors/%s.crt", certName),
		[]string{"update-ca-trust", "extract"}
}

func certsExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("manage_ca_certificates requires root privileges")
	}
	action, _ := params["action"].(string)
	certName, _ := params["cert_name"].(string)
	certPEM, _ := params["certificate"].(string)

	installPath, updateCmd := distroInstallPath(certName)

	snapshot := ""
	if data, err := os.ReadFile(installPath); err == nil {
		snapshot = string(data)
	}

	var certSubject, certExpiry string

	if action == "install" {
		tmp, err := os.CreateTemp("", "nexplane-cert-*.pem")
		if err != nil {
			return nil, fmt.Errorf("creating temp file: %w", err)
		}
		defer os.Remove(tmp.Name())
		if _, err := tmp.WriteString(certPEM); err != nil {
			return nil, fmt.Errorf("writing temp cert: %w", err)
		}
		tmp.Close()
		out, err := exec.Command("openssl", "x509", "-noout", "-subject", "-enddate", "-in", tmp.Name()).Output()
		if err != nil {
			return nil, fmt.Errorf("invalid PEM certificate: %w", err)
		}
		for _, line := range strings.Split(string(out), "\n") {
			if strings.HasPrefix(line, "subject=") {
				certSubject = strings.TrimPrefix(line, "subject=")
			}
			if strings.HasPrefix(line, "notAfter=") {
				certExpiry = strings.TrimPrefix(line, "notAfter=")
			}
		}
		if err := os.WriteFile(installPath, []byte(certPEM), 0644); err != nil {
			return nil, fmt.Errorf("writing cert: %w", err)
		}
	} else {
		if _, err := os.Stat(installPath); err != nil {
			return nil, fmt.Errorf("cert not found: %s", installPath)
		}
		if err := os.Remove(installPath); err != nil {
			return nil, fmt.Errorf("removing cert: %w", err)
		}
	}

	cmd := exec.Command(updateCmd[0], updateCmd[1:]...)
	if out, err := cmd.CombinedOutput(); err != nil {
		return nil, fmt.Errorf("trust store update failed: %s: %w", out, err)
	}

	return map[string]any{
		"action": action, "cert_path": installPath, "cert_name": certName,
		"cert_subject": certSubject, "cert_expiry": certExpiry,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func certsRollbackOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	certName, _ := params["cert_name"].(string)
	snapshot, _ := params["snapshot"].(string)

	installPath, updateCmd := distroInstallPath(certName)

	if action == "install" {
		os.Remove(installPath)
	} else {
		if snapshot == "" {
			return nil, fmt.Errorf("snapshot is required to restore removed cert")
		}
		if err := os.WriteFile(installPath, []byte(snapshot), 0644); err != nil {
			return nil, fmt.Errorf("restoring cert: %w", err)
		}
	}

	cmd := exec.Command(updateCmd[0], updateCmd[1:]...)
	if out, err := cmd.CombinedOutput(); err != nil {
		return nil, fmt.Errorf("trust store update failed: %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true, "cert_path": installPath}, nil
}
