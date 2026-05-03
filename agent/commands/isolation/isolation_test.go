package isolation_test

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"nexplane-agent/commands/isolation"
)

func TestIsolationConfig_MissingManagementCIDR(t *testing.T) {
	cfg := isolation.IsolationConfig{ControlPlaneURL: "https://cp.example.com:443"}
	_, err := isolation.Isolate(context.Background(), cfg)
	if err == nil || !strings.Contains(err.Error(), "management_cidr") {
		t.Fatalf("expected management_cidr validation error, got: %v", err)
	}
}

func TestIsolationConfig_MissingControlPlaneURL(t *testing.T) {
	cfg := isolation.IsolationConfig{ManagementCIDR: "10.0.0.0/8"}
	_, err := isolation.Isolate(context.Background(), cfg)
	if err == nil || !strings.Contains(err.Error(), "control_plane_url") {
		t.Fatalf("expected control_plane_url validation error, got: %v", err)
	}
}

func TestPreIsolationState_RoundTrip(t *testing.T) {
	state := &isolation.PreIsolationState{
		OS:            "linux",
		IPTablesRules: "*filter\n:INPUT ACCEPT [0:0]\nCOMMIT\n",
	}
	b, err := json.Marshal(state)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var got isolation.PreIsolationState
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got.OS != state.OS || got.IPTablesRules != state.IPTablesRules {
		t.Fatalf("round-trip mismatch: got %+v", got)
	}
}

func TestRestore_NilState(t *testing.T) {
	err := isolation.Restore(context.Background(), nil)
	if err == nil || !strings.Contains(err.Error(), "state") {
		t.Fatalf("expected nil-state error, got: %v", err)
	}
}

func TestRestore_EmptyOS(t *testing.T) {
	state := &isolation.PreIsolationState{}
	err := isolation.Restore(context.Background(), state)
	if err == nil || !strings.Contains(err.Error(), "os") {
		t.Fatalf("expected missing-os error, got: %v", err)
	}
}
