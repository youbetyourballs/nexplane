package registration_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"nexplane-agent/client"
	"nexplane-agent/registration"
)

func TestRegistrationCallsRegisterEndpoint(t *testing.T) {
	called := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/agent/register" && r.Method == http.MethodPost {
			called = true
			json.NewEncoder(w).Encode(map[string]string{
				"agent_id": "00000000-0000-0000-0000-000000000001",
				"asset_id": "00000000-0000-0000-0000-000000000002",
			})
		}
	}))
	defer srv.Close()

	c := client.New(srv.URL, "test-secret")
	_, err := registration.Register(context.Background(), c, "machine-id-1", "test-host", "windows", "0.1.0")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !called {
		t.Error("register endpoint was not called")
	}
}
