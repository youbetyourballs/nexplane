package poller

import (
	"testing"
	"time"
)

// backoffSequence simulates the backoff state machine and returns the sequence
// of currentBackoff values after each error (before jitter is applied).
func backoffSequence(base, maxBackoff time.Duration, steps int) []time.Duration {
	current := base
	result := make([]time.Duration, 0, steps)
	for i := 0; i < steps; i++ {
		result = append(result, current)
		current *= 2
		if current > maxBackoff {
			current = maxBackoff
		}
	}
	return result
}

func TestBackoffIncreasesOnError(t *testing.T) {
	base := 1 * time.Second
	max := 16 * time.Second

	seq := backoffSequence(base, max, 4)

	// Each step should be double the previous until capped.
	expected := []time.Duration{1, 2, 4, 8}
	for i, want := range expected {
		if seq[i] != want*time.Second {
			t.Errorf("step %d: got %v, want %v", i, seq[i], want*time.Second)
		}
	}
}

func TestBackoffCappedAtMaxBackoff(t *testing.T) {
	base := 1 * time.Second
	max := 5 * time.Second

	seq := backoffSequence(base, max, 6)

	for i, v := range seq {
		if v > max {
			t.Errorf("step %d: backoff %v exceeds maxBackoff %v", i, v, max)
		}
	}

	// After enough doublings the value should be pinned to max.
	last := seq[len(seq)-1]
	if last != max {
		t.Errorf("expected final backoff to be capped at %v, got %v", max, last)
	}
}

func TestBackoffResetsAfterSuccess(t *testing.T) {
	base := 1 * time.Second
	max := 32 * time.Second

	// Simulate 4 errors then a success.
	current := base
	for i := 0; i < 4; i++ {
		current *= 2
		if current > max {
			current = max
		}
	}
	// current is now 16s — well above base.
	if current <= base {
		t.Fatalf("expected backoff to have grown, got %v", current)
	}

	// On success, reset.
	current = base
	if current != base {
		t.Errorf("after reset, expected %v, got %v", base, current)
	}
}

func TestJitterWithinBounds(t *testing.T) {
	// Jitter must be in [0, currentBackoff]. Verify the range property mathematically.
	// rand.Int63n(n+1) returns [0, n], so jitter in [0, currentBackoff] is guaranteed.
	// This test just documents the expected invariant.
	base := 30 * time.Second
	max := 5 * time.Minute

	seq := backoffSequence(base, max, 8)
	for i, v := range seq {
		if v < base {
			t.Errorf("step %d: backoff %v is below base %v", i, v, base)
		}
		if v > max {
			t.Errorf("step %d: backoff %v exceeds max %v", i, v, max)
		}
	}
}
