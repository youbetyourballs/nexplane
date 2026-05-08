package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"runtime"
	"syscall"

	"nexplane-agent/client"
	"nexplane-agent/config"
	"nexplane-agent/fingerprint"
	"nexplane-agent/poller"
	"nexplane-agent/registration"
	"nexplane-agent/updater"
)

// Version is injected at build time via -ldflags "-X main.Version=<version>".
// Falls back to "dev" for local builds.
var Version = "dev"

func main() {
	cfg, err := config.Load(os.Args[1:])
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}

	log.Printf("Nexplane Agent %s starting (mode=%s)", Version, cfg.Mode)

	// Check for updates before doing anything else. If an update is applied,
	// syscall.Exec replaces this process and we never reach the next line.
	if _, err := updater.CheckAndUpdate(context.Background(), cfg.ControlPlane, Version); err != nil {
		log.Printf("[updater] skipping update: %v", err)
	}

	machineID, err := fingerprint.GetMachineID()
	if err != nil {
		log.Fatalf("Cannot determine machine ID: %v", err)
	}

	hostname := os.Getenv("NP_HOSTNAME")
	if hostname == "" {
		hostname, _ = os.Hostname()
	}
	osType := runtime.GOOS

	c := client.New(cfg.ControlPlane, cfg.Secret)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	info, err := registration.Register(ctx, c, machineID, hostname, osType, Version)
	if err != nil {
		log.Fatalf("Registration failed: %v", err)
	}
	log.Printf("Registered: agent_id=%s asset_id=%s", info.AgentID, info.AssetID)

	switch cfg.Mode {
	case "ephemeral":
		if err := poller.RunEphemeral(ctx, c, info.AgentID, cfg.Secret); err != nil {
			log.Fatalf("Ephemeral run failed: %v", err)
		}
	case "service":
		poller.RunService(ctx, c, info.AgentID, cfg.Secret, cfg.PollInterval)
	}

	log.Println("Agent stopped.")
}
