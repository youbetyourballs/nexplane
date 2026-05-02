package executor

import (
	"fmt"

	"nexplane-agent/commands/changip"
	"nexplane-agent/commands/configsyslog"
	"nexplane-agent/commands/estimatesize"
	"nexplane-agent/commands/linuxauth"
	"nexplane-agent/commands/uploadimage"
	"nexplane-agent/commands/virtualize"
)

// Result is the outcome of a command execution.
type Result struct {
	Status string
	Data   map[string]any
	Error  string
}

// CommandFunc is the signature every command must implement.
type CommandFunc func(params map[string]any) (map[string]any, error)

var commands = map[string]CommandFunc{
	"estimate_image_size":      estimatesize.Execute,
	"change_ip":                changip.Execute,
	"configure_syslog":         configsyslog.Execute,
	"virtualize_for_migration": virtualize.Execute,
	"upload_image":             uploadimage.Execute,
	"configure_pam":                 linuxauth.ConfigurePAMExecute,
	"harden_ssh":                    linuxauth.HardenSSHExecute,
	"audit_users_and_groups":        linuxauth.AuditUsersAndGroupsExecute,
	"audit_privesc_vulnerabilities": linuxauth.AuditPrivescVulnerabilitiesExecute,
	"manage_ca_certificates":        linuxauth.ManageCACertificatesExecute,
	"configure_ntp":                 linuxauth.ConfigureNTPExecute,
}

var rollbacks = map[string]CommandFunc{
	"change_ip":                changip.Rollback,
	"configure_syslog":         configsyslog.Rollback,
	"virtualize_for_migration": virtualize.Rollback,
	"upload_image":             uploadimage.Rollback,
	"configure_pam":          linuxauth.ConfigurePAMRollback,
	"harden_ssh":             linuxauth.HardenSSHRollback,
	"manage_ca_certificates": linuxauth.ManageCACertificatesRollback,
	"configure_ntp":          linuxauth.ConfigureNTPRollback,
}

// Dispatch routes a command to its implementation.
// If rollback is true, previousResult is merged into params for context.
func Dispatch(command string, params map[string]any, rollback bool, previousResult map[string]any) Result {
	var fn CommandFunc
	var ok bool

	if rollback {
		fn, ok = rollbacks[command]
		if !ok {
			return Result{Status: "failed", Error: fmt.Sprintf("no rollback defined for command %q", command)}
		}
		merged := make(map[string]any, len(params)+len(previousResult))
		for k, v := range previousResult {
			merged[k] = v
		}
		for k, v := range params {
			merged[k] = v
		}
		params = merged
	} else {
		fn, ok = commands[command]
		if !ok {
			return Result{Status: "failed", Error: fmt.Sprintf("unknown command %q", command)}
		}
	}

	data, err := fn(params)
	if err != nil {
		return Result{Status: "failed", Data: data, Error: err.Error()}
	}
	return Result{Status: "completed", Data: data}
}
