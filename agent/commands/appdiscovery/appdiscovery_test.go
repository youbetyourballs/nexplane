package appdiscovery

import (
	"testing"
)

func TestDiscoverApplicationsExecuteReturnsMap(t *testing.T) {
	result, err := DiscoverApplicationsExecute(map[string]any{})
	if err != nil {
		t.Fatalf("DiscoverApplicationsExecute returned error: %v", err)
	}
	if result["action"] != "discover_applications" {
		t.Errorf("expected action=discover_applications, got %v", result["action"])
	}
	apps, ok := result["applications"].([]map[string]any)
	if !ok {
		t.Errorf("expected applications to be []map[string]any, got %T", result["applications"])
	}
	_ = apps
}

func TestPortBindingsToMaps(t *testing.T) {
	bindings := []PortBinding{{Port: 80, Protocol: "tcp"}, {Port: 443, Protocol: "tcp"}}
	maps := portBindingsToMaps(bindings)
	if len(maps) != 2 {
		t.Fatalf("expected 2 maps, got %d", len(maps))
	}
	if maps[0]["port"] != 80 {
		t.Errorf("expected port 80, got %v", maps[0]["port"])
	}
}
