package linuxauth_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/linuxauth"
)

func TestConfigurePAMInvalidProfile(t *testing.T) {
	_, err := linuxauth.ConfigurePAMExecute(map[string]any{"profile": "not_a_profile"})
	if err == nil {
		t.Error("expected error for invalid profile")
	}
	if err != nil && !strings.Contains(err.Error(), "invalid profile") {
		t.Errorf("expected 'invalid profile' in error, got: %v", err)
	}
}

func TestConfigurePAMValidProfilesPassValidation(t *testing.T) {
	for _, profile := range []string{"cis_level1", "cis_level2", "custom"} {
		_, err := linuxauth.ConfigurePAMExecute(map[string]any{"profile": profile})
		if err != nil && strings.Contains(err.Error(), "invalid profile") {
			t.Errorf("profile %q should pass validation, got: %v", profile, err)
		}
	}
}

func TestConfigurePAMRollbackRequiresSnapshot(t *testing.T) {
	_, err := linuxauth.ConfigurePAMRollback(map[string]any{})
	if err == nil {
		t.Error("expected error when files_snapshot missing")
	}
}

func TestManageCACertificatesInvalidAction(t *testing.T) {
	_, err := linuxauth.ManageCACertificatesExecute(map[string]any{
		"action": "update", "cert_name": "test",
	})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected 'action must be' error, got: %v", err)
	}
}

func TestManageCACertificatesRequiresCertName(t *testing.T) {
	_, err := linuxauth.ManageCACertificatesExecute(map[string]any{"action": "install"})
	if err == nil || !strings.Contains(err.Error(), "cert_name") {
		t.Errorf("expected cert_name error, got: %v", err)
	}
}

func TestManageCACertificatesInstallRequiresPEM(t *testing.T) {
	_, err := linuxauth.ManageCACertificatesExecute(map[string]any{
		"action": "install", "cert_name": "test",
	})
	if err == nil || !strings.Contains(err.Error(), "certificate PEM") {
		t.Errorf("expected certificate PEM error, got: %v", err)
	}
}

func TestConfigureNTPRequiresServers(t *testing.T) {
	_, err := linuxauth.ConfigureNTPExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "servers") {
		t.Errorf("expected servers error, got: %v", err)
	}
}

func TestConfigureNTPEmptyServersFails(t *testing.T) {
	_, err := linuxauth.ConfigureNTPExecute(map[string]any{"servers": []any{}})
	if err == nil {
		t.Error("expected error for empty servers list")
	}
}

func TestConfigureNTPRollbackRequiresSnapshot(t *testing.T) {
	_, err := linuxauth.ConfigureNTPRollback(map[string]any{})
	if err == nil {
		t.Error("expected error when snapshot missing")
	}
}
