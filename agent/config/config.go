package config

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"time"
)

type Config struct {
	ControlPlane string
	Secret       string
	Mode         string
	PollInterval time.Duration
}

// Load parses configuration from flags first, then falls back to environment variables.
func Load(args []string) (*Config, error) {
	fs := flag.NewFlagSet("nexplane-agent", flag.ContinueOnError)

	controlPlane := fs.String("control-plane", "", "Base URL of the Nexplane control plane")
	secret := fs.String("secret", "", "Shared HMAC secret for agent authentication")
	mode := fs.String("mode", "", "Operation mode: ephemeral or service (default: service)")
	pollInterval := fs.Duration("poll-interval", 0, "Poll interval in service mode (default: 30s)")

	if err := fs.Parse(args); err != nil {
		return nil, fmt.Errorf("parsing flags: %w", err)
	}

	cfg := &Config{}

	if *controlPlane != "" {
		cfg.ControlPlane = *controlPlane
	} else if v := os.Getenv("NP_CONTROL_PLANE"); v != "" {
		cfg.ControlPlane = v
	}

	if *secret != "" {
		cfg.Secret = *secret
	} else if v := os.Getenv("NP_SECRET"); v != "" {
		cfg.Secret = v
	}

	if *mode != "" {
		cfg.Mode = *mode
	} else if v := os.Getenv("NP_MODE"); v != "" {
		cfg.Mode = v
	} else {
		cfg.Mode = "service"
	}

	if *pollInterval != 0 {
		cfg.PollInterval = *pollInterval
	} else if v := os.Getenv("NP_POLL_INTERVAL"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return nil, fmt.Errorf("invalid NP_POLL_INTERVAL %q: %w", v, err)
		}
		cfg.PollInterval = d
	} else {
		cfg.PollInterval = 30 * time.Second
	}

	var errs []string
	if cfg.ControlPlane == "" {
		errs = append(errs, "--control-plane / NP_CONTROL_PLANE is required")
	}
	if cfg.Secret == "" {
		errs = append(errs, "--secret / NP_SECRET is required")
	}
	if cfg.Mode != "ephemeral" && cfg.Mode != "service" {
		errs = append(errs, fmt.Sprintf("--mode must be 'ephemeral' or 'service', got %q", cfg.Mode))
	}
	if len(errs) > 0 {
		return nil, errors.New(errs[0])
	}

	return cfg, nil
}
