package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"runtime"
	"syscall"
	"time"

	"nexplane-agent/client"
	"nexplane-agent/commands/changip"
	"nexplane-agent/config"
	"nexplane-agent/executor"
	"nexplane-agent/fingerprint"
	"nexplane-agent/installer"
	"nexplane-agent/poller"
	"nexplane-agent/registration"
	"nexplane-agent/tunnelsupervisor"
	"nexplane-agent/updater"
)

// tunnelConfigPollInterval is how often the agent reconciles its reverse-tunnel
// config with the control plane (live enable/disable + allowlist changes).
const tunnelConfigPollInterval = 30 * time.Second

// Version is injected at build time via -ldflags "-X main.Version=<version>".
// Falls back to "dev" for local builds.
var Version = "dev"

func main() {
	// Dispatch subcommands before flag parsing so "install" exits after setting up the service.
	if len(os.Args) > 1 && os.Args[1] == "install" {
		if err := installer.Run(os.Args[2:]); err != nil {
			fmt.Fprintf(os.Stderr, "error: %v\n", err)
			os.Exit(1)
		}
		return
	}

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

	hostname := cfg.Hostname
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

	// Reverse tunnel: run the supervisor in the background. It opens the
	// outbound tunnel when the control plane has enabled it for this agent (so
	// the control plane can reach allowlisted destinations in this network) and
	// reconciles live — enable/disable and allowlist changes take effect within
	// one poll interval, no restart. Always started (even when initially
	// disabled) so a later admin enable is picked up. A tunnel failure never
	// blocks job polling.
	go tunnelsupervisor.Run(
		ctx, c, cfg.ControlPlane, cfg.Secret, info.AgentID,
		client.TunnelConfig{Enabled: info.TunnelEnabled, Allowlist: info.TunnelAllowlist},
		tunnelConfigPollInterval,
	)

	if err := changip.CheckPendingRollback(func(params map[string]any) {
		result := executor.Dispatch("change_ip", params, true, params)
		log.Printf("Startup dead man's switch rollback: %s", result.Status)
	}); err != nil {
		log.Printf("Warning: dead man's switch check failed: %v", err)
	}

	switch cfg.Mode {
	case "ephemeral":
		if err := poller.RunEphemeral(ctx, c, info.AgentID, cfg.Secret); err != nil {
			log.Fatalf("Ephemeral run failed: %v", err)
		}
	case "service":
		poller.RunService(ctx, c, info.AgentID, cfg.Secret, cfg.PollInterval, cfg.MaxBackoff)
	}

	log.Println("Agent stopped.")
}
