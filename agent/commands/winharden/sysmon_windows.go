//go:build windows

package winharden

import (
	"fmt"
	"os"
	"strings"
)

const sysmonDefaultConfig = `<Sysmon schemaversion="4.82">
  <HashAlgorithms>md5,sha256</HashAlgorithms>
  <EventFiltering>
    <RuleGroup name="" groupRelation="or">
      <ProcessCreate onmatch="exclude"/>
      <FileCreateTime onmatch="exclude"/>
      <NetworkConnect onmatch="exclude">
        <DestinationPort condition="is">443</DestinationPort>
        <DestinationPort condition="is">80</DestinationPort>
      </NetworkConnect>
      <ProcessTerminate onmatch="exclude"/>
      <DriverLoad onmatch="exclude"/>
      <CreateRemoteThread onmatch="exclude"/>
      <RawAccessRead onmatch="exclude"/>
      <FileCreate onmatch="exclude"/>
      <FileCreateStreamHash onmatch="exclude"/>
      <FileDelete onmatch="exclude"/>
    </RuleGroup>
  </EventFiltering>
</Sysmon>`

func sysmonDeployExecuteOS(params map[string]any) (map[string]any, error) {
	sysmonPath, _ := params["sysmon_path"].(string)
	if sysmonPath == "" {
		sysmonPath = `C:\Windows\Sysmon64.exe`
	}
	configContent, _ := params["config_xml"].(string)
	if configContent == "" {
		configContent = sysmonDefaultConfig
	}
	configPath := `C:\Windows\sysmon-nexplane.xml`
	if err := os.WriteFile(configPath, []byte(configContent), 0644); err != nil {
		return nil, fmt.Errorf("write sysmon config: %w", err)
	}
	// Check if already installed
	_, err := runPS(`Get-Service -Name Sysmon64 -ErrorAction SilentlyContinue`)
	if err != nil {
		// Install
		if out, err2 := runPS(fmt.Sprintf(`& "%s" -accepteula -i "%s"`, sysmonPath, configPath)); err2 != nil {
			return nil, fmt.Errorf("sysmon install: %s: %w", string(out), err2)
		}
	} else {
		// Already installed — update config
		if out, err2 := runPS(fmt.Sprintf(`& "%s" -c "%s"`, sysmonPath, configPath)); err2 != nil {
			return nil, fmt.Errorf("sysmon config update: %s: %w", string(out), err2)
		}
	}
	return map[string]any{
		"action":      "sysmon_deploy",
		"config_path": configPath,
		"sysmon_path": sysmonPath,
	}, nil
}

func sysmonFIMExecuteOS(params map[string]any) (map[string]any, error) {
	dirs, _ := params["directories"].([]any)
	var dirList []string
	for _, d := range dirs {
		if s, ok := d.(string); ok {
			dirList = append(dirList, s)
		}
	}
	if len(dirList) == 0 {
		dirList = []string{`C:\Windows\System32`, `C:\Program Files`}
	}

	// Collect FileCreate/FileDelete events for specified dirs
	filterParts := make([]string, 0, len(dirList))
	for _, d := range dirList {
		filterParts = append(filterParts,
			fmt.Sprintf(`[EventData[Data[@Name='TargetFilename'] and contains(Data,'%s')]]`, d))
	}
	xpath := fmt.Sprintf(
		`*[System[EventID=11 or EventID=23] and (%s)]`,
		strings.Join(filterParts, " or "),
	)
	out, _ := runPS(fmt.Sprintf(
		`Get-WinEvent -LogName "Microsoft-Windows-Sysmon/Operational" -FilterXPath '%s' -MaxEvents 500 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Message`,
		xpath,
	))
	events := parseWDACLines(string(out))
	return map[string]any{
		"action":      "sysmon_fim",
		"directories": dirList,
		"events":      events,
		"event_count": len(events),
	}, nil
}

func sysmonRollbackOS(_ map[string]any) (map[string]any, error) {
	runPS(`Get-Service -Name Sysmon64 -ErrorAction SilentlyContinue | Stop-Service -Force`)
	runPS(`C:\Windows\Sysmon64.exe -u force`)
	return map[string]any{"action": "sysmon_rollback", "status": "uninstalled"}, nil
}
