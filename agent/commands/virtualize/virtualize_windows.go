//go:build windows

package virtualize

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func executeOS(params map[string]any) (map[string]any, error) {
	imagePath, _ := params["image_path"].(string)
	targetMode, _ := params["target_mode"].(string)

	if imagePath == "" {
		return nil, fmt.Errorf("image_path is required")
	}
	if targetMode == "" {
		return nil, fmt.Errorf("target_mode is required")
	}

	sizeOut, err := exec.Command("powershell", "-Command",
		"(Get-Disk -Number 0).Size").Output()
	if err != nil {
		return nil, fmt.Errorf("getting disk size: %w", err)
	}
	diskSizeStr := strings.TrimSpace(string(sizeOut))

	psCreate := fmt.Sprintf("New-VHD -Path '%s' -SizeBytes %s -Fixed", imagePath, diskSizeStr)
	if err := runPS(psCreate); err != nil {
		return nil, fmt.Errorf("creating VHD: %w", err)
	}

	psMount := fmt.Sprintf(`Mount-DiskImage -ImagePath '%s' -PassThru | Get-DiskImage | Get-Disk | Get-Partition | Get-Volume | Select-Object -ExpandProperty DriveLetter`, imagePath)
	driveOut, err := exec.Command("powershell", "-Command", psMount).Output()
	if err != nil {
		return nil, fmt.Errorf("mounting VHD: %w", err)
	}
	driveLetter := strings.TrimSpace(string(driveOut))

	robocopyArgs := []string{`C:\`, driveLetter + `:\`, "/E", "/MIR", "/XD",
		"$RECYCLE.BIN", "System Volume Information", "/NFL", "/NDL"}
	exec.Command("robocopy", robocopyArgs...).Run()

	if err := editNetworkConfigW(driveLetter+`:\`, targetMode, params); err != nil {
		exec.Command("powershell", "-Command",
			fmt.Sprintf("Dismount-DiskImage -ImagePath '%s'", imagePath)).Run()
		return nil, fmt.Errorf("editing network config: %w", err)
	}

	runPS(fmt.Sprintf("Dismount-DiskImage -ImagePath '%s'", imagePath))

	info, _ := os.Stat(imagePath)
	imageSize := int64(0)
	if info != nil {
		imageSize = info.Size()
	}

	return map[string]any{
		"action":                 "virtualize_for_migration",
		"image_path":             imagePath,
		"image_size_bytes":       imageSize,
		"network_config_applied": true,
		"completed_at":           time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	imagePath, _ := params["image_path"].(string)
	if imagePath == "" {
		return nil, fmt.Errorf("image_path is required for rollback")
	}
	if err := os.Remove(imagePath); err != nil && !os.IsNotExist(err) {
		return nil, fmt.Errorf("deleting VHD: %w", err)
	}
	return map[string]any{"rolled_back": true, "deleted": true, "image_path": imagePath}, nil
}

func editNetworkConfigW(mountRoot, mode string, params map[string]any) error {
	iface, _ := params["target_interface"].(string)
	if iface == "" {
		iface = "Ethernet"
	}

	unattendPath := filepath.Join(mountRoot, "Windows", "Panther", "unattend.xml")
	os.MkdirAll(filepath.Dir(unattendPath), 0755)

	var ipConfig string
	if mode == "dhcp" {
		ipConfig = `<component name="Microsoft-Windows-TCPIP">
      <Interfaces><Interface><Identifier>Local Area Connection</Identifier>
        <Ipv4Settings><DhcpEnabled>true</DhcpEnabled></Ipv4Settings>
      </Interface></Interfaces></component>`
	} else {
		v4, _ := params["target_ip_v4"].(string)
		gw, _ := params["target_gateway_v4"].(string)
		parts := strings.SplitN(v4, "/", 2)
		prefix := "24"
		if len(parts) > 1 {
			prefix = parts[1]
		}
		ipConfig = fmt.Sprintf(`<component name="Microsoft-Windows-TCPIP">
      <Interfaces><Interface><Identifier>%s</Identifier>
        <Ipv4Settings><DhcpEnabled>false</DhcpEnabled></Ipv4Settings>
        <UnicastIpAddresses><IpAddress wcm:action="add" wcm:keyValue="1">%s/%s</IpAddress></UnicastIpAddresses>
        <Routes><Route wcm:action="add"><Identifier>0</Identifier><Metric>256</Metric>
          <NextHopAddress>%s</NextHopAddress><Prefix>0.0.0.0/0</Prefix></Route></Routes>
      </Interface></Interfaces></component>`, iface, parts[0], prefix, gw)
	}

	unattend := fmt.Sprintf(`<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend"
          xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
  <settings pass="specialize">%s</settings>
</unattend>`, ipConfig)

	return os.WriteFile(unattendPath, []byte(unattend), 0644)
}

func runPS(command string) error {
	out, err := exec.Command("powershell", "-Command", command).CombinedOutput()
	if err != nil {
		return fmt.Errorf("powershell: %w (output: %s)", err, out)
	}
	return nil
}
