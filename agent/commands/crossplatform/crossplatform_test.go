package crossplatform_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/crossplatform"
)

func TestManageTLSInvalidAction(t *testing.T) {
	_, err := crossplatform.ManageTLSCertificatesExecute(map[string]any{"action": "revoke", "service": "nginx"})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestManageTLSInvalidService(t *testing.T) {
	_, err := crossplatform.ManageTLSCertificatesExecute(map[string]any{"action": "deploy", "service": "lighttpd", "source": "manual", "cert_pem": "x", "key_pem": "y"})
	if err == nil || !strings.Contains(err.Error(), "service must be") {
		t.Errorf("expected service error, got: %v", err)
	}
}

func TestManageTLSInvalidSource(t *testing.T) {
	_, err := crossplatform.ManageTLSCertificatesExecute(map[string]any{"action": "deploy", "service": "nginx", "source": "vault"})
	if err == nil || !strings.Contains(err.Error(), "source must be") {
		t.Errorf("expected source error, got: %v", err)
	}
}

func TestManageTLSACMERequiresDomain(t *testing.T) {
	_, err := crossplatform.ManageTLSCertificatesExecute(map[string]any{"action": "deploy", "service": "nginx", "source": "acme"})
	if err == nil || !strings.Contains(err.Error(), "domain") {
		t.Errorf("expected domain error, got: %v", err)
	}
}

func TestManageTLSManualRequiresCert(t *testing.T) {
	_, err := crossplatform.ManageTLSCertificatesExecute(map[string]any{"action": "deploy", "service": "nginx", "source": "manual"})
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
	_, err := crossplatform.ConfigureDNSResolverExecute(map[string]any{"resolvers": []any{"1.1.1.1"}, "mode": "vpn"})
	if err == nil || !strings.Contains(err.Error(), "mode must be") {
		t.Errorf("expected mode error, got: %v", err)
	}
}
