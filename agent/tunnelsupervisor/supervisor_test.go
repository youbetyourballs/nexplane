package tunnelsupervisor

import (
	"context"
	"testing"
	"time"

	"nexplane-agent/client"
)

// fakeRunner records tunnel start/stop lifecycle over channels for deterministic
// assertions. It blocks until its context is cancelled, like tunnel.Run.
type fakeRunner struct {
	started chan []string
	stopped chan struct{}
}

func newFakeRunner() *fakeRunner {
	return &fakeRunner{started: make(chan []string, 8), stopped: make(chan struct{}, 8)}
}

func (f *fakeRunner) run(ctx context.Context, allowlist []string) {
	f.started <- allowlist
	<-ctx.Done()
	f.stopped <- struct{}{}
}

func recv[T any](t *testing.T, ch <-chan T, what string) T {
	t.Helper()
	select {
	case v := <-ch:
		return v
	case <-time.After(2 * time.Second):
		t.Fatalf("timed out waiting for %s", what)
		var zero T
		return zero
	}
}

func expectQuiet[T any](t *testing.T, ch <-chan T, what string) {
	t.Helper()
	select {
	case <-ch:
		t.Fatalf("unexpected %s", what)
	case <-time.After(150 * time.Millisecond):
	}
}

func TestSupervisorReconcileTransitions(t *testing.T) {
	f := newFakeRunner()
	s := &supervisor{parent: context.Background(), run: f.run}

	// disabled -> nothing runs
	s.apply(false, nil)
	expectQuiet(t, f.started, "start while disabled")

	// enable -> starts with the allowlist
	s.apply(true, []string{"10.0.0.0/8:5432"})
	al := recv(t, f.started, "start on enable")
	if len(al) != 1 || al[0] != "10.0.0.0/8:5432" {
		t.Fatalf("started with wrong allowlist: %v", al)
	}

	// same config -> no restart
	s.apply(true, []string{"10.0.0.0/8:5432"})
	expectQuiet(t, f.started, "restart on unchanged config")

	// allowlist change -> restart (old stops, new starts)
	s.apply(true, []string{"10.0.0.0/8:5432", "192.168.0.0/16:22"})
	recv(t, f.stopped, "stop on allowlist change")
	al2 := recv(t, f.started, "restart on allowlist change")
	if len(al2) != 2 {
		t.Fatalf("restarted with wrong allowlist: %v", al2)
	}

	// disable -> stops
	s.apply(false, nil)
	recv(t, f.stopped, "stop on disable")
	expectQuiet(t, f.started, "start on disable")

	// enabled but empty allowlist -> stays stopped (deny-by-default)
	s.apply(true, nil)
	expectQuiet(t, f.started, "start with empty allowlist")
}

// fakeFetcher feeds a sequence of configs to the polling loop.
type fakeFetcher struct {
	configs chan client.TunnelConfig
}

func (f *fakeFetcher) GetTunnelConfig(ctx context.Context, agentID string) (*client.TunnelConfig, error) {
	select {
	case cfg := <-f.configs:
		return &cfg, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func TestRunWithPollsAndReconciles(t *testing.T) {
	f := newFakeRunner()
	fetch := &fakeFetcher{configs: make(chan client.TunnelConfig, 8)}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Start disabled; the poll loop should later enable it.
	go runWith(ctx, fetch, "agent-1", client.TunnelConfig{Enabled: false}, 10*time.Millisecond, f.run)
	expectQuiet(t, f.started, "start before any enabling poll")

	// A poll returns enabled -> tunnel starts.
	fetch.configs <- client.TunnelConfig{Enabled: true, Allowlist: []string{"10.0.0.0/8:5432"}}
	recv(t, f.started, "start after enabling poll")

	// A later poll disables it -> tunnel stops.
	fetch.configs <- client.TunnelConfig{Enabled: false}
	recv(t, f.stopped, "stop after disabling poll")

	// Cancelling the context stops the loop cleanly.
	cancel()
}
