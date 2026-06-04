package macos

// FilevaultStatusExecute returns FileVault encryption status.
func FilevaultStatusExecute(params map[string]any) (map[string]any, error) {
	return filevaultStatus(params)
}

// FilevaultEnableExecute enables FileVault disk encryption.
func FilevaultEnableExecute(params map[string]any) (map[string]any, error) {
	return filevaultEnable(params)
}

// GatekeeperStatusExecute returns Gatekeeper status.
func GatekeeperStatusExecute(params map[string]any) (map[string]any, error) {
	return gatekeeperStatus(params)
}

// GatekeeperEnableExecute enables Gatekeeper.
func GatekeeperEnableExecute(params map[string]any) (map[string]any, error) {
	return gatekeeperEnable(params)
}

// GatekeeperDisableExecute disables Gatekeeper.
func GatekeeperDisableExecute(params map[string]any) (map[string]any, error) {
	return gatekeeperDisable(params)
}

// SoftwareupdateListExecute lists available software updates.
func SoftwareupdateListExecute(params map[string]any) (map[string]any, error) {
	return softwareupdateList(params)
}

// SoftwareupdateInstallExecute installs software updates.
func SoftwareupdateInstallExecute(params map[string]any) (map[string]any, error) {
	return softwareupdateInstall(params)
}

// ProfilesListExecute lists installed configuration profiles.
func ProfilesListExecute(params map[string]any) (map[string]any, error) {
	return profilesList(params)
}

// LaunchctlListExecute lists running launchd services.
func LaunchctlListExecute(params map[string]any) (map[string]any, error) {
	return launchctlList(params)
}

// MacosSysinfoExecute returns macOS system information.
func MacosSysinfoExecute(params map[string]any) (map[string]any, error) {
	return macosSysinfo(params)
}

// FilevaultEnableRollback rolls back FileVault enable by disabling it.
func FilevaultEnableRollback(params map[string]any) (map[string]any, error) {
	return RollbackFilevaultEnable(params)
}

// GatekeeperEnableRollback rolls back Gatekeeper enable.
func GatekeeperEnableRollback(params map[string]any) (map[string]any, error) {
	return RollbackGatekeeperEnable(params)
}

// GatekeeperDisableRollback rolls back Gatekeeper disable.
func GatekeeperDisableRollback(params map[string]any) (map[string]any, error) {
	return RollbackGatekeeperDisable(params)
}

// DefaultsWriteExecute writes a macOS defaults preference value.
func DefaultsWriteExecute(params map[string]any) (map[string]any, error) {
	return defaultsWrite(params)
}

// DefaultsWriteRollback rolls back a defaults_write by restoring the previous value.
func DefaultsWriteRollback(params map[string]any) (map[string]any, error) {
	return RollbackDefaultsWrite(params)
}

// SantaCheckExecute audits Santa binary allowlisting status.
func SantaCheckExecute(params map[string]any) (map[string]any, error) {
	return santaCheck(params)
}

func ProfilesInstallExecute(params map[string]any) (map[string]any, error) {
	return profilesInstall(params)
}

func ProfilesRemoveExecute(params map[string]any) (map[string]any, error) {
	return profilesRemove(params)
}

func ProfilesInstallRollback(params map[string]any) (map[string]any, error) {
	return RollbackProfilesInstall(params)
}

func ProfilesRemoveRollback(params map[string]any) (map[string]any, error) {
	return RollbackProfilesRemove(params)
}

// HomebrewListExecute lists all installed Homebrew packages and versions.
func HomebrewListExecute(params map[string]any) (map[string]any, error) {
	return homebrewList(params)
}

func SantaRuleAddExecute(params map[string]any) (map[string]any, error) {
	return santaRuleAdd(params)
}

func SantaRuleRemoveExecute(params map[string]any) (map[string]any, error) {
	return santaRuleRemove(params)
}

func SantaRuleListExecute(params map[string]any) (map[string]any, error) {
	return santaRuleList(params)
}

func SantaRuleAddRollback(params map[string]any) (map[string]any, error) {
	return RollbackSantaRuleAdd(params)
}

func SantaRuleRemoveRollback(params map[string]any) (map[string]any, error) {
	return RollbackSantaRuleRemove(params)
}

func SantaModeSetExecute(params map[string]any) (map[string]any, error) {
	return santaModeSet(params)
}

func SantaSyncTriggerExecute(params map[string]any) (map[string]any, error) {
	return santaSyncTrigger(params)
}

func SantaEventExportExecute(params map[string]any) (map[string]any, error) {
	return santaEventExport(params)
}

func SantaBinaryCheckExecute(params map[string]any) (map[string]any, error) {
	return santaBinaryCheck(params)
}

func SantaModeSetRollback(params map[string]any) (map[string]any, error) {
	return RollbackSantaModeSet(params)
}

// SantaInstallExecute installs Santa binary allowlisting software.
func SantaInstallExecute(params map[string]any) (map[string]any, error) {
	return santaInstall(params)
}

// SantaInstallRollback uninstalls Santa.
func SantaInstallRollback(params map[string]any) (map[string]any, error) {
	return RollbackSantaInstall(params)
}
