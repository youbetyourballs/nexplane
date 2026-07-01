package client_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"nexplane-agent/client"
)

func TestClientSendsBearerToken(t *testing.T) {
	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		json.NewEncoder(w).Encode(map[string]string{
			"agent_id": "00000000-0000-0000-0000-000000000001",
			"asset_id": "00000000-0000-0000-0000-000000000002",
		})
	}))
	defer srv.Close()

	c := client.New(srv.URL, "my-secret")
	_, err := c.Register(context.Background(), client.RegisterRequest{
		MachineID: "test-machine",
		Hostname:  "host1",
		OsType:    "linux",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if gotAuth != "Bearer my-secret" {
		t.Errorf("got Authorization %q, want %q", gotAuth, "Bearer my-secret")
	}
}

func TestRegisterDecodesTunnelConfig(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{
			"agent_id":         "00000000-0000-0000-0000-000000000001",
			"asset_id":         "00000000-0000-0000-0000-000000000002",
			"tunnel_enabled":   true,
			"tunnel_allowlist": []string{"10.0.0.0/8:5432", "db.internal:5432"},
		})
	}))
	defer srv.Close()

	c := client.New(srv.URL, "s")
	resp, err := c.Register(context.Background(), client.RegisterRequest{MachineID: "m", Hostname: "h", OsType: "linux"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !resp.TunnelEnabled {
		t.Error("expected TunnelEnabled=true")
	}
	if len(resp.TunnelAllowlist) != 2 || resp.TunnelAllowlist[0] != "10.0.0.0/8:5432" {
		t.Errorf("got allowlist %v, want 2 rules starting with the CIDR", resp.TunnelAllowlist)
	}
}

func TestRegisterDefaultsTunnelDisabled(t *testing.T) {
	// A control plane that omits the tunnel fields (older / tunnel off) must
	// decode to disabled, not error.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]string{
			"agent_id": "00000000-0000-0000-0000-000000000001",
			"asset_id": "00000000-0000-0000-0000-000000000002",
		})
	}))
	defer srv.Close()

	c := client.New(srv.URL, "s")
	resp, err := c.Register(context.Background(), client.RegisterRequest{MachineID: "m", Hostname: "h", OsType: "linux"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.TunnelEnabled {
		t.Error("expected TunnelEnabled=false when field omitted")
	}
	if len(resp.TunnelAllowlist) != 0 {
		t.Errorf("expected empty allowlist, got %v", resp.TunnelAllowlist)
	}
}

func TestClientHandles401(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(401)
	}))
	defer srv.Close()

	c := client.New(srv.URL, "wrong-secret")
	_, err := c.Register(context.Background(), client.RegisterRequest{
		MachineID: "x",
		Hostname:  "y",
		OsType:    "linux",
	})
	if err == nil {
		t.Error("expected error on 401, got nil")
	}
}
