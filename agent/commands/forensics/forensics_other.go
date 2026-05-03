//go:build !linux && !windows

package forensics

import (
	"bytes"
	"context"
	"fmt"
	"runtime"
)

func collectOS(_ context.Context, _ ForensicsConfig) ([]ArtifactEntry, bytes.Buffer, error) {
	return nil, bytes.Buffer{}, fmt.Errorf("collect_forensics not supported on %s", runtime.GOOS)
}
