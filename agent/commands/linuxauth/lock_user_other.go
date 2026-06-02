//go:build !linux

package linuxauth

import "fmt"

func LockLocalUserExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("lock_local_user requires Linux")
}

func LockLocalUserRollback(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("lock_local_user requires Linux")
}
