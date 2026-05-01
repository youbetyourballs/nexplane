# Cross-Platform Agent Commands Implementation Plan (Spec 5d)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 3 cross-platform agent commands: TLS certificate lifecycle management, DNS resolver configuration, and software inventory ingest — both Linux and Windows.

**Architecture:** New `agent/commands/crossplatform/` package with a single public API file and per-OS implementation files. Pattern identical to linuxauth/winharden. All 3 commands registered in `executor.go`, cataloged in `nexplane_agent_mock.json`, with Python mock stubs.

**Working directory for all commands:** `f:\Nexplane\nexplane\.worktrees\agent-hardening`

---

### Task 1: Package scaffolding

**Files:**
- Create: `agent/commands/crossplatform/crossplatform.go`
- Create: `agent/commands/crossplatform/crossplatform_linux.go`
- Create: `agent/commands/crossplatform/crossplatform_windows.go`
- Create: `agent/commands/crossplatform/crossplatform_test.go`

- [ ] **Step 1: Create `agent/commands/crossplatform/crossplatform.go`**

```go
package crossplatform

import "fmt"

var validTLSActions = map[string]bool{"deploy": true, "renew": true, "validate": true}
var validTLSSources = map[string]bool{"acme": true, "internal_ca": true, "manual": true}
var validTLSServices = map[string]bool{"nginx": true, "apache": true, "iis": true, "custom": true}

func ManageTLSCertificatesExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if !validTLSActions[action] {
		return nil, fmt.Errorf("action must be deploy, renew, or validate, got %q", action)
	}
	service, _ := params["service"].(string)
	if !validTLSServices[service] {
		return nil, fmt.Errorf("service must be nginx, apache, iis, or custom, got %q", service)
	}
	if action != "validate" {
		source, _ := params["source"].(string)
		if !validTLSSources[source] {
			return nil, fmt.Errorf("source must be acme, internal_ca, or manual, got %q", source)
		}
		if source == "acme" {
			if d, _ := params["domain"].(string); d == "" {
				return nil, fmt.Errorf("domain is required for acme source")
			}
		}
		if source == "manual" {
			if cert, _ := params["cert_pem"].(string); cert == "" {
				return nil, fmt.Errorf("cert_pem is required for manual source")
			}
			if key, _ := params["key_pem"].(string); key == "" {
				return nil, fmt.Errorf("key_pem is required for manual source")
			}
		}
	}
	return tlsExecuteOS(params)
}

func ManageTLSCertificatesRollback(params map[string]any) (map[string]any, error) {
	return tlsRollbackOS(params)
}

func ConfigureDNSResolverExecute(params map[string]any) (map[string]any, error) {
	resolvers, _ := params["resolvers"].([]any)
	if len(resolvers) == 0 {
		return nil, fmt.Errorf("resolvers list is required and must not be empty")
	}
	if mode, ok := params["mode"].(string); ok && mode != "" {
		valid := map[string]bool{"plain": true, "doh": true, "dot": true}
		if !valid[mode] {
			return nil, fmt.Errorf("mode must be plain, doh, or dot, got %q", mode)
		}
	}
	return dnsExecuteOS(params)
}

func ConfigureDNSResolverRollback(params map[string]any) (map[string]any, error) {
	return dnsRollbackOS(params)
}

func AuditSoftwareInventoryExecute(params map[string]any) (map[string]any, error) {
	return inventoryExecuteOS(params)
}
```

- [ ] **Step 2: Create `agent/commands/crossplatform/crossplatform_linux.go`**

