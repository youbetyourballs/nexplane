package uploadimage

func Execute(params map[string]any) (map[string]any, error) {
	return map[string]any{
		"action":            "upload_image",
		"destination_uri":   params["destination_uri"],
		"checksum_verified": true,
		"note":              "stub — implement in Task 8",
	}, nil
}

func Rollback(params map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": true, "note": "stub"}, nil
}
