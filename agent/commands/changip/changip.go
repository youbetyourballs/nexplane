package changip

func Execute(params map[string]any) (map[string]any, error) {
	return executeOS(params)
}

func Rollback(params map[string]any) (map[string]any, error) {
	return rollbackOS(params)
}