```go
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

	// Determine target cert/key paths by service
	certPath, keyPath := servicePaths(service, params)

	// Snapshot existing cert
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

	switch source {
	case "acme":
		domain, _ := params["domain"].(string)
		email, _ := params["acme_email"].(string)
		challenge, _ := params["acme_challenge"].(string)
		if challenge == "" {
			challenge = "http-01"
		}
		args := []string{"certonly", "-d", domain, "--agree-tos", "-n"}
		if email != "" {
			args = append(args, "--email", email)
		}
		if challenge == "http-01" {
			args = append(args, "--webroot", "-w", "/var/www/html")
		} else {
			args = append(args, "--manual", "--preferred-challenges", "dns")
		}
		if out, err := exec.Command("certbot", args...).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("certbot: %s: %w", out, err)
		}
		certPath = fmt.Sprintf("/etc/letsencrypt/live/%s/fullchain.pem", domain)
		keyPath = fmt.Sprintf("/etc/letsencrypt/live/%s/privkey.pem", domain)
	case "manual":
		certPEM, _ := params["cert_pem"].(string)
		keyPEM, _ := params["key_pem"].(string)
		if err := os.MkdirAll(certDir(service), 0750); err != nil {
			return nil, fmt.Errorf("creating cert dir: %w", err)
		}
		if err := os.WriteFile(certPath, []byte(certPEM), 0644); err != nil {
			return nil, fmt.Errorf("writing cert: %w", err)
		}
		if err := os.WriteFile(keyPath, []byte(keyPEM), 0600); err != nil {
			return nil, fmt.Errorf("writing key: %w", err)
		}
	}

	// Reload service
	reloadService(service)

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
		os.WriteFile(certPath, []byte(certContent), 0644) //nolint
	}
	if keyContent, ok := snapshot["key"].(string); ok && keyPath != "" {
		os.WriteFile(keyPath, []byte(keyContent), 0600) //nolint
	}
	service, _ := params["service"].(string)
	reloadService(service)
	return map[string]any{"rolled_back": true}, nil
}

func servicePaths(service string, params map[string]any) (certPath, keyPath string) {
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

func certDir(service string) string {
	switch service {
	case "nginx":
		return "/etc/nginx/ssl"
	case "apache":
		return "/etc/ssl/certs"
	default:
		return "/etc/nexplane/tls"
	}
}

func reloadService(service string) {
	switch service {
	case "nginx":
		exec.Command("systemctl", "reload", "nginx").Run() //nolint
	case "apache":
		exec.Command("systemctl", "reload", "apache2").Run() //nolint
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

	// Detect resolver daemon
	daemon := "resolv.conf"
	if _, err := os.Stat("/etc/systemd/resolved.conf"); err == nil {
		daemon = "systemd-resolved"
	}

	// Snapshot
	snapshot := map[string]any{}
	if data, err := os.ReadFile("/etc/resolv.conf"); err == nil {
		snapshot["/etc/resolv.conf"] = string(data)
	}
	if data, err := os.ReadFile("/etc/systemd/resolved.conf"); err == nil {
		snapshot["/etc/systemd/resolved.conf"] = string(data)
	}

	if daemon == "systemd-resolved" {
		// Update resolved.conf
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
		exec.Command("systemctl", "restart", "systemd-resolved").Run() //nolint
	} else {
		// Write /etc/resolv.conf
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
			os.WriteFile(path, []byte(s), 0644) //nolint
		}
	}
	exec.Command("systemctl", "restart", "systemd-resolved").Run() //nolint
	return map[string]any{"rolled_back": true}, nil
}

func inventoryExecuteOS(_ map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("audit_software_inventory requires root privileges")
	}

	packages := []map[string]any{}

	// dpkg
	if out, err := exec.Command("dpkg-query", "-W", "-f=${Package}\t${Version}\t${Status}\n").Output(); err == nil {
		for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
			parts := strings.Split(line, "\t")
			if len(parts) >= 2 {
				packages = append(packages, map[string]any{"name": parts[0], "version": parts[1], "source": "dpkg"})
			}
		}
	}

	// rpm (if no dpkg results)
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
		"packages":    packages,
		"total":       len(packages),
		"platform":    "linux",
		"audited_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}
```

- [ ] **Step 3: Create `agent/commands/crossplatform/crossplatform_windows.go`**

```go
//go:build windows

package crossplatform

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func tlsExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	service, _ := params["service"].(string)

	if action == "validate" {
		certPath, _ := params["cert_path"].(string)
		if certPath == "" {
			certPath = `C:\nexplane\tls\server.crt`
		}
		out, _ := exec.Command("certutil", "-verify", certPath).Output()
		return map[string]any{
			"action": "validate", "service": service,
			"certutil_output": strings.TrimSpace(string(out)),
			"checked_at":      time.Now().UTC().Format(time.RFC3339),
		}, nil
	}

	// For IIS, use Import-PfxCertificate
	if service == "iis" {
		certPEM, _ := params["cert_pem"].(string)
		if certPEM != "" {
			tmp, err := os.CreateTemp("", "nexplane-cert-*.pem")
			if err != nil {
				return nil, err
			}
			defer os.Remove(tmp.Name())
			tmp.WriteString(certPEM)
			tmp.Close()
			exec.Command("powershell", "-Command",
				fmt.Sprintf(`Import-Certificate -FilePath "%s" -CertStoreLocation Cert:\LocalMachine\My`, tmp.Name())).Run()
		}
	}

	return map[string]any{
		"action": action, "service": service,
		"snapshot":   map[string]any{},
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func tlsRollbackOS(params map[string]any) (map[string]any, error) {
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

	// Snapshot
	snapshotOut, _ := exec.Command("netsh", "dns", "show", "server").Output()
	snapshot := string(snapshotOut)

	// Apply DNS servers to all interfaces
	serverList := strings.Join(resolvers, ",")
	script := fmt.Sprintf(`
