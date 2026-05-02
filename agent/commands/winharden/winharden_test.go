package winharden_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/winharden"
)

func TestConfigureLAPSInvalidAction(t *testing.T) {
	_, err := winharden.ConfigureLAPSExecute(map[string]any{"action": "toggle"})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestEnableCredentialGuardInvalidAction(t *testing.T) {
	_, err := winharden.EnableCredentialGuardExecute(map[string]any{"action": "maybe"})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestEnforcePSCLMInvalidAction(t *testing.T) {
	_, err := winharden.EnforcePowerShellCLMExecute(map[string]any{"action": "toggle"})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestDeployAppLockerPolicyRequiresPolicy(t *testing.T) {
	_, err := winharden.DeployAppLockerPolicyExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "policy") {
		t.Errorf("expected policy error, got: %v", err)
	}
}

func TestEnableBitLockerInvalidProtector(t *testing.T) {
	_, err := winharden.EnableBitLockerExecute(map[string]any{"protector": "usb"})
	if err == nil || !strings.Contains(err.Error(), "protector must be") {
		t.Errorf("expected protector error, got: %v", err)
	}
}

func TestEnableBitLockerTPMPinRequiresPin(t *testing.T) {
	_, err := winharden.EnableBitLockerExecute(map[string]any{"protector": "tpm_pin"})
	if err == nil || !strings.Contains(err.Error(), "pin is required") {
		t.Errorf("expected pin error, got: %v", err)
	}
}

func TestConfigureWindowsFirewallInvalidAction(t *testing.T) {
	_, err := winharden.ConfigureWindowsFirewallExecute(map[string]any{"action": "delete"})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestConfigureWindowsAuditPolicyInvalidProfile(t *testing.T) {
	_, err := winharden.ConfigureWindowsAuditPolicyExecute(map[string]any{"profile": "nsa_level42"})
	if err == nil || !strings.Contains(err.Error(), "profile must be") {
		t.Errorf("expected profile error, got: %v", err)
	}
}
