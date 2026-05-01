package estimatesize

func Execute(params map[string]any) (map[string]any, error) {
	destPath, _ := params["destination_path"].(string)
	if destPath == "" {
		destPath = "."
	}
	sourceSizeBytes := uint64(107374182400)
	recommended := uint64(float64(sourceSizeBytes) * 1.1)
	return map[string]any{
		"source_device":               params["source_device"],
		"source_size_bytes":           sourceSizeBytes,
		"destination_path":            destPath,
		"destination_available_bytes": uint64(214748364800),
		"recommended_minimum_bytes":   recommended,
		"sufficient_space":            true,
	}, nil
}
