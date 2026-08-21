// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package drift

import (
	"fmt"
	"os"
)

func writeFile(params map[string]any) (map[string]any, error) {
	path, ok := params["path"].(string)
	if !ok || path == "" {
		return nil, fmt.Errorf("path is required")
	}
	content, _ := params["content"].(string)

	mode := os.FileMode(0644)
	if m, ok := params["mode"].(float64); ok {
		mode = os.FileMode(int(m))
	}

	if err := os.WriteFile(path, []byte(content), mode); err != nil {
		return nil, fmt.Errorf("writing %s: %w", path, err)
	}

	return map[string]any{
		"status": "success",
		"path":   path,
		"bytes":  len(content),
	}, nil
}
