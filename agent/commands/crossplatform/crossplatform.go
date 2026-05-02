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
