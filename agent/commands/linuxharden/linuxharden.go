package linuxharden

func SeccompLearnExecute(params map[string]any) (map[string]any, error) { return seccompLearnExecute(params) }
func SeccompLearnRollback(params map[string]any) (map[string]any, error) { return seccompLearnRollback(params) }

func IptablesLogBaselineExecute(params map[string]any) (map[string]any, error) { return iptablesLogBaselineExecute(params) }
func IptablesLogBaselineRollback(params map[string]any) (map[string]any, error) { return iptablesLogBaselineRollback(params) }

func TrivyScanExecute(params map[string]any) (map[string]any, error)    { return trivyScanExecute(params) }
func TrivyScanRollback(params map[string]any) (map[string]any, error)   { return trivyScanRollback(params) }
func LynisAuditExecute(params map[string]any) (map[string]any, error)   { return lynisAuditExecute(params) }
func LynisAuditRollback(params map[string]any) (map[string]any, error)  { return lynisAuditRollback(params) }
func OpenscapScanExecute(params map[string]any) (map[string]any, error) { return openscapScanExecute(params) }
func OpenscapScanRollback(params map[string]any) (map[string]any, error) { return openscapScanRollback(params) }
func AuthorizedKeysAuditExecute(p map[string]any) (map[string]any, error) { return authorizedKeysAuditExecute(p) }
func AuthorizedKeysAuditRollback(p map[string]any) (map[string]any, error) { return authorizedKeysAuditRollback(p) }
func SudoersAuditExecute(p map[string]any) (map[string]any, error)  { return sudoersAuditExecute(p) }
func SudoersAuditRollback(p map[string]any) (map[string]any, error) { return sudoersAuditRollback(p) }
func SuidScanExecute(p map[string]any) (map[string]any, error)   { return suidScanExecute(p) }
func SuidScanRollback(p map[string]any) (map[string]any, error)  { return suidScanRollback(p) }
func SslCertInspectExecute(p map[string]any) (map[string]any, error)  { return sslCertInspectExecute(p) }
func SslCertInspectRollback(p map[string]any) (map[string]any, error) { return sslCertInspectRollback(p) }
