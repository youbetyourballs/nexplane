//go:build linux

package linuxharden

import (
	"bufio"
	"crypto/tls"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func authorizedKeysAuditExecute(params map[string]any) (map[string]any, error) {
	var entries []map[string]any
	homes, _ := filepath.Glob("/home/*")
	homes = append(homes, "/root")
	for _, home := range homes {
		akPath := filepath.Join(home, ".ssh", "authorized_keys")
		data, err := os.ReadFile(akPath)
		if err != nil {
			continue
		}
		user := filepath.Base(home)
		scanner := bufio.NewScanner(strings.NewReader(string(data)))
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			parts := strings.Fields(line)
			comment := ""
			if len(parts) >= 3 {
				comment = parts[2]
			}
			keyType := ""
			if len(parts) >= 1 {
				keyType = parts[0]
			}
			entries = append(entries, map[string]any{
				"user":     user,
				"path":     akPath,
				"key_type": keyType,
				"comment":  comment,
			})
		}
	}
	return map[string]any{
		"action":     "authorized_keys_audit",
		"key_count":  len(entries),
		"keys":       entries,
		"audited_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func authorizedKeysAuditRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "authorized_keys_audit_rollback", "status": "read_only"}, nil
}

func sudoersAuditExecute(_ map[string]any) (map[string]any, error) {
	out, err := exec.Command("grep", "-rh", `NOPASSWD\|ALL=(ALL)`, "/etc/sudoers", "/etc/sudoers.d/").Output()
	if err != nil && len(out) == 0 {
		return map[string]any{"action": "sudoers_audit", "error": err.Error()}, nil
	}
	var findings []string
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if line != "" && !strings.HasPrefix(line, "#") {
			findings = append(findings, line)
		}
	}
	return map[string]any{
		"action":        "sudoers_audit",
		"finding_count": len(findings),
		"findings":      findings,
		"audited_at":    time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func sudoersAuditRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "sudoers_audit_rollback", "status": "read_only"}, nil
}

func suidScanExecute(_ map[string]any) (map[string]any, error) {
	out, err := exec.Command("find", "/", "-perm", "/4000", "-o", "-perm", "/2000",
		"-not", "-path", "*/proc/*", "-not", "-path", "*/sys/*").Output()
	if err != nil && len(out) == 0 {
		return nil, fmt.Errorf("suid scan failed: %w", err)
	}
	var files []string
	for _, f := range strings.Split(string(out), "\n") {
		f = strings.TrimSpace(f)
		if f != "" {
			files = append(files, f)
		}
	}
	return map[string]any{
		"action":     "suid_scan",
		"file_count": len(files),
		"files":      files,
		"scanned_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func suidScanRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "suid_scan_rollback", "status": "read_only"}, nil
}

func sslCertInspectExecute(_ map[string]any) (map[string]any, error) {
	out, _ := exec.Command("ss", "-tlnp").Output()
	var certs []map[string]any
	for _, line := range strings.Split(string(out), "\n") {
		if !strings.Contains(line, "LISTEN") {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) < 5 {
			continue
		}
		addr := parts[4]
		if idx := strings.LastIndex(addr, ":"); idx >= 0 {
			port := addr[idx+1:]
			host := "127.0.0.1"
			conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, port), 2*time.Second)
			if err != nil {
				continue
			}
			tlsConn := tls.Client(conn, &tls.Config{InsecureSkipVerify: true, ServerName: host})
			if err := tlsConn.Handshake(); err != nil {
				conn.Close()
				continue
			}
			peerCerts := tlsConn.ConnectionState().PeerCertificates
			tlsConn.Close()
			if len(peerCerts) > 0 {
				cert := peerCerts[0]
				daysLeft := int(time.Until(cert.NotAfter).Hours() / 24)
				certs = append(certs, map[string]any{
					"port":      port,
					"subject":   cert.Subject.CommonName,
					"issuer":    cert.Issuer.CommonName,
					"expires":   cert.NotAfter.Format(time.RFC3339),
					"days_left": daysLeft,
					"expired":   daysLeft < 0,
					"critical":  daysLeft <= 7,
					"warning":   daysLeft <= 30,
				})
			}
		}
	}
	return map[string]any{
		"action":       "ssl_cert_inspect",
		"cert_count":   len(certs),
		"certificates": certs,
		"inspected_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func sslCertInspectRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "ssl_cert_inspect_rollback", "status": "read_only"}, nil
}