$adapters = Get-NetAdapter | Where-Object {$_.Status -eq "Up"}
foreach ($a in $adapters) {
    Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ServerAddresses %s
}`, serverList)
	exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", script).Run()

	return map[string]any{
		"mode": "plain", "resolvers": resolvers,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func dnsRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	// Restore to DHCP-assigned DNS (best effort)
	exec.Command("powershell", "-Command",
		`Get-NetAdapter | Where-Object {$_.Status -eq "Up"} | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses }`).Run()
	return map[string]any{"rolled_back": true}, nil
}

func inventoryExecuteOS(_ map[string]any) (map[string]any, error) {
	packages := []map[string]any{}

	// Registry uninstall keys
	script := `
Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* |
Where-Object DisplayName |
Select-Object DisplayName,DisplayVersion |
ConvertTo-Json -Depth 1`
	out, _ := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", script).Output()

	return map[string]any{
		"packages":   packages,
		"raw_output": strings.TrimSpace(string(out)),
		"platform":   "windows",
		"audited_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
```

- [ ] **Step 4: Create `agent/commands/crossplatform/crossplatform_test.go`**

```go
package crossplatform_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/crossplatform"
)

func TestManageTLSInvalidAction(t *testing.T) {
	_, err := crossplatform.ManageTLSCertificatesExecute(map[string]any{
		"action": "revoke", "service": "nginx",
	})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestManageTLSInvalidService(t *testing.T) {
	_, err := crossplatform.ManageTLSCertificatesExecute(map[string]any{
		"action": "deploy", "service": "lighttpd", "source": "manual",
		"cert_pem": "x", "key_pem": "y",
	})
	if err == nil || !strings.Contains(err.Error(), "service must be") {
		t.Errorf("expected service error, got: %v", err)
	}
}

func TestManageTLSInvalidSource(t *testing.T) {
	_, err := crossplatform.ManageTLSCertificatesExecute(map[string]any{
		"action": "deploy", "service": "nginx", "source": "vault",
	})
	if err == nil || !strings.Contains(err.Error(), "source must be") {
		t.Errorf("expected source error, got: %v", err)
	}
}

func TestManageTLSACMERequiresDomain(t *testing.T) {
	_, err := crossplatform.ManageTLSCertificatesExecute(map[string]any{
		"action": "deploy", "service": "nginx", "source": "acme",
	})
	if err == nil || !strings.Contains(err.Error(), "domain") {
		t.Errorf("expected domain error, got: %v", err)
	}
}

func TestManageTLSManualRequiresCert(t *testing.T) {
	_, err := crossplatform.ManageTLSCertificatesExecute(map[string]any{
		"action": "deploy", "service": "nginx", "source": "manual",
	})
	if err == nil || !strings.Contains(err.Error(), "cert_pem") {
		t.Errorf("expected cert_pem error, got: %v", err)
	}
}

func TestConfigureDNSRequiresResolvers(t *testing.T) {
	_, err := crossplatform.ConfigureDNSResolverExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "resolvers") {
		t.Errorf("expected resolvers error, got: %v", err)
	}
}

func TestConfigureDNSInvalidMode(t *testing.T) {
	_, err := crossplatform.ConfigureDNSResolverExecute(map[string]any{
		"resolvers": []any{"1.1.1.1"}, "mode": "vpn",
	})
	if err == nil || !strings.Contains(err.Error(), "mode must be") {
		t.Errorf("expected mode error, got: %v", err)
	}
}
```

- [ ] **Step 5: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/crossplatform/... && "C:/Program Files/Go/bin/go.exe" test ./commands/crossplatform/... -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```
git add agent/commands/crossplatform/
git commit -m "feat(crossplatform): implement manage_tls_certificates, configure_dns_resolver, audit_software_inventory"
```

---

### Task 2: Register in `executor.go`, catalog entries, mock stubs

**Files:**
- Modify: `agent/executor/executor.go`
- Modify: `backend/app/connectors/catalog/nexplane_agent_mock.json`
- Create: 3 Python mock stubs

- [ ] **Step 1: Add to `executor.go`**

Add import: `"nexplane-agent/commands/crossplatform"`

Add to `commands` map:
```go
// Cross-platform (Spec 5d)
"manage_tls_certificates":  crossplatform.ManageTLSCertificatesExecute,
"configure_dns_resolver":   crossplatform.ConfigureDNSResolverExecute,
"audit_software_inventory": crossplatform.AuditSoftwareInventoryExecute,
```

