package linuxharden

func SeccompLearnExecute(params map[string]any) (map[string]any, error) { return seccompLearnExecute(params) }
func SeccompLearnRollback(params map[string]any) (map[string]any, error) { return seccompLearnRollback(params) }

func IptablesLogBaselineExecute(params map[string]any) (map[string]any, error) { return iptablesLogBaselineExecute(params) }
func IptablesLogBaselineRollback(params map[string]any) (map[string]any, error) { return iptablesLogBaselineRollback(params) }
