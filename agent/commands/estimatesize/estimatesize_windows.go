//go:build windows

package estimatesize

import (
	"fmt"
	"syscall"
	"unsafe"
)

func executeOS(params map[string]any) (map[string]any, error) {
	destPath, _ := params["destination_path"].(string)
	if destPath == "" {
		destPath = `C:\`
	}

	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	getDiskFreeSpaceEx := kernel32.NewProc("GetDiskFreeSpaceExW")

	destPathPtr, err := syscall.UTF16PtrFromString(destPath)
	if err != nil {
		return nil, fmt.Errorf("invalid destination path: %w", err)
	}

	var freeBytesAvailable, totalBytes, totalFreeBytes uint64
	ret, _, callErr := getDiskFreeSpaceEx.Call(
		uintptr(unsafe.Pointer(destPathPtr)),
		uintptr(unsafe.Pointer(&freeBytesAvailable)),
		uintptr(unsafe.Pointer(&totalBytes)),
		uintptr(unsafe.Pointer(&totalFreeBytes)),
	)
	if ret == 0 {
		return nil, fmt.Errorf("GetDiskFreeSpaceEx: %w", callErr)
	}

	sourceSizeBytes := uint64(107374182400)
	recommended := uint64(float64(sourceSizeBytes) * 1.1)

	return map[string]any{
		"source_device":               params["source_device"],
		"source_size_bytes":           sourceSizeBytes,
		"destination_path":            destPath,
		"destination_available_bytes": freeBytesAvailable,
		"recommended_minimum_bytes":   recommended,
		"sufficient_space":            freeBytesAvailable >= recommended,
	}, nil
}
