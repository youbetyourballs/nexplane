package config_test

import (
	"os"
	"testing"

	"nexplane-agent/config"
)

func TestLoadFromFlags(t *testing.T) {
	cfg, err := config.Load([]string{
		"--control-plane", "https://nexplane.example.com",
		"--secret", "sk-agent-abc123",
		"--mode", "ephemeral",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ControlPlane != "https://nexplane.example.com" {
		t.Errorf("got %q, want %q", cfg.ControlPlane, "https://nexplane.example.com")
	}
	if cfg.Secret != "sk-agent-abc123" {
		t.Errorf("got %q, want %q", cfg.Secret, "sk-agent-abc123")
	}
	if cfg.Mode != "ephemeral" {
		t.Errorf("got %q, want %q", cfg.Mode, "ephemeral")
	}
}

func TestLoadFromEnv(t *testing.T) {
	os.Setenv("NP_CONTROL_PLANE", "https://env.example.com")
	os.Setenv("NP_SECRET", "sk-agent-env")
	os.Setenv("NP_MODE", "service")
	defer func() {
		os.Unsetenv("NP_CONTROL_PLANE")
		os.Unsetenv("NP_SECRET")
		os.Unsetenv("NP_MODE")
	}()

	cfg, err := config.Load([]string{})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ControlPlane != "https://env.example.com" {
		t.Errorf("got %q, want %q", cfg.ControlPlane, "https://env.example.com")
	}
}

func TestLoadDefaultMode(t *testing.T) {
	cfg, err := config.Load([]string{
		"--control-plane", "https://nexplane.example.com",
		"--secret", "sk-agent-abc",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Mode != "service" {
		t.Errorf("default mode should be 'service', got %q", cfg.Mode)
	}
	if cfg.PollInterval.Seconds() != 30 {
		t.Errorf("default poll interval should be 30s, got %v", cfg.PollInterval)
	}
}

func TestLoadErrorsMissingRequired(t *testing.T) {
	_, err := config.Load([]string{})
	if err == nil {
		t.Error("expected error for missing required flags, got nil")
	}
}
