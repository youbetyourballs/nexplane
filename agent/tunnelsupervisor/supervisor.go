// Package tunnelsupervisor reconciles the agent's running reverse tunnel against
// the control plane's desired config, polled live. Enabling/disabling the tunnel
// or changing the allowlist takes effect within one poll interval — no restart.
//
// An allowlist change restarts the tunnel (new WebSocket connection), which makes
// the relay reload its own copy of the allowlist too, keeping both ends in sync.
package tunnelsupervisor

import (
	"context"
	"log"
	"time"

	"nexplane-agent/client"
	"nexplane-agent/tunnel"
)

// runFunc runs a tunnel with the given allowlist until ctx is cancelled.
// Injectable so the reconcile logic is testable without a real relay.
type runFunc func(ctx context.Context, allowlist []string)

// configFetcher is the slice of *client.Client the supervisor needs (also lets
// tests inject a fake config source).
type configFetcher interface {
	GetTunnelConfig(ctx context.Context, agentID string) (*client.TunnelConfig, error)
}

// supervisor holds the currently-running tunnel state and reconciles it.
type supervisor struct {
	parent    context.Context
	run       runFunc
	allowlist []string
	cancel    context.CancelFunc // non-nil iff a tunnel is currently running
}

func desiredRunning(enabled bool, allowlist []string) bool {
	// Enabling with an empty allowlist would bridge nothing (deny-by-default),
	// so treat it as "not running" rather than opening a useless connection.
	return enabled && len(allowlist) > 0
}

func sameAllowlist(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// apply reconciles the running tunnel toward (enabled, allowlist).
func (s *supervisor) apply(enabled bool, allowlist []string) {
	want := desiredRunning(enabled, allowlist)
	running := s.cancel != nil
	if running == want && (!want || sameAllowlist(s.allowlist, allowlist)) {
		return // no change
	}
	if s.cancel != nil { // stop the old tunnel (disable or allowlist change)
		s.cancel()
		s.cancel = nil
		s.allowlist = nil
	}
	if want {
		ctx, cancel := context.WithCancel(s.parent)
		s.cancel = cancel
		s.allowlist = append([]string(nil), allowlist...)
		go s.run(ctx, s.allowlist)
		log.Printf("[tunnel] reconcile: running with %d allowlist rule(s)", len(allowlist))
	} else {
		log.Printf("[tunnel] reconcile: stopped")
	}
}

func (s *supervisor) stop() {
	if s.cancel != nil {
		s.cancel()
		s.cancel = nil
	}
}

// Run starts the supervisor: it applies the initial config immediately, then
// polls the control plane every interval to reconcile live. Blocks until ctx is
// cancelled. Always run it (even when initially disabled) so a later admin
// enable is picked up without an agent restart.
//
// The client is used both as the config poller and as the TokenFetcher so the
// tunnel fetches a short-lived per-connection token before each WS dial.
func Run(ctx context.Context, c *client.Client, controlPlane, secret, agentID string, initial client.TunnelConfig, interval time.Duration) {
	run := func(rctx context.Context, allowlist []string) {
		if err := tunnel.Run(rctx, controlPlane, secret, agentID, allowlist, c); err != nil && rctx.Err() == nil {
			log.Printf("[tunnel] stopped: %v", err)
		}
	}
	runWith(ctx, c, agentID, initial, interval, run)
}

// runWith is the testable core: same as Run but with an injectable config source
// and tunnel runner.
func runWith(ctx context.Context, c configFetcher, agentID string, initial client.TunnelConfig, interval time.Duration, run runFunc) {
	s := &supervisor{parent: ctx, run: run}
	s.apply(initial.Enabled, initial.Allowlist)

	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			s.stop()
			return
		case <-ticker.C:
			cfg, err := c.GetTunnelConfig(ctx, agentID)
			if err != nil {
				log.Printf("[tunnel] config poll error: %v", err)
				continue
			}
			s.apply(cfg.Enabled, cfg.Allowlist)
		}
	}
}
