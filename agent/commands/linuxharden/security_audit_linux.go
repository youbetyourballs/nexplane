//go:build linux

package linuxharden

import (
	"crypto/tls"
	"net"
	"os/exec"
	"strings"
	"time"
)

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
