package linuxpatch_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/linuxpatch"
)

func TestApplyLinuxPatchesRequiresMode(t *testing.T) {
	_, err := linuxpatch.ApplyLinuxPatchesExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "mode must be") {
		t.Errorf("expected mode validation error, got: %v", err)
	}
}

func TestApplyLinuxPatchesInvalidMode(t *testing.T) {
	_, err := linuxpatch.ApplyLinuxPatchesExecute(map[string]any{"mode": "full_upgrade"})
	if err == nil || !strings.Contains(err.Error(), "mode must be") {
		t.Errorf("expected mode validation error, got: %v", err)
	}
}

func TestApplyLinuxPatchesModePackageRequiresPackageName(t *testing.T) {
	_, err := linuxpatch.ApplyLinuxPatchesExecute(map[string]any{"mode": "package"})
	if err == nil || !strings.Contains(err.Error(), "package_name") {
		t.Errorf("expected package_name error, got: %v", err)
	}
}

func TestApplyLinuxPatchesModeCVERequiresCVEID(t *testing.T) {
	_, err := linuxpatch.ApplyLinuxPatchesExecute(map[string]any{"mode": "cve"})
	if err == nil || !strings.Contains(err.Error(), "cve_id") {
		t.Errorf("expected cve_id error, got: %v", err)
	}
}

func TestApplyLinuxPatchesValidModesPassValidation(t *testing.T) {
	cases := []map[string]any{
		{"mode": "security_only"},
		{"mode": "package", "package_name": "openssl"},
		{"mode": "cve", "cve_id": "CVE-2024-3094"},
	}
	for _, params := range cases {
		// On non-Linux the OS call will fail, but param validation must pass
		_, err := linuxpatch.ApplyLinuxPatchesExecute(params)
		if err != nil && strings.Contains(err.Error(), "mode must be") {
			t.Errorf("params %v should pass validation, got: %v", params, err)
		}
		if err != nil && strings.Contains(err.Error(), "package_name") {
			t.Errorf("params %v should pass validation, got: %v", params, err)
		}
		if err != nil && strings.Contains(err.Error(), "cve_id") {
			t.Errorf("params %v should pass validation, got: %v", params, err)
		}
	}
}

func TestAuditLinuxPatchStatusExecuteReturnsResult(t *testing.T) {
	// On any platform this returns either real data or an errNotSupported error.
	// The function must not panic.
	result, _ := linuxpatch.AuditLinuxPatchStatusExecute(map[string]any{})
	_ = result // nil on non-Linux is acceptable
}
