//go:build linux

package compliance_test

import (
	"context"
	"testing"

	"nexplane-agent/commands/compliance"
)

// ensure context import doesn't cause error (used by other tests conceptually)
var _ = context.Background

func TestAuditCISCompliance_ReturnsStructuredOutput(t *testing.T) {
	out, err := compliance.AuditCISComplianceExecute(map[string]any{
		"level":     float64(1),
		"os_family": "rhel",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	controls, ok := out["controls"]
	if !ok {
		t.Fatal("missing 'controls' key in output")
	}
	list, ok := controls.([]compliance.ControlResult)
	if !ok {
		t.Fatalf("controls is not []ControlResult, got %T", controls)
	}
	if len(list) == 0 {
		t.Fatal("expected at least one control result")
	}
	score, ok := out["score"].(float64)
	if !ok {
		t.Fatal("missing or non-float 'score' key in output")
	}
	if score < 0 || score > 1 {
		t.Fatalf("score out of range [0,1]: %f", score)
	}
}

func TestAuditCISCompliance_Level1_OnlyBasicSections(t *testing.T) {
	out, err := compliance.AuditCISComplianceExecute(map[string]any{
		"level":     float64(1),
		"os_family": "debian",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	controls := out["controls"].([]compliance.ControlResult)
	// Level 1 must not include pam, auditd, selinux, apparmor sections
	for _, c := range controls {
		switch c.Section {
		case "pam", "auditd", "selinux", "apparmor":
			t.Errorf("level 1 should not include section %q (control %s)", c.Section, c.ID)
		}
	}
}

func TestAuditCISCompliance_Level2_IncludesAllSections(t *testing.T) {
	out, err := compliance.AuditCISComplianceExecute(map[string]any{
		"level":     float64(2),
		"os_family": "rhel",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	controls := out["controls"].([]compliance.ControlResult)
	sections := map[string]bool{}
	for _, c := range controls {
		sections[c.Section] = true
	}
	for _, want := range []string{"filesystem", "sysctl", "ssh", "pam", "auditd", "selinux"} {
		if !sections[want] {
			t.Errorf("level 2 rhel: expected section %q in controls", want)
		}
	}
}

func TestAuditCISCompliance_InvalidOSFamily(t *testing.T) {
	_, err := compliance.AuditCISComplianceExecute(map[string]any{
		"level":     float64(1),
		"os_family": "openbsd",
	})
	if err == nil {
		t.Fatal("expected error for unsupported os_family")
	}
}

func TestAuditCISCompliance_MissingLevel(t *testing.T) {
	_, err := compliance.AuditCISComplianceExecute(map[string]any{
		"os_family": "rhel",
	})
	if err == nil {
		t.Fatal("expected error when level is missing")
	}
}

func TestCalculateScore(t *testing.T) {
	controls := []compliance.ControlResult{
		{Status: "pass"},
		{Status: "pass"},
		{Status: "fail"},
		{Status: "skip"},
	}
	score := compliance.CalculateScore(controls)
	// 2 pass out of 3 non-skip = 0.666...
	if score < 0.66 || score > 0.67 {
		t.Fatalf("expected ~0.666, got %f", score)
	}
}

func TestCalculateScore_AllSkip(t *testing.T) {
	controls := []compliance.ControlResult{
		{Status: "skip"},
		{Status: "skip"},
	}
	score := compliance.CalculateScore(controls)
	if score != 1.0 {
		t.Fatalf("all-skip score should be 1.0, got %f", score)
	}
}
