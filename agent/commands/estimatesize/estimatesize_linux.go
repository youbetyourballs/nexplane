//go:build linux

package estimatesize

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
)

func executeOS(params map[string]any) (map[string]any, error) {
	destPath, _ := params["destination_path"].(string)
	if destPath == "" {
		destPath = "."
	}

	var stat syscall.Statfs_t
	if err := syscall.Statfs(destPath, &stat); err != nil {
		return nil, fmt.Errorf("statfs %s: %w", destPath, err)
	}
	availBytes := stat.Bavail * uint64(stat.Bsize)

	sourceSizeBytes := uint64(107374182400)
	if dev, ok := params["source_device"].(string); ok && dev != "" {
		if sz := blockDeviceSize(dev); sz > 0 {
			sourceSizeBytes = sz
		}
	}

	recommended := uint64(float64(sourceSizeBytes) * 1.1)

	return map[string]any{
		"source_device":               params["source_device"],
		"source_size_bytes":           sourceSizeBytes,
		"destination_path":            destPath,
		"destination_available_bytes": availBytes,
		"recommended_minimum_bytes":   recommended,
		"sufficient_space":            availBytes >= recommended,
	}, nil
}

func blockDeviceSize(dev string) uint64 {
	sysfsPath := fmt.Sprintf("/sys/block/%s/size", filepath.Base(dev))
	data, err := os.ReadFile(sysfsPath)
	if err != nil {
		return 0
	}
	var sectors uint64
	fmt.Sscanf(strings.TrimSpace(string(data)), "%d", &sectors)
	return sectors * 512
}
