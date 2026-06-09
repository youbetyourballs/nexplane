package configsyslog

const nexplaneBegin = "# nexplane-managed-begin"
const nexplaneEnd = "# nexplane-managed-end"

func toInt(v any) int {
	switch n := v.(type) {
	case int:
		return n
	case float64:
		return int(n)
	case int64:
		return int(n)
	}
	return 514
}

func Execute(params map[string]any) (map[string]any, error) {
	return executeOS(params)
}

func Rollback(params map[string]any) (map[string]any, error) {
	return rollbackOS(params)
}
