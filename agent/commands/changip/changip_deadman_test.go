package changip

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestDeadManSwitch_CancelsOnSuccessfulProbe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	dir := t.TempDir()
	rollbackPath := filepath.Join(dir, "pending_rollback.json")

	pr := PendingRollback{
		JobID:             "job-cancel-test",
		ExpiresAt:         time.Now().Add(30 * time.Second),
		RollbackParams:    map[string]any{"interface": "eth0"},
		ProbeURL:          srv.URL,
		ProbeIntervalSecs: 1,
		ProbeTimeoutSecs:  2,
	}

	rollbackCalled := make(chan struct{})
	cancelFn, err := startDeadManSwitch(pr, rollbackPath, func() {
		close(rollbackCalled)
	})
	if err != nil {
		t.Fatalf("startDeadManSwitch: %v", err)
	}

	// Wait for goroutine to probe and cancel.
	select {
	case <-rollbackCalled:
		t.Error("rollback should NOT have been called — probe succeeds")
	case <-time.After(5 * time.Second):
		// Good — rollback was not triggered.
	}
	cancelFn()

	// Pending file should be deleted after successful probe.
	if _, err := os.Stat(rollbackPath); !os.IsNotExist(err) {
		t.Error("pending_rollback.json should be deleted after successful probe")
	}
}

func TestDeadManSwitch_RollbackOnTimeout(t *testing.T) {
	dir := t.TempDir()
	rollbackPath := filepath.Join(dir, "pending_rollback.json")

	pr := PendingRollback{
		JobID:             "job-timeout-test",
		ExpiresAt:         time.Now().Add(1 * time.Second), // 1 second timer
		RollbackParams:    map[string]any{"interface": "eth0"},
		ProbeURL:          "http://127.0.0.1:1", // unreachable
		ProbeIntervalSecs: 1,
		ProbeTimeoutSecs:  1,
	}

	rollbackCalled := make(chan struct{}, 1)
	_, err := startDeadManSwitch(pr, rollbackPath, func() {
		rollbackCalled <- struct{}{}
	})
	if err != nil {
		t.Fatalf("startDeadManSwitch: %v", err)
	}

	select {
	case <-rollbackCalled:
		// Good — rollback triggered.
	case <-time.After(5 * time.Second):
		t.Error("expected rollback to be called within 5s, but it was not")
	}
}

func TestCheckPendingRollback_Expired(t *testing.T) {
	dir := t.TempDir()
	rollbackPath := filepath.Join(dir, "pending_rollback.json")

	pr := PendingRollback{
		JobID:          "job-expired",
		ExpiresAt:      time.Now().Add(-10 * time.Second), // already expired
		RollbackParams: map[string]any{"interface": "eth0"},
		ProbeURL:       "http://127.0.0.1:1",
	}
	data, _ := json.Marshal(pr)
	os.WriteFile(rollbackPath, data, 0644)

	rollbackCalled := make(chan struct{}, 1)
	err := checkPendingRollback(rollbackPath, func(params map[string]any) {
		rollbackCalled <- struct{}{}
	})
	if err != nil {
		t.Fatalf("checkPendingRollback: %v", err)
	}

	select {
	case <-rollbackCalled:
		// Good.
	case <-time.After(1 * time.Second):
		t.Error("rollback should have been called immediately for expired file")
	}
}

func TestCheckPendingRollback_NotExpired(t *testing.T) {
	dir := t.TempDir()
	rollbackPath := filepath.Join(dir, "pending_rollback.json")

	pr := PendingRollback{
		JobID:             "job-not-expired",
		ExpiresAt:         time.Now().Add(30 * time.Second),
		RollbackParams:    map[string]any{"interface": "eth0"},
		ProbeURL:          "http://127.0.0.1:1", // unreachable so timer fires if not cancelled
		ProbeIntervalSecs: 5,
		ProbeTimeoutSecs:  1,
	}
	data, _ := json.Marshal(pr)
	os.WriteFile(rollbackPath, data, 0644)

	rollbackCalled := make(chan struct{}, 1)
	err := checkPendingRollback(rollbackPath, func(params map[string]any) {
		rollbackCalled <- struct{}{}
	})
	if err != nil {
		t.Fatalf("checkPendingRollback: %v", err)
	}

	// Rollback should NOT be called immediately.
	select {
	case <-rollbackCalled:
		t.Error("rollback should not fire immediately for a non-expired file")
	case <-time.After(500 * time.Millisecond):
		// Good — timer is still pending.
	}
}
