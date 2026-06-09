//go:build darwin

package crossplatform

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

var execCommandCrossplatformDarwin = exec.Command

func tlsExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	service, _ := params["service"].(string)
	certDir := fmt.Sprintf("/etc/nexplane/tls/%s", service)
	certPath := certDir + "/cert.pem"
	keyPath := certDir + "/key.pem"

	snapshot := map[string]any{}
	if data, err := os.ReadFile(certPath); err == nil {
		snapshot["cert"] = string(data)
	}
	if data, err := os.ReadFile(keyPath); err == nil {
		snapshot["key"] = string(data)
	}

	if action == "validate" {
		out, _ := execCommandCrossplatformDarwin("openssl", "x509", "-noout", "-subject", "-enddate", "-in", certPath).Output()
		return map[string]any{
			"action": "validate", "service": service, "cert_path": certPath,
			"openssl_output": strings.TrimSpace(string(out)),
			"checked_at":     time.Now().UTC().Format(time.RFC3339),
		}, nil
	}

	source, _ := params["source"].(string)
	if source == "manual" {
		certPEM, _ := params["cert_pem"].(string)
		keyPEM, _ := params["key_pem"].(string)
		if err := os.MkdirAll(certDir, 0750); err != nil {
			return nil, fmt.Errorf("creating cert dir: %w", err)
		}
		if err := os.WriteFile(certPath, []byte(certPEM), 0644); err != nil {
			return nil, fmt.Errorf("writing cert: %w", err)
		}
		if err := os.WriteFile(keyPath, []byte(keyPEM), 0600); err != nil {
			return nil, fmt.Errorf("writing key: %w", err)
		}
	}

	if service != "" {
		execCommandCrossplatformDarwin("launchctl", "kickstart", "-k", "system/"+service).Run()
	}

	return map[string]any{
		"service":    service,
		"cert_path":  certPath,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func tlsRollbackOS(params map[string]any) (map[string]any, error) {
	service, _ := params["service"].(string)
	snapshot, _ := params["snapshot"].(map[string]any)
	if snapshot == nil {
		return map[string]any{"rolled_back": false, "reason": "no snapshot"}, nil
	}
	certDir := fmt.Sprintf("/etc/nexplane/tls/%s", service)
	if certData, _ := snapshot["cert"].(string); certData != "" {
		os.WriteFile(certDir+"/cert.pem", []byte(certData), 0644) //nolint:errcheck
	}
	if keyData, _ := snapshot["key"].(string); keyData != "" {
		os.WriteFile(certDir+"/key.pem", []byte(keyData), 0600) //nolint:errcheck
	}
	if service != "" {
		execCommandCrossplatformDarwin("launchctl", "kickstart", "-k", "system/"+service).Run()
	}
	return map[string]any{"rolled_back": true}, nil
}

func dnsExecuteOS(params map[string]any) (map[string]any, error) {
	resolversRaw, _ := params["resolvers"].([]any)
	resolvers := make([]string, 0, len(resolversRaw))
	for _, r := range resolversRaw {
		if s, ok := r.(string); ok {
			resolvers = append(resolvers, s)
		}
	}
	if len(resolvers) == 0 {
		return nil, fmt.Errorf("resolvers is required")
	}

	svcOut, _ := execCommandCrossplatformDarwin("networksetup", "-listallnetworkservices").Output()
	service := detectActiveNetworkService(string(svcOut))

	snapOut, _ := execCommandCrossplatformDarwin("networksetup", "-getdnsservers", service).Output()
	snapshot := strings.TrimSpace(string(snapOut))

	args := append([]string{"-setdnsservers", service}, resolvers...)
	if out, err := execCommandCrossplatformDarwin("networksetup", args...).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("networksetup -setdnsservers: %s: %w", out, err)
	}

	return map[string]any{
		"service":    service,
		"resolvers":  resolvers,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func dnsRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	service, _ := params["service"].(string)
	if service == "" || snapshot == "" {
		return map[string]any{"rolled_back": false, "reason": "no snapshot or service"}, nil
	}
	if strings.Contains(snapshot, "There aren't") {
		execCommandCrossplatformDarwin("networksetup", "-setdnsservers", service, "Empty").Run()
	} else {
		resolvers := strings.Fields(snapshot)
		args := append([]string{"-setdnsservers", service}, resolvers...)
		execCommandCrossplatformDarwin("networksetup", args...).Run()
	}
	return map[string]any{"rolled_back": true}, nil
}

func inventoryExecuteOS(_ map[string]any) (map[string]any, error) {
	packages := []map[string]any{}

	if out, err := execCommandCrossplatformDarwin("brew", "list", "--versions").Output(); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			parts := strings.Fields(line)
			if len(parts) >= 2 {
				packages = append(packages, map[string]any{"name": parts[0], "version": parts[1], "source": "brew"})
			}
		}
	}

	if out, err := execCommandCrossplatformDarwin("port", "installed").Output(); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			line = strings.TrimSpace(line)
			if line == "" || strings.HasPrefix(line, "The following") {
				continue
			}
			parts := strings.Fields(line)
			if len(parts) >= 2 {
				packages = append(packages, map[string]any{
					"name": parts[0], "version": strings.TrimPrefix(parts[1], "@"), "source": "macports",
				})
			}
		}
	}

	if out, err := execCommandCrossplatformDarwin("pkgutil", "--pkgs").Output(); err == nil {
		for _, pkg := range strings.Split(string(out), "\n") {
			pkg = strings.TrimSpace(pkg)
			if pkg != "" {
				packages = append(packages, map[string]any{"name": pkg, "source": "pkgutil"})
			}
		}
	}

	return map[string]any{
		"packages":     packages,
		"collected_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func detectActiveNetworkService(listOutput string) string {
	for _, line := range strings.Split(listOutput, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "*") || strings.HasPrefix(line, "An asterisk") {
			continue
		}
		return line
	}
	return "Wi-Fi"
}
