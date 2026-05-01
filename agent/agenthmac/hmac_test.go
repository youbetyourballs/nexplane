package agenthmac_test

import (
	"testing"

	"nexplane-agent/agenthmac"
)

func TestVerifyAcceptsCorrectSignature(t *testing.T) {
	sig := agenthmac.Sign("mysecret", "job-1", "change_ip", map[string]any{"interface": "eth0"})
	if !agenthmac.Verify("mysecret", "job-1", "change_ip", map[string]any{"interface": "eth0"}, sig) {
		t.Error("verify should accept correct signature")
	}
}

func TestVerifyRejectsWrongSecret(t *testing.T) {
	sig := agenthmac.Sign("secret-a", "job-1", "cmd", map[string]any{})
	if agenthmac.Verify("secret-b", "job-1", "cmd", map[string]any{}, sig) {
		t.Error("verify should reject wrong secret")
	}
}

func TestVerifyRejectsModifiedParams(t *testing.T) {
	sig := agenthmac.Sign("secret", "job-1", "cmd", map[string]any{"a": 1})
	if agenthmac.Verify("secret", "job-1", "cmd", map[string]any{"a": 2}, sig) {
		t.Error("verify should reject modified parameters")
	}
}

func TestSignIsDeterministic(t *testing.T) {
	s1 := agenthmac.Sign("s", "j", "c", map[string]any{"b": 2, "a": 1})
	s2 := agenthmac.Sign("s", "j", "c", map[string]any{"a": 1, "b": 2})
	if s1 != s2 {
		t.Error("sign should be deterministic regardless of map key order")
	}
}
