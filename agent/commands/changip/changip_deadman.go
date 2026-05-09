package changip

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// PendingRollbackPath is the default on-disk location for the commit-timer state.
const PendingRollbackPath = "/var/lib/nexplane-agent/pending_rollback.json"

// deadManPathOverride overrides PendingRollbackPath when non-empty. Used in tests.
var deadManPathOverride string

func activePendingRollbackPath() string {
	if deadManPathOverride != "" {
		return deadManPathOverride
	}
	return PendingRollbackPath
}

// PendingRollback is written to disk before an IP change is applied.
// On agent restart, if this file exists, the agent resumes or executes rollback.
type PendingRollback struct {
	JobID             string         `json:"job_id"`
	ExpiresAt         time.Time      `json:"expires_at"`
	RollbackParams    map[string]any `json:"rollback_params"`
	ProbeURL          string         `json:"probe_url"`
	ProbeIntervalSecs int            `json:"probe_interval_seconds"`
	ProbeTimeoutSecs  int            `json:"probe_timeout_seconds"`
}

// StartDeadManSwitch writes the pending rollback file and starts a background
// goroutine that polls ProbeURL/health. The first HTTP 200 response cancels the
// timer and deletes the file. If ExpiresAt is reached without a successful probe,
// rollbackFn is called. Returns a cancelFn that stops the goroutine.
func StartDeadManSwitch(pr PendingRollback, rollbackFn func()) (cancelFn func(), err error) {
	return startDeadManSwitch(pr, PendingRollbackPath, rollbackFn)
}

// startDeadManSwitch is the internal version with an injectable rollbackPath for tests.
func startDeadManSwitch(pr PendingRollback, rollbackPath string, rollbackFn func()) (cancelFn func(), err error) {
	data, err := json.Marshal(pr)
	if err != nil {
		return nil, fmt.Errorf("marshal pending rollback: %w", err)
	}
	dir := filepath.Dir(rollbackPath)
	if !strings.HasSuffix(dir, string(filepath.Separator)) && dir != "." {
		if mkErr := os.MkdirAll(dir, 0755); mkErr != nil {
			// Best-effort directory creation.
			_ = mkErr
		}
	}
	if err := os.WriteFile(rollbackPath, data, 0644); err != nil {
		return nil, fmt.Errorf("write pending rollback: %w", err)
	}

	ctx, cancel := context.WithCancel(context.Background())

	go func() {
		interval := time.Duration(pr.ProbeIntervalSecs) * time.Second
		if interval == 0 {
			interval = 5 * time.Second
		}
		probeTimeout := time.Duration(pr.ProbeTimeoutSecs) * time.Second
		if probeTimeout == 0 {
			probeTimeout = 3 * time.Second
		}
		deadline := pr.ExpiresAt
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		expiry := time.NewTimer(time.Until(deadline))
		defer expiry.Stop()

		probeURL := strings.TrimRight(pr.ProbeURL, "/") + "/health"
		client := &http.Client{Timeout: probeTimeout}

		probe := func() bool {
			resp, err := client.Get(probeURL)
			if err != nil {
				return false
			}
			defer resp.Body.Close()
			return resp.StatusCode == http.StatusOK
		}

		// Probe immediately on start too.
		if probe() {
			os.Remove(rollbackPath)
			cancel()
			return
		}

		for {
			select {
			case <-ctx.Done():
				return
			case <-expiry.C:
				rollbackFn()
				os.Remove(rollbackPath)
				return
			case <-ticker.C:
				if probe() {
					os.Remove(rollbackPath)
					cancel()
					return
				}
			}
		}
	}()

	return func() {
		cancel()
		os.Remove(rollbackPath)
	}, nil
}

// CheckPendingRollback checks for an existing pending_rollback.json on agent startup.
// If the file exists and ExpiresAt is in the past, rollbackFn is called immediately.
// If ExpiresAt is in the future, the commit-timer goroutine is resumed.
// Returns nil if no file exists.
func CheckPendingRollback(rollbackFn func()) error {
	return checkPendingRollback(PendingRollbackPath, rollbackFn)
}

// checkPendingRollback is the internal version with an injectable path for tests.
func checkPendingRollback(rollbackPath string, rollbackFn func()) error {
	data, err := os.ReadFile(rollbackPath)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("read pending rollback: %w", err)
	}

	var pr PendingRollback
	if err := json.Unmarshal(data, &pr); err != nil {
		return fmt.Errorf("parse pending rollback: %w", err)
	}

	if time.Now().After(pr.ExpiresAt) {
		// Already expired — roll back immediately.
		rollbackFn()
		os.Remove(rollbackPath)
		return nil
	}

	// Not yet expired — resume the timer with the remaining window.
	_, err = startDeadManSwitch(pr, rollbackPath, rollbackFn)
	return err
}
