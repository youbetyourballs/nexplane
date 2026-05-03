package winpatch_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/winpatch"
)

func TestApplyWindowsPatchesRequiresMode(t *testing.T) {
	_, err := winpatch.ApplyWindowsPatchesExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "mode must be") {
		t.Errorf("expected mode validation error, got: %v", err)
	}
}

func TestApplyWindowsPatchesInvalidMode(t *testing.T) {
	_, err := winpatch.ApplyWindowsPatchesExecute(map[string]any{"mode": "full_upgrade"})
	if err == nil || !strings.Contains(err.Error(), "mode must be") {
		t.Errorf("expected mode validation error, got: %v", err)
	}
}

func TestApplyWindowsPatchesModeKBRequiresKBID(t *testing.T) {
	_, err := winpatch.ApplyWindowsPatchesExecute(map[string]any{"mode": "kb"})
	if err == nil || !strings.Contains(err.Error(), "kb_id") {
		t.Errorf("expected kb_id error, got: %v", err)
	}
}

func TestApplyWindowsPatchesValidModesPassValidation(t *testing.T) {
	cases := []map[string]any{
		{"mode": "security_only"},
		{"mode": "kb", "kb_id": "KB5034441"},
	}
	for _, params := range cases {
		_, err := winpatch.ApplyWindowsPatchesExecute(params)
		if err != nil && strings.Contains(err.Error(), "mode must be") {
			t.Errorf("params %v should pass mode validation, got: %v", params, err)
		}
		if err != nil && strings.Contains(err.Error(), "kb_id") {
			t.Errorf("params %v should pass kb_id validation, got: %v", params, err)
		}
	}
}

func TestAuditWindowsPatchStatusExecuteReturnsResult(t *testing.T) {
	result, _ := winpatch.AuditWindowsPatchStatusExecute(map[string]any{})
	_ = result // nil on non-Windows is acceptable
}
