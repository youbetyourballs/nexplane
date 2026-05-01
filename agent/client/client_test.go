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