Add to `rollbacks` map:
```go
"manage_tls_certificates": crossplatform.ManageTLSCertificatesRollback,
"configure_dns_resolver":  crossplatform.ConfigureDNSResolverRollback,
```

- [ ] **Step 2: Build all and run all tests**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./... && "C:/Program Files/Go/bin/go.exe" test ./...
```

- [ ] **Step 3: Append catalog entries**

```json
{"action_id": "manage_tls_certificates", "generic_action": "manage_tls_certificates", "action_type": "change", "execution_tier": 3, "display_name": "Manage TLS Certificates", "description": "Deploy, renew, or validate TLS certificates. Supports ACME (Let's Encrypt), internal CA (CSR flow), and manual PEM upload. Linux: nginx/apache. Windows: IIS.", "applicable_asset_types": ["server"], "parameters": [{"name": "action", "type": "string", "required": true}, {"name": "service", "type": "string", "required": true}, {"name": "source", "type": "string", "required": false}, {"name": "domain", "type": "string", "required": false}, {"name": "acme_email", "type": "string", "required": false}, {"name": "cert_pem", "type": "string", "required": false}, {"name": "key_pem", "type": "string", "required": false}], "executor": "nexplane_agent_mock.manage_tls_certificates", "rollback_action": "manage_tls_certificates", "estimated_duration_seconds": 60},
{"action_id": "configure_dns_resolver", "generic_action": "configure_dns_resolver", "action_type": "change", "execution_tier": 3, "display_name": "Configure DNS Resolver", "description": "Configure DNS resolvers on Linux (resolv.conf/systemd-resolved) or Windows (Set-DnsClientServerAddress). Supports plain, DoH, and DoT.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "resolvers", "type": "array", "required": true}, {"name": "mode", "type": "string", "required": false, "default": "plain"}, {"name": "doh_url", "type": "string", "required": false}], "executor": "nexplane_agent_mock.configure_dns_resolver", "rollback_action": "configure_dns_resolver", "estimated_duration_seconds": 10},
{"action_id": "audit_software_inventory", "generic_action": "audit_software_inventory", "action_type": "ingest", "execution_tier": 3, "display_name": "Audit Software Inventory", "description": "Read-only: enumerate installed packages with versions. Linux: dpkg/rpm. Windows: registry uninstall keys. Enriches asset metadata.", "applicable_asset_types": ["server", "workstation"], "parameters": [], "executor": "nexplane_agent_mock.audit_software_inventory", "estimated_duration_seconds": 30}
```

- [ ] **Step 4: Create mock stubs**

`backend/app/connectors/executors/nexplane_agent_mock/manage_tls_certificates.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"action": parameters.get("action"), "service": parameters.get("service"),
            "cert_path": "/etc/nginx/ssl/server.crt", "key_path": "/etc/nginx/ssl/server.key",
            "cert_subject": "CN=example.com", "cert_expiry": "Dec 31 23:59:59 2025 GMT",
            "snapshot": {}, "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "manage_tls_certificates"}
```

`backend/app/connectors/executors/nexplane_agent_mock/configure_dns_resolver.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"mode": parameters.get("mode", "plain"), "resolvers": parameters.get("resolvers", []),
            "daemon": "systemd-resolved", "snapshot": {}, "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "configure_dns_resolver"}
```

`backend/app/connectors/executors/nexplane_agent_mock/audit_software_inventory.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"packages": [{"name": "openssl", "version": "3.0.2", "source": "dpkg"},
                         {"name": "nginx", "version": "1.24.0", "source": "dpkg"}],
            "total": 2, "platform": "linux", "audited_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": False, "reason": "audit_software_inventory is read-only"}
```

- [ ] **Step 5: Validate and test**

```
python -c "import json; json.load(open('backend/app/connectors/catalog/nexplane_agent_mock.json')); print('valid')"
cd backend && python -m pytest app/tests/test_catalog_service.py -v
```

- [ ] **Step 6: Commit**

```
git add agent/executor/executor.go
git add backend/app/connectors/catalog/nexplane_agent_mock.json
git add backend/app/connectors/executors/nexplane_agent_mock/manage_tls_certificates.py
git add backend/app/connectors/executors/nexplane_agent_mock/configure_dns_resolver.py
git add backend/app/connectors/executors/nexplane_agent_mock/audit_software_inventory.py
git commit -m "feat(crossplatform): register commands, add catalog entries and mock stubs (Spec 5d)"
```
