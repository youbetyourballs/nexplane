//go:build windows

package changip_test

import (
	"testing"

	"nexplane-agent/commands/changip"
)

func TestExecuteRequiresInterface(t *testing.T) {
	_, err := changip.Execute(map[string]any{
		"mode": "static",
	})
	if err == nil {
		t.Error("expected error for missing interface")
	}
}

func TestExecuteRequiresMode(t *testing.T) {
	_, err := changip.Execute(map[string]any{
		"interface": "Ethernet",
	})
	if err == nil {
		t.Error("expected error for missing mode")
	}
}

func TestExecuteInvalidMode(t *testing.T) {
	_, err := changip.Execute(map[string]any{
		"interface": "Ethernet",
		"mode":      "invalid",
	})
	if err == nil {
		t.Error("expected error for invalid mode")
	}
}
