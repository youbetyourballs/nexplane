package ossecurity_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/ossecurity"
)

func TestConfigureSELinuxRequiresParam(t *testing.T) {
	_, err := ossecurity.ConfigureSELinuxExecute(map[string]any{})
	if err == nil {
		t.Error("expected error when no params provided")
	}
}

func TestConfigureSELinuxInvalidMode(t *testing.T) {
	_, err := ossecurity.ConfigureSELinuxExecute(map[string]any{"mode": "turbo"})
	if err == nil || !strings.Contains(err.Error(), "invalid mode") {
		t.Errorf("expected 'invalid mode' error, got: %v", err)
	}
}

func TestConfigureSELinuxValidModes(t *testing.T) {
	for _, m := range []string{"enforcing", "permissive", "disabled"} {
		_, err := ossecurity.ConfigureSELinuxExecute(map[string]any{"mode": m})
		if err != nil && strings.Contains(err.Error(), "invalid mode") {
			t.Errorf("mode %q should be valid, got: %v", m, err)
		}
	}
}

func TestConfigureAppArmorInvalidMode(t *testing.T) {
	_, err := ossecurity.ConfigureAppArmorExecute(map[string]any{"mode": "broken"})
	if err == nil || !strings.Contains(err.Error(), "invalid mode") {
		t.Errorf("expected 'invalid mode' error, got: %v", err)
	}
}

func TestConfigureSeccompRequiresService(t *testing.T) {
	_, err := ossecurity.ConfigureSeccompExecute(map[string]any{"profile": "{}"})
	if err == nil || !strings.Contains(err.Error(), "service_name") {
		t.Errorf("expected service_name error, got: %v", err)
	}
}

func TestConfigureSeccompRequiresProfile(t *testing.T) {
	_, err := ossecurity.ConfigureSeccompExecute(map[string]any{"service_name": "nginx"})
	if err == nil || !strings.Contains(err.Error(), "profile") {
		t.Errorf("expected profile error, got: %v", err)
	}
}

func TestConfigureHostFirewallInvalidAction(t *testing.T) {
	_, err := ossecurity.ConfigureHostFirewallExecute(map[string]any{"action": "delete"})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestBlacklistKernelModulesRequiresModules(t *testing.T) {
	_, err := ossecurity.BlacklistKernelModulesExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "modules") {
		t.Errorf("expected modules error, got: %v", err)
	}
}

func TestDeployAuditdRulesRequiresProfileOrRules(t *testing.T) {
	_, err := ossecurity.DeployAuditdRulesExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "profile or rules") {
		t.Errorf("expected profile or rules error, got: %v", err)
	}
}
