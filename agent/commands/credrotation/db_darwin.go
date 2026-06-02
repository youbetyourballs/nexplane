//go:build darwin

package credrotation

import (
	"context"
	"fmt"
)

func updateDBUserPassword(_ context.Context, _ DBRotateParams) error {
	return fmt.Errorf("db credential rotation not supported on macOS")
}

func restartService(_ context.Context, _ string) error {
	return fmt.Errorf("restartService not supported on macOS")
}
