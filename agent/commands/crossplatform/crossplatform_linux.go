//go:build linux

package crossplatform

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func tlsExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("manage_tls_certificates requires root privileges")
	}
	action, _ := params["action"].(string)
	service, _ := params["service"].(string)
	source, _ := params["source"].(string)

	certPath, keyPath := serviceCertPaths(service, params)

	snapshot := map[string]any{}
	if data, err := os.ReadFile(certPath); err == nil {
		snapshot["cert"] = string(data)
	}
	if data, err := os.ReadFile(keyPath); err == nil {
		snapshot["key"] = string(data)
	}

	if action == "validate" {
		out, _ := exec.Command("openssl", "x509", "-noout", "-subject", "-enddate", "-in", certPath).Output()
		return map[string]any{
			"action": "validate", "service": service, "cert_path": certPath,
			"openssl_output": strings.TrimSpace(string(out)),
			"checked_at":     time.Now().UTC().Format(time.RFC3339),
		}, nil
	}

	if source == "acme" {
		domain, _ := params["domain"].(string)
		email, _ := params["acme_email"].(string)
		args := []string{"certonly", "-d", domain, "--agree-tos", "-n", "--webroot", "-w", "/var/www/html"}
		if email != "" {
			args = append(args, "--email", email)
		}
		if out, err := exec.Command("certbot", args...).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("certbot: %s: %w", out, err)
		}
		certPath = fmt.Sprintf("/etc/letsencrypt/live/%s/fullchain.pem", domain)
		keyPath = fmt.Sprintf("/etc/letsencrypt/live/%s/privkey.pem", domain)
	} else if source == "manual" {
		certPEM, _ := params["cert_pem"].(string)
		keyPEM, _ := params["key_pem"].(string)
		if err := os.MkdirAll(serviceCertDir(service), 0750); err != nil {
			return nil, fmt.Errorf("creating cert dir: %w", err)
		}
		if err := os.WriteFile(certPath, []byte(certPEM), 0644); err != nil {
			return nil, fmt.Errorf("writing cert: %w", err)
		}
		if err := os.WriteFile(keyPath, []byte(keyPEM), 0600); err != nil {
			return nil, fmt.Errorf("writing key: %w", err)
		}
	}

	reloadServiceLinux(service)

	return map[string]any{
		"action": action, "service": service, "source": source,
		"cert_path": certPath, "key_path": keyPath,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func tlsRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	certPath, _ := params["cert_path"].(string)
	keyPath, _ := params["key_path"].(string)
	if certContent, ok := snapshot["cert"].(string); ok && certPath != "" {
		os.WriteFile(certPath, []byte(certContent), 0644) //nolint:errcheck
	}
	if keyContent, ok := snapshot["key"].(string); ok && keyPath != "" {
		os.WriteFile(keyPath, []byte(keyContent), 0600) //nolint:errcheck
	}
	service, _ := params["service"].(string)
	reloadServiceLinux(service)
	return map[string]any{"rolled_back": true}, nil
}

func serviceCertPaths(service string, params map[string]any) (certPath, keyPath string) {
	switch service {
	case "nginx":
		return "/etc/nginx/ssl/server.crt", "/etc/nginx/ssl/server.key"
	case "apache":
		return "/etc/ssl/certs/server.crt", "/etc/ssl/private/server.key"
	default:
		cp, _ := params["cert_path"].(string)
		kp, _ := params["key_path"].(string)
		if cp == "" {
			cp = "/etc/nexplane/tls/server.crt"
		}
		if kp == "" {
			kp = "/etc/nexplane/tls/server.key"
		}
		return cp, kp
	}
}

func serviceCertDir(service string) string {
	switch service {
	case "nginx":
		return "/etc/nginx/ssl"
	case "apache":
		return "/etc/ssl/certs"
	default:
		return "/etc/nexplane/tls"
	}
}

func reloadServiceLinux(service string) {
	switch service {
	case "nginx":
		exec.Command("systemctl", "reload", "nginx").Run() //nolint:errcheck
	case "apache":
		exec.Command("systemctl", "reload", "apache2").Run() //nolint:errcheck
	}
}

func dnsExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_dns_resolver requires root privileges")
	}
	resolversRaw, _ := params["resolvers"].([]any)
	resolvers := make([]string, 0, len(resolversRaw))
	for _, r := range resolversRaw {
		if s, ok := r.(string); ok {
			resolvers = append(resolvers, s)
		}
	}
	mode, _ := params["mode"].(string)
	if mode == "" {
		mode = "plain"
	}

	daemon := "resolv.conf"
	if _, err := os.Stat("/etc/systemd/resolved.conf"); err == nil {
		daemon = "systemd-resolved"
	}

	snapshot := map[string]any{}
	if data, err := os.ReadFile("/etc/resolv.conf"); err == nil {
		snapshot["/etc/resolv.conf"] = string(data)
	}
	if data, err := os.ReadFile("/etc/systemd/resolved.conf"); err == nil {
		snapshot["/etc/systemd/resolved.conf"] = string(data)
	}

	if daemon == "systemd-resolved" {
		data, _ := os.ReadFile("/etc/systemd/resolved.conf")
		content := string(data)
		dnsLine := "DNS=" + strings.Join(resolvers, " ")
		var lines []string
		replaced := false
		for _, l := range strings.Split(content, "\n") {
			if strings.HasPrefix(l, "DNS=") {
				lines = append(lines, dnsLine)
				replaced = true
			} else {
				lines = append(lines, l)
			}
		}
		if !replaced {
			lines = append(lines, dnsLine)
		}
		if mode == "doh" || mode == "dot" {
			lines = append(lines, "DNSOverTLS=yes")
		}
		if err := os.WriteFile("/etc/systemd/resolved.conf", []byte(strings.Join(lines, "\n")), 0644); err != nil {
			return nil, fmt.Errorf("writing resolved.conf: %w", err)
		}
		exec.Command("systemctl", "restart", "systemd-resolved").Run() //nolint:errcheck
	} else {
		var content strings.Builder
		for _, r := range resolvers {
			fmt.Fprintf(&content, "nameserver %s\n", r)
		}
		if err := os.WriteFile("/etc/resolv.conf", []byte(content.String()), 0644); err != nil {
			return nil, fmt.Errorf("writing resolv.conf: %w", err)
		}
	}

	return map[string]any{
		"mode": mode, "resolvers": resolvers, "daemon": daemon,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func dnsRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	for path, content := range snapshot {
		if s, ok := content.(string); ok {
			os.WriteFile(path, []byte(s), 0644) //nolint:errcheck
		}
	}
	exec.Command("systemctl", "restart", "systemd-resolved").Run() //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}

func inventoryExecuteOS(_ map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("audit_software_inventory requires root privileges")
	}
	packages := []map[string]any{}
	if out, err := exec.Command("dpkg-query", "-W", "-f=${Package}\t${Version}\t${Status}\n").Output(); err == nil {
		for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
			parts := strings.Split(line, "\t")
			if len(parts) >= 2 {
				packages = append(packages, map[string]any{"name": parts[0], "version": parts[1], "source": "dpkg"})
			}
		}
	}
	if len(packages) == 0 {
		if out, err := exec.Command("rpm", "-qa", "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n").Output(); err == nil {
			for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
				parts := strings.Split(line, "\t")
				if len(parts) >= 2 {
					packages = append(packages, map[string]any{"name": parts[0], "version": parts[1], "source": "rpm"})
				}
			}
		}
	}
	return map[string]any{
		"packages": packages, "total": len(packages),
		"platform": "linux", "audited_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
