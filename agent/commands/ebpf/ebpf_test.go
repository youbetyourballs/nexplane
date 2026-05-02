package ebpf_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/ebpf"
)

func TestDeployEBPFPolicyRequiresProgramPath(t *testing.T) {
	_, err := ebpf.DeployEBPFPolicyExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "program_path") {
		t.Errorf("expected program_path error, got: %v", err)
	}
}

func TestDeployEBPFPolicyInvalidAttachType(t *testing.T) {
	_, err := ebpf.DeployEBPFPolicyExecute(map[string]any{
		"program_path": "/tmp/prog.o",
		"attach_type":  "invalid",
	})
	if err == nil || !strings.Contains(err.Error(), "invalid attach_type") {
		t.Errorf("expected attach_type error, got: %v", err)
	}
}

func TestConfigureEBPFSecurityPolicyInvalidFramework(t *testing.T) {
	_, err := ebpf.ConfigureEBPFSecurityPolicyExecute(map[string]any{
		"framework": "unknown",
		"policy":    "{}",
	})
	if err == nil || !strings.Contains(err.Error(), "invalid framework") {
		t.Errorf("expected framework error, got: %v", err)
	}
}

func TestConfigureEBPFSecurityPolicyRequiresPolicy(t *testing.T) {
	_, err := ebpf.ConfigureEBPFSecurityPolicyExecute(map[string]any{
		"framework": "falco",
	})
	if err == nil || !strings.Contains(err.Error(), "policy") {
		t.Errorf("expected policy error, got: %v", err)
	}
}
