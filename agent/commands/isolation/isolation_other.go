//go:build !linux && !darwin && !windows

package isolation

import (
	"context"
	"fmt"
	"runtime"
)

func isolateOS(_ context.Context, _ IsolationConfig) (*PreIsolationState, error) {
	return nil, fmt.Errorf("isolate_host not supported on %s", runtime.GOOS)
}

func restoreOS(_ context.Context, _ *PreIsolationState) error {
	return fmt.Errorf("restore_network_access not supported on %s", runtime.GOOS)
}
